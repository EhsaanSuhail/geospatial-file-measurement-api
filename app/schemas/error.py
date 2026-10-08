from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str = Field(examples=["UNSUPPORTED_FILE_FORMAT"])
    message: str = Field(
        examples=["Only KML files and ZIP archives containing a Shapefile are supported."]
    )
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
