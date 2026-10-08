"""Checks uploads and stages them in a temporary folder before GDAL reads them.

The extension decides the format and the content must match it. Files from a ZIP are never
written using their paths inside the archive: only the Shapefile parts are copied, under new
names, so a path like "../../x" cannot escape the folder. Such archives are rejected anyway.
"""

import lzma
import re
import stat
import uuid
import zipfile
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO
from xml.parsers import expat

import numpy as np

from app.core.enums import FileFormat
from app.core.exceptions import (
    FileTooLargeError,
    InvalidShapefileArchiveError,
    MalformedFileError,
    UnsupportedFileFormatError,
)

_EXTENSION_FORMATS = {".kml": FileFormat.KML, ".zip": FileFormat.SHAPEFILE}
_UNSUPPORTED_FORMAT_MESSAGE = (
    "Only KML files (.kml) and ZIP archives containing a Shapefile (.zip) are supported."
)
_REQUIRED_COMPONENTS = (".shp", ".shx", ".dbf")
_OPTIONAL_COMPONENTS = (".prj", ".cpg")  # CRS and attribute encoding
_SHAPEFILE_MAGIC = (9994).to_bytes(4, "big")  # file code at offset 0 of .shp and .shx
_SHX_HEADER_BYTES = 100  # .shp and .shx share the same 100-byte header
_SHX_RECORD_BYTES = 8  # one (offset, length) pair per shape
_DBF_HEADER_BYTES = 32  # fixed part of the dBase header, before the field descriptors
_MAX_FILENAME_LENGTH = 255
_MB = 1024 * 1024
_CHUNK_BYTES = _MB
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class UploadLimits:
    max_upload_bytes: int
    max_uncompressed_bytes: int
    max_archive_members: int


@dataclass(frozen=True)
class StagedUpload:
    filename: str  # client-supplied name, reduced to a safe display basename
    file_format: FileFormat
    size_bytes: int
    path: Path  # what GDAL opens: the .kml file, or the extracted .shp
    source_name: str  # original stem; replaces layer names GDAL derives from `path`


def stage_upload(
    stream: IO[bytes], filename: str | None, workdir: Path, limits: UploadLimits
) -> StagedUpload:
    """Copy the upload into `workdir` and verify it is an acceptable KML or Shapefile ZIP."""
    display_name = sanitize_filename(filename)
    extension = PurePosixPath(display_name).suffix.lower()
    file_format = _EXTENSION_FORMATS.get(extension)
    if file_format is None:
        raise UnsupportedFileFormatError(_UNSUPPORTED_FORMAT_MESSAGE)

    raw_path = workdir / f"upload-{uuid.uuid4().hex}{extension}"
    size = _copy_limited(
        stream,
        raw_path,
        limits.max_upload_bytes,
        f"The file exceeds the upload limit of {_format_size(limits.max_upload_bytes)}.",
    )
    if size == 0:
        raise MalformedFileError("The uploaded file is empty.")

    if file_format is FileFormat.KML:
        _verify_kml(raw_path)
        return StagedUpload(
            display_name, file_format, size, raw_path, PurePosixPath(display_name).stem
        )

    shp_path, source_name = _extract_shapefile(raw_path, workdir / "shapefile", limits)
    return StagedUpload(display_name, file_format, size, shp_path, source_name)


def sanitize_filename(filename: str | None) -> str:
    # Browsers send a basename, but nothing stops a client from sending a full path.
    basename = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    printable = "".join(char for char in basename if char.isprintable()).strip()
    return printable[:_MAX_FILENAME_LENGTH]


def _copy_limited(source: IO[bytes], destination: Path, limit: int, message: str) -> int:
    written = 0
    with destination.open("wb") as target:
        while chunk := source.read(_CHUNK_BYTES):
            written += len(chunk)
            if written > limit:
                raise FileTooLargeError(message)
            target.write(chunk)
    return written


# --- KML -----------------------------------------------------------------------------------


class _RootElementFound(Exception):  # noqa: N818 - control-flow signal, not an error
    """Stops the streaming parse once the root element has been seen."""


def _verify_kml(path: Path) -> None:
    """Check that the root element is <kml>, reading only up to that element.

    A DOCTYPE is rejected: KML never needs one, and without it there are no XML entity attacks
    (external entities, "billion laughs") for GDAL's parser to run into later.
    """
    root_names: list[str] = []

    def reject_doctype(*_: object) -> None:
        raise MalformedFileError("KML documents containing a DOCTYPE declaration are not accepted.")

    def record_root(name: str, _attributes: object) -> None:
        root_names.append(name)
        raise _RootElementFound

    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartDoctypeDeclHandler = reject_doctype
    parser.StartElementHandler = record_root
    try:
        with path.open("rb") as document:
            while chunk := document.read(64 * 1024):
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
    except _RootElementFound:
        pass
    except expat.ExpatError as exc:
        raise MalformedFileError("The file is not a well-formed XML (KML) document.") from exc

    # With a namespace separator, expat reports "{namespace} {local-name}".
    if not root_names or root_names[0].rsplit(" ", 1)[-1] != "kml":
        raise MalformedFileError("The file is not a KML document: its root element is not <kml>.")


# --- Shapefile ZIP -------------------------------------------------------------------------


def _extract_shapefile(
    archive_path: Path, destination: Path, limits: UploadLimits
) -> tuple[Path, str]:
    try:
        archive = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        raise MalformedFileError("The file is not a valid ZIP archive.") from exc

    with archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        _check_members(members, limits)
        source_name, components = _locate_shapefile(members)
        if sum(info.file_size for info in components.values()) > limits.max_uncompressed_bytes:
            raise FileTooLargeError(_uncompressed_limit_message(limits))

        destination.mkdir()
        staged_stem = uuid.uuid4().hex
        remaining = limits.max_uncompressed_bytes
        try:
            for extension, info in components.items():
                with archive.open(info) as member:
                    remaining -= _copy_limited(
                        member,
                        destination / f"{staged_stem}{extension}",
                        remaining,
                        _uncompressed_limit_message(limits),
                    )
        except (
            zipfile.BadZipFile,
            zlib.error,
            lzma.LZMAError,
            NotImplementedError,
            EOFError,
        ) as exc:
            # CRC mismatch, corrupt or truncated data, or an unsupported compression method.
            raise MalformedFileError("The ZIP archive is corrupted or unreadable.") from exc

    shp_path = destination / f"{staged_stem}.shp"
    _verify_components(shp_path)
    return shp_path, source_name


def _verify_components(shp_path: Path) -> None:
    """Check the parts of a Shapefile that GDAL would accept without complaining.

    With a broken .dbf GDAL returns the features without any attributes, and for a truncated
    .shp it returns empty geometries for the missing records. Neither raises an error.
    """
    for extension in (".shp", ".shx"):
        with shp_path.with_suffix(extension).open("rb") as component:
            if component.read(4) != _SHAPEFILE_MAGIC:
                raise MalformedFileError(
                    f"The {extension} file is not a valid Shapefile component."
                )

    shx_bytes = shp_path.with_suffix(".shx").read_bytes()
    if (
        len(shx_bytes) < _SHX_HEADER_BYTES
        or (len(shx_bytes) - _SHX_HEADER_BYTES) % _SHX_RECORD_BYTES
    ):
        raise MalformedFileError("The .shx index file is truncated or corrupt.")
    # Each .shx entry is (offset, length) in 16-bit words, big-endian; every record must end
    # inside the .shp.
    index = np.frombuffer(shx_bytes, dtype=">i4", offset=_SHX_HEADER_BYTES).reshape(-1, 2)
    shape_count = len(index)
    record_ends = (index[:, 0].astype(np.int64) + 4 + index[:, 1]) * 2  # 8-byte record header
    if shape_count and (
        index[:, 0].min() * 2 < _SHX_HEADER_BYTES or record_ends.max() > shp_path.stat().st_size
    ):
        raise MalformedFileError("The .shp file is truncated or does not match its .shx index.")

    dbf_path = shp_path.with_suffix(".dbf")
    with dbf_path.open("rb") as dbf:
        header = dbf.read(_DBF_HEADER_BYTES)
    if len(header) < _DBF_HEADER_BYTES:
        raise MalformedFileError("The .dbf attribute table is truncated or corrupt.")
    record_count = int.from_bytes(header[4:8], "little")
    header_length = int.from_bytes(header[8:10], "little")
    record_length = int.from_bytes(header[10:12], "little")
    if (
        header_length <= _DBF_HEADER_BYTES
        or record_length < 1
        or dbf_path.stat().st_size < header_length + record_count * record_length
    ):
        raise MalformedFileError("The .dbf attribute table is truncated or corrupt.")
    if record_count != shape_count:
        raise MalformedFileError(
            f"The .dbf attribute table has {record_count} records but the Shapefile "
            f"has {shape_count} shapes."
        )


def _check_members(members: list[zipfile.ZipInfo], limits: UploadLimits) -> None:
    if len(members) > limits.max_archive_members:
        raise InvalidShapefileArchiveError(
            f"The archive contains {len(members)} files; at most "
            f"{limits.max_archive_members} are allowed."
        )
    for info in members:
        if _is_unsafe_path(info.filename):
            raise InvalidShapefileArchiveError(
                "The archive contains files with unsafe paths (absolute or containing '..')."
            )
        if stat.S_ISLNK(info.external_attr >> 16):
            raise InvalidShapefileArchiveError("The archive contains symbolic links.")
        if info.flag_bits & 0x1:
            raise InvalidShapefileArchiveError(
                "Encrypted (password-protected) archives are not supported."
            )


def _locate_shapefile(members: Iterable[zipfile.ZipInfo]) -> tuple[str, dict[str, zipfile.ZipInfo]]:
    """Find exactly one .shp and its sibling components (same folder, same stem, any case)."""
    candidates = [info for info in members if not _is_os_metadata(info.filename)]
    shp_members = [info for info in candidates if _member_path(info).suffix.lower() == ".shp"]
    if not shp_members:
        raise InvalidShapefileArchiveError("The archive does not contain a Shapefile (.shp).")
    if len(shp_members) > 1:
        names = ", ".join(sorted(_member_path(info).name for info in shp_members)[:5])
        raise InvalidShapefileArchiveError(
            f"The archive contains {len(shp_members)} Shapefiles ({names}); "
            "upload exactly one Shapefile per archive."
        )

    shp_path = _member_path(shp_members[0])
    components: dict[str, zipfile.ZipInfo] = {}
    for info in candidates:
        path = _member_path(info)
        extension = path.suffix.lower()
        if (
            path.parent == shp_path.parent
            and path.stem.lower() == shp_path.stem.lower()
            and extension in _REQUIRED_COMPONENTS + _OPTIONAL_COMPONENTS
        ):
            if extension in components:
                raise InvalidShapefileArchiveError(
                    f"The archive contains more than one {extension} file for the Shapefile."
                )
            components[extension] = info

    missing = [extension for extension in _REQUIRED_COMPONENTS if extension not in components]
    if missing:
        raise InvalidShapefileArchiveError(
            f"The Shapefile is missing required component(s): {', '.join(missing)}."
        )
    return shp_path.stem, components


def _member_path(info: zipfile.ZipInfo) -> PurePosixPath:
    # The ZIP format mandates '/', but some Windows tools write '\'.
    return PurePosixPath(info.filename.replace("\\", "/"))


def _is_unsafe_path(name: str) -> bool:
    normalized = name.replace("\\", "/")
    return (
        normalized.startswith("/")
        or _DRIVE_LETTER.match(normalized) is not None
        or ".." in normalized.split("/")
        or "\x00" in normalized
    )


def _is_os_metadata(name: str) -> bool:
    """macOS archives add `__MACOSX/._name.shp` resource forks next to the real files."""
    parts = name.replace("\\", "/").split("/")
    return "__MACOSX" in parts[:-1] or parts[-1].startswith("._")


def _uncompressed_limit_message(limits: UploadLimits) -> str:
    return (
        "The Shapefile components exceed the uncompressed size limit of "
        f"{_format_size(limits.max_uncompressed_bytes)}."
    )


def _format_size(size_bytes: int) -> str:
    if size_bytes >= _MB and size_bytes % _MB == 0:
        return f"{size_bytes // _MB} MB"
    return f"{size_bytes} bytes"
