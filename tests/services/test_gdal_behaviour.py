"""Tests that pin down GDAL behaviours the validation code and the README rely on.

They call GDAL (pyogrio) directly, without the API's own checks. If a GDAL upgrade changes one
of these behaviours, the matching check or documented limitation should be looked at again.
"""

import http.server
import io
import threading
import warnings
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pyogrio
import pytest
from pyogrio import raw
from shapely.geometry import Polygon

from tests.factories import kml_document, placemark, shapefile_zip

SQUARE = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])


def extract(archive: bytes, directory: Path) -> Path:
    zipfile.ZipFile(io.BytesIO(archive)).extractall(directory)  # trusted, test-made archive
    return directory / "parcels.shp"


def read_all_layers(path: Path) -> list[tuple[str, list[object]]]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = []
        for name, _ in pyogrio.list_layers(path):
            meta, _, _, fields = raw.read(path, layer=name)
            names = fields[list(meta["fields"]).index("Name")] if "Name" in meta["fields"] else []
            result.append((str(name), list(names)))
        return result


def test_gdal_silently_drops_all_attributes_of_a_corrupt_dbf(tmp_path: Path) -> None:
    archive = shapefile_zip(
        [SQUARE, SQUARE], exclude=[".dbf"], extra_entries={"parcels.dbf": b"\x03x"}
    )

    meta, _, geometries, fields = raw.read(extract(archive, tmp_path))

    assert len(geometries) == 2  # the features are returned...
    assert list(meta["fields"]) == []  # ...without any attribute, and without an error
    assert fields == []


def test_gdal_silently_returns_null_geometries_for_a_truncated_shp(tmp_path: Path) -> None:
    shp = zipfile.ZipFile(io.BytesIO(shapefile_zip([SQUARE, SQUARE]))).read("parcels.shp")
    archive = shapefile_zip(
        [SQUARE, SQUARE], exclude=[".shp"], extra_entries={"parcels.shp": shp[:120]}
    )

    _, _, geometries, _ = raw.read(extract(archive, tmp_path))

    assert list(geometries) == [None, None]


def test_libkml_keeps_only_the_last_of_several_root_placemarks(tmp_path: Path) -> None:
    # The KML specification allows a single root feature; extra ones need a <Document>.
    path = tmp_path / "roots.kml"
    path.write_bytes(
        b'<kml xmlns="http://www.opengis.net/kml/2.2">'
        b"<Placemark><name>first</name><Point><coordinates>1,2</coordinates></Point></Placemark>"
        b"<Placemark><name>last</name><Point><coordinates>3,4</coordinates></Point></Placemark>"
        b"</kml>"
    )

    assert read_all_layers(path) == [("roots", ["last"])]


@pytest.fixture
def http_requests() -> Iterator[tuple[str, list[str]]]:
    """A local HTTP server recording every request it receives."""
    received: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", received
    finally:
        server.shutdown()
        server.server_close()


def test_libkml_does_not_fetch_urls_referenced_by_a_kml(
    tmp_path: Path, http_requests: tuple[str, list[str]]
) -> None:
    # A server-side reader that followed these links would be an SSRF vector.
    url, received = http_requests
    path = tmp_path / "links.kml"
    path.write_bytes(
        kml_document(
            f'<Style id="s"><IconStyle><Icon><href>{url}/icon.png</href></Icon></IconStyle></Style>'
            f"<NetworkLink><Link><href>{url}/network-link.kml</href></Link></NetworkLink>"
            f"<GroundOverlay><Icon><href>{url}/overlay.png</href></Icon><LatLonBox><north>1"
            "</north><south>0</south><east>1</east><west>0</west></LatLonBox></GroundOverlay>"
            + placemark(
                "styled",
                f"<styleUrl>{url}/styles.kml#s</styleUrl><Point>"
                "<coordinates>3,4</coordinates></Point>",
            )
        )
    )

    read_all_layers(path)

    assert received == []
