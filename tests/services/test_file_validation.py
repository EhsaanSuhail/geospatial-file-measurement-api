import io
import re
import zipfile
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon

from app.core.enums import FileFormat
from app.core.exceptions import (
    FileTooLargeError,
    InvalidShapefileArchiveError,
    MalformedFileError,
    UnsupportedFileFormatError,
)
from app.services.file_validation import StagedUpload, UploadLimits, sanitize_filename, stage_upload
from tests.factories import build_zip, kml_document, placemark, shapefile_zip

LIMITS = UploadLimits(
    max_upload_bytes=1024 * 1024, max_uncompressed_bytes=4 * 1024 * 1024, max_archive_members=20
)
SQUARE = Polygon([(77.59, 12.97), (77.60, 12.97), (77.60, 12.98), (77.59, 12.98)])
KML = kml_document(placemark("marker", "<Point><coordinates>77.59,12.97</coordinates></Point>"))


def stage(
    data: bytes, filename: str | None, workdir: Path, limits: UploadLimits = LIMITS
) -> StagedUpload:
    return stage_upload(io.BytesIO(data), filename, workdir, limits)


def zip_entries(archive: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(archive)) as opened:
        return {name: opened.read(name) for name in opened.namelist()}


# --- format detection -----------------------------------------------------------------------


@pytest.mark.parametrize("filename", ["notes.txt", "site.geojson", "site.kmz", "README", "", None])
def test_unsupported_extensions_are_rejected(tmp_path: Path, filename: str | None) -> None:
    with pytest.raises(UnsupportedFileFormatError):
        stage(KML, filename, tmp_path)


def test_extension_check_is_case_insensitive(tmp_path: Path) -> None:
    assert stage(KML, "SITE.KML", tmp_path).file_format is FileFormat.KML


def test_empty_upload_is_malformed(tmp_path: Path) -> None:
    with pytest.raises(MalformedFileError, match="empty"):
        stage(b"", "site.kml", tmp_path)


def test_upload_over_size_limit_is_rejected(tmp_path: Path) -> None:
    limits = UploadLimits(max_upload_bytes=100, max_uncompressed_bytes=100, max_archive_members=5)

    with pytest.raises(FileTooLargeError, match="upload limit of 100 bytes"):
        stage(b"<kml>" + b" " * 200 + b"</kml>", "site.kml", tmp_path, limits)


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("site.kml", "site.kml"),
        ("C:\\Users\\surveyor\\site.kml", "site.kml"),
        ("../../etc/site.kml", "site.kml"),
        ("site\x00\n.kml", "site.kml"),
        ("a" * 300 + ".kml", "a" * 255),
    ],
)
def test_filenames_are_reduced_to_safe_basenames(raw_name: str, expected: str) -> None:
    assert sanitize_filename(raw_name) == expected


# --- KML ------------------------------------------------------------------------------------


def test_valid_kml_is_staged_under_a_generated_name(tmp_path: Path) -> None:
    staged = stage(KML, "survey.kml", tmp_path)

    assert staged.filename == "survey.kml"
    assert staged.file_format is FileFormat.KML
    assert staged.size_bytes == len(KML)
    assert staged.source_name == "survey"
    assert staged.path.read_bytes() == KML
    assert staged.path.parent == tmp_path
    assert "survey" not in staged.path.name


@pytest.mark.parametrize(
    "document",
    [
        kml_document("", namespace="http://earth.google.com/kml/2.1"),
        b"<kml><Placemark/></kml>",
        b"\xef\xbb\xbf<?xml version='1.0'?><kml xmlns='http://www.opengis.net/kml/2.2'/>",
        '<?xml version="1.0" encoding="UTF-16"?><kml/>'.encode("utf-16"),
    ],
    ids=["kml-2.1-namespace", "no-namespace", "utf-8-bom", "utf-16"],
)
def test_kml_variants_are_accepted(tmp_path: Path, document: bytes) -> None:
    assert stage(document, "site.kml", tmp_path).file_format is FileFormat.KML


@pytest.mark.parametrize(
    "document",
    [
        b"definitely not xml",
        b"PK\x03\x04 a zip archive renamed to .kml",
        b'<?xml version="1.0"?><gpx version="1.1"/>',
        b'<?xml version="1.0"?>',
    ],
    ids=["not-xml", "zip-bytes", "gpx-root", "no-root-element"],
)
def test_non_kml_content_with_kml_extension_is_malformed(tmp_path: Path, document: bytes) -> None:
    with pytest.raises(MalformedFileError):
        stage(document, "site.kml", tmp_path)


def test_kml_with_doctype_is_rejected(tmp_path: Path) -> None:
    billion_laughs = (
        b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><kml>&b;</kml>'
    )

    with pytest.raises(MalformedFileError, match="DOCTYPE"):
        stage(billion_laughs, "site.kml", tmp_path)


# --- Shapefile ZIP --------------------------------------------------------------------------


def test_valid_shapefile_zip_is_extracted(tmp_path: Path) -> None:
    archive = shapefile_zip([SQUARE], stem="parcels")

    staged = stage(archive, "parcels.zip", tmp_path)

    assert staged.file_format is FileFormat.SHAPEFILE
    assert staged.size_bytes == len(archive)
    assert staged.source_name == "parcels"
    assert staged.path.suffix == ".shp"
    assert sorted(path.suffix for path in staged.path.parent.iterdir()) == [
        ".cpg",
        ".dbf",
        ".prj",
        ".shp",
        ".shx",
    ]


def test_shapefile_in_subfolder_with_uppercase_extensions_is_found(tmp_path: Path) -> None:
    entries = {
        f"export/data/Parcels{Path(name).suffix.upper()}": data
        for name, data in zip_entries(shapefile_zip([SQUARE], stem="Parcels")).items()
    }

    staged = stage(build_zip(entries), "export.zip", tmp_path)

    assert staged.source_name == "Parcels"


def test_macos_resource_forks_are_ignored(tmp_path: Path) -> None:
    archive = shapefile_zip(
        [SQUARE],
        extra_entries={"__MACOSX/._parcels.shp": b"\x00\x05\x16\x07", "._parcels.shp": b"junk"},
    )

    assert stage(archive, "parcels.zip", tmp_path).source_name == "parcels"


def test_unrelated_files_in_archive_are_not_extracted(tmp_path: Path) -> None:
    archive = shapefile_zip([SQUARE], extra_entries={"README.txt": b"notes", "parcels.qix": b""})

    staged = stage(archive, "parcels.zip", tmp_path)

    extracted = {path.suffix for path in staged.path.parent.iterdir()}
    assert extracted == {".shp", ".shx", ".dbf", ".prj", ".cpg"}


@pytest.mark.parametrize(
    "data",
    [b"not a zip at all", b"PK\x03\x04truncated", shapefile_zip([SQUARE])[:-30]],
    ids=["random-bytes", "header-only", "truncated-archive"],
)
def test_malformed_zip_is_rejected(tmp_path: Path, data: bytes) -> None:
    with pytest.raises(MalformedFileError, match="not a valid ZIP archive"):
        stage(data, "parcels.zip", tmp_path)


@pytest.mark.parametrize(
    "archive", [build_zip({}), build_zip({"notes.txt": b"hello"})], ids=["empty", "no-shp"]
)
def test_zip_without_shapefile_is_rejected(tmp_path: Path, archive: bytes) -> None:
    with pytest.raises(InvalidShapefileArchiveError, match="does not contain a Shapefile"):
        stage(archive, "parcels.zip", tmp_path)


@pytest.mark.parametrize("missing", [".shx", ".dbf"])
def test_zip_missing_required_component_is_rejected(tmp_path: Path, missing: str) -> None:
    archive = shapefile_zip([SQUARE], exclude=[missing])

    with pytest.raises(InvalidShapefileArchiveError, match=re.escape(f"component(s): {missing}")):
        stage(archive, "parcels.zip", tmp_path)


def test_zip_with_two_shapefiles_is_rejected(tmp_path: Path) -> None:
    entries = zip_entries(shapefile_zip([SQUARE], stem="a"))
    entries |= zip_entries(shapefile_zip([Point(1, 1)], stem="b"))

    with pytest.raises(
        InvalidShapefileArchiveError, match=re.escape("2 Shapefiles (a.shp, b.shp)")
    ):
        stage(build_zip(entries), "two.zip", tmp_path)


def test_zip_with_duplicate_components_is_rejected(tmp_path: Path) -> None:
    dbf = zip_entries(shapefile_zip([SQUARE]))["parcels.dbf"]
    archive = shapefile_zip([SQUARE], extra_entries={"PARCELS.DBF": dbf})  # same stem, any case

    with pytest.raises(InvalidShapefileArchiveError, match=r"more than one \.dbf file"):
        stage(archive, "parcels.zip", tmp_path)


@pytest.mark.parametrize(
    "unsafe_name",
    ["../../evil.shp", "/etc/evil.shp", "C:/Windows/evil.shp", "data\\..\\..\\evil.dbf"],
)
def test_archive_with_unsafe_paths_is_rejected(tmp_path: Path, unsafe_name: str) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    archive = shapefile_zip([SQUARE], extra_entries={unsafe_name: b"payload"})

    with pytest.raises(InvalidShapefileArchiveError, match="unsafe paths"):
        stage(archive, "parcels.zip", workdir)
    assert not list(tmp_path.glob("evil.*"))


def test_archive_with_symlink_is_rejected(tmp_path: Path) -> None:
    buffer = io.BytesIO(shapefile_zip([SQUARE]))
    with zipfile.ZipFile(buffer, "a") as archive:
        link = zipfile.ZipInfo("parcels.prj.link")
        link.external_attr = 0o120777 << 16  # S_IFLNK file type in the high bytes
        archive.writestr(link, "/etc/passwd")

    with pytest.raises(InvalidShapefileArchiveError, match="symbolic links"):
        stage(buffer.getvalue(), "parcels.zip", tmp_path)


def _set_encryption_flag(archive: bytes, member: str) -> bytes:
    """zipfile clears flag bits when writing, so patch the member's central-directory record."""
    data = bytearray(archive)
    position = 0
    while (position := data.find(b"PK\x01\x02", position)) != -1:
        name_length = int.from_bytes(data[position + 28 : position + 30], "little")
        if data[position + 46 : position + 46 + name_length] == member.encode():
            data[position + 8] |= 0x01  # general-purpose flag bit 0: encrypted
            return bytes(data)
        position += 4
    raise AssertionError(f"{member} not found in archive")


def test_encrypted_archive_is_rejected(tmp_path: Path) -> None:
    archive = _set_encryption_flag(shapefile_zip([SQUARE]), "parcels.dbf")
    assert zipfile.ZipFile(io.BytesIO(archive)).getinfo("parcels.dbf").flag_bits & 0x1

    with pytest.raises(InvalidShapefileArchiveError, match="Encrypted"):
        stage(archive, "parcels.zip", tmp_path)


def test_archive_with_too_many_members_is_rejected(tmp_path: Path) -> None:
    archive = shapefile_zip([SQUARE], extra_entries={f"extra/{i}.txt": b"" for i in range(30)})

    with pytest.raises(InvalidShapefileArchiveError, match="at most 20"):
        stage(archive, "parcels.zip", tmp_path)


def test_highly_compressed_archive_over_uncompressed_limit_is_rejected(tmp_path: Path) -> None:
    limits = UploadLimits(
        max_upload_bytes=1024 * 1024, max_uncompressed_bytes=64 * 1024, max_archive_members=20
    )
    padded_dbf = b"\x03" + b"\x00" * (1024 * 1024)  # deflates to about 1 KB
    archive = shapefile_zip([SQUARE], exclude=[".dbf"], extra_entries={"parcels.dbf": padded_dbf})
    assert len(archive) < limits.max_upload_bytes

    with pytest.raises(FileTooLargeError, match="uncompressed size limit"):
        stage(archive, "parcels.zip", tmp_path, limits)


def test_corrupted_member_data_is_malformed(tmp_path: Path) -> None:
    archive = bytearray(shapefile_zip([SQUARE]))
    # First entry is parcels.cpg: 30-byte local header + name, then its deflate stream.
    payload = archive.index(b"PK\x03\x04") + 30 + len("parcels.cpg")
    archive[payload : payload + 4] = b"\xff\xff\xff\xff"

    with pytest.raises(MalformedFileError, match="corrupted"):
        stage(bytes(archive), "parcels.zip", tmp_path)


def _component(archive: bytes, name: str) -> bytes:
    return zip_entries(archive)[name]


def _replace_component(name: str, data: bytes) -> bytes:
    two_squares = [SQUARE, SQUARE]
    return shapefile_zip(two_squares, exclude=[Path(name).suffix], extra_entries={name: data})


@pytest.mark.parametrize(
    "dbf",
    [
        b"\x03garbage",
        b"",
        _component(shapefile_zip([SQUARE, SQUARE]), "parcels.dbf")[:40],
    ],
    ids=["garbage", "empty", "truncated"],
)
def test_corrupt_attribute_table_is_rejected(tmp_path: Path, dbf: bytes) -> None:
    # GDAL would silently return the features without any attributes.
    with pytest.raises(MalformedFileError, match=r"\.dbf attribute table is truncated or corrupt"):
        stage(_replace_component("parcels.dbf", dbf), "parcels.zip", tmp_path)


def test_attribute_table_with_wrong_record_count_is_rejected(tmp_path: Path) -> None:
    single_record_dbf = _component(shapefile_zip([SQUARE]), "parcels.dbf")

    with pytest.raises(MalformedFileError, match="has 1 records but the Shapefile has 2 shapes"):
        stage(_replace_component("parcels.dbf", single_record_dbf), "parcels.zip", tmp_path)


def test_truncated_shp_is_rejected(tmp_path: Path) -> None:
    # GDAL would silently return null geometries for the missing records.
    shp = _component(shapefile_zip([SQUARE, SQUARE]), "parcels.shp")

    with pytest.raises(MalformedFileError, match=r"\.shp file is truncated"):
        stage(_replace_component("parcels.shp", shp[:120]), "parcels.zip", tmp_path)


def test_truncated_shx_is_rejected(tmp_path: Path) -> None:
    shx = _component(shapefile_zip([SQUARE, SQUARE]), "parcels.shx")

    with pytest.raises(MalformedFileError, match=r"\.shx index file is truncated"):
        stage(_replace_component("parcels.shx", shx[:-3]), "parcels.zip", tmp_path)


def test_shp_component_with_wrong_magic_number_is_malformed(tmp_path: Path) -> None:
    archive = shapefile_zip([SQUARE], exclude=[".shp"], extra_entries={"parcels.shp": b"\0" * 100})

    with pytest.raises(MalformedFileError, match=r"\.shp file is not a valid Shapefile"):
        stage(archive, "parcels.zip", tmp_path)
