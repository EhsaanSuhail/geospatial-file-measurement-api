"""Plain ASGI middleware, so uploads are streamed through instead of buffered."""

import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import error_response
from app.core.logging import request_id_var

logger = logging.getLogger("app.access")

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    """Assign a request ID (or accept a well-formed X-Request-ID), echo it, and log the request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_PATTERN.match(incoming) else uuid.uuid4().hex
        # Each ASGI request runs in its own task, so this value cannot leak between requests.
        request_id_var.set(request_id)
        status_code = 500
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                if "x-request-id" not in headers:
                    headers.append("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "%s %s %s",
                scope["method"],
                scope["path"],
                status_code,
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )


class TrailingSlashMiddleware:
    """Route `/path/` exactly like `/path`, without a redirect.

    The assignment brief writes endpoints with trailing slashes (`/api/files/`); a 307 redirect
    would silently break clients that do not follow redirects for multipart POSTs (e.g. curl).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope.get("path", "")
        if scope["type"] == "http" and len(path) > 1 and path.endswith("/"):
            scope = dict(scope, path=path.rstrip("/") or "/")
            if raw_path := scope.get("raw_path"):
                scope["raw_path"] = raw_path.rstrip(b"/") or b"/"
        await self.app(scope, receive, send)


class BodySizeLimitMiddleware:
    """Reject request bodies above `max_body_bytes` before they are spooled to disk.

    Declared sizes (Content-Length) are rejected up front; chunked bodies are counted as they
    stream in. The per-file limit is enforced again, exactly, when the upload is staged.
    """

    def __init__(self, app: ASGIApp, max_body_bytes: int, message: str) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.message = message

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_body_bytes:
            response = error_response(413, "FILE_TOO_LARGE", self.message)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    # FastAPI re-raises HTTPExceptions raised while reading the body, so this
                    # reaches the regular exception handlers and becomes a 413 response.
                    raise HTTPException(status_code=413, detail=self.message)
            return message

        await self.app(scope, limited_receive, send)
