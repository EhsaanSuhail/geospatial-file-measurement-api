"""Helpers that build test inputs: Shapefiles, ZIP archives and KML documents."""

import io
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import shapely
from pyogrio import raw
from shapely.geometry.base import BaseGeometry

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def write_shapefile(
    directory: Path,
    stem: str,
    geometries: Sequence[BaseGeometry | None],
    *,
    crs: str | None = "EPSG:4326",
    fields: Mapping[str, Sequence[object]] | None = None,
) -> Path:
    """Write a Shapefile (.shp/.shx/.dbf/.cpg, plus .prj when `crs` is given)."""
    if fields is None:
        fields = {"name": [f"feature-{index}" for index in range(len(geometries))]}
    path = directory / f"{stem}.shp"
    raw.write(
        path,
        geometry=np.array(
            [None if geometry is None else shapely.to_wkb(geometry) for geometry in geometries],
            dtype=object,
        ),
        field_data=[np.asarray(values) for values in fields.values()],
        fields=list(fields),
        geometry_type=_shapefile_geometry_type(geometries),
        crs=crs,
        driver="ESRI Shapefile",
        encoding="UTF-8",
    )
    return path


def shapefile_zip(
    geometries: Sequence[BaseGeometry | None],
    *,
    stem: str = "parcels",
    crs: str | None = "EPSG:4326",
    fields: Mapping[str, Sequence[object]] | None = None,
    folder: str = "",
    exclude: Sequence[str] = (),
    extra_entries: Mapping[str, bytes] | None = None,
) -> bytes:
    """A ZIP holding one Shapefile; `exclude` drops components such as ".shx"."""
    with tempfile.TemporaryDirectory() as directory:
        write_shapefile(Path(directory), stem, geometries, crs=crs, fields=fields)
        entries = {
            f"{folder}{path.name}": path.read_bytes()
            for path in sorted(Path(directory).iterdir())
            if path.suffix not in exclude
        }
    entries.update(extra_entries or {})
    return build_zip(entries)


def build_zip(entries: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def kml_document(body: str, *, namespace: str = "http://www.opengis.net/kml/2.2") -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="{namespace}">'
        f"<Document>{body}</Document></kml>"
    ).encode()


def placemark(name: str, geometry: str) -> str:
    return f"<Placemark><name>{name}</name>{geometry}</Placemark>"


def _shapefile_geometry_type(geometries: Sequence[BaseGeometry | None]) -> str:
    types = {geometry.geom_type for geometry in geometries if geometry is not None}
    for base in ("Polygon", "LineString", "Point"):
        if types <= {base, f"Multi{base}"} and types:
            return f"Multi{base}" if f"Multi{base}" in types else base
    raise ValueError(f"A Shapefile holds a single geometry family, got {sorted(types)}")
