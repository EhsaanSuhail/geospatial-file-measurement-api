"""Read the features of a KML file or Shapefile with GDAL (through pyogrio).

Geometries are parsed one feature at a time, so one broken geometry (for example an unclosed
KML ring) is recorded on that feature instead of failing the whole file, which is what
`geopandas.read_file` would do.
"""

import logging
import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pyogrio
import shapely
from pyogrio import raw
from pyogrio.errors import DataLayerError, DataSourceError
from pyproj import CRS
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from app.core.enums import FileFormat
from app.core.exceptions import FileTooLargeError, MalformedFileError
from app.services.file_validation import StagedUpload

logger = logging.getLogger(__name__)

# LIBKML adds drawing settings (tessellate, visibility, ...) as fields. They describe how a
# placemark is displayed, not the feature itself, so they are left out of its properties.
_KML_PRESENTATION_FIELDS = frozenset(
    {"altitudeMode", "tessellate", "extrude", "visibility", "drawOrder", "icon"}
)
_WKB_GEOMETRY_TYPES = {
    1: "Point",
    2: "LineString",
    3: "Polygon",
    4: "MultiPoint",
    5: "MultiLineString",
    6: "MultiPolygon",
    7: "GeometryCollection",
}


@dataclass(frozen=True)
class ExtractedFeature:
    index: int  # position in the file, across all layers
    layer_name: str
    geometry_type: str | None
    geometry: BaseGeometry | None  # None when absent, or unusable (see geometry_error)
    properties: dict[str, Any]
    geometry_error: str | None = None

    def geometry_as_geojson(self) -> dict[str, Any] | None:
        """GeoJSON geometry object, with coordinates in the file's source CRS."""
        return None if self.geometry is None else mapping(self.geometry)


@dataclass(frozen=True)
class ParsedDataset:
    crs: CRS | None
    features: list[ExtractedFeature]


def read_features(staged: StagedUpload, max_features: int) -> ParsedDataset:
    """Extract every feature of every layer (each KML folder is a separate GDAL layer)."""
    try:
        layer_names = [str(name) for name, _ in pyogrio.list_layers(staged.path)]
    except DataSourceError as exc:
        logger.info("GDAL could not open the dataset: %s", exc)
        raise _unreadable(staged.file_format) from exc

    features: list[ExtractedFeature] = []
    layer_crs: list[CRS | None] = []
    for layer in layer_names:
        try:
            # Read one feature past the remaining budget, to detect an oversized file early.
            meta, _, wkb_values, field_values = raw.read(
                staged.path, layer=layer, max_features=max_features - len(features) + 1
            )
        except (DataSourceError, DataLayerError) as exc:
            logger.info("GDAL could not read layer %r: %s", layer, exc)
            raise _unreadable(staged.file_format) from exc

        if len(features) + len(wkb_values) > max_features:
            raise FileTooLargeError(
                f"The file contains more than {max_features} features, the processing limit."
            )
        # GDAL names some layers after the file it opened; restore the client's name.
        layer_name = staged.source_name if layer == staged.path.stem else layer
        # GDAL reports the CRS as "AUTHORITY:CODE" or WKT, which PROJ can always parse.
        layer_crs.append(CRS.from_user_input(meta["crs"]) if meta["crs"] else None)
        field_names = [str(name) for name in meta["fields"]]
        for position, wkb in enumerate(wkb_values):
            properties = {
                name: _to_json_value(values[position])
                for name, values in zip(field_names, field_values, strict=True)
            }
            if staged.file_format is FileFormat.KML:
                properties = _kml_attributes(properties)
            features.append(_extract_feature(len(features), layer_name, wkb, properties))

    return ParsedDataset(crs=_dataset_crs(layer_crs), features=features)


def _extract_feature(
    index: int, layer_name: str, wkb: bytes | None, properties: dict[str, Any]
) -> ExtractedFeature:
    def unusable(geometry_type: str | None, error: str) -> ExtractedFeature:
        return ExtractedFeature(
            index=index,
            layer_name=layer_name,
            geometry_type=geometry_type,
            geometry=None,
            properties=properties,
            geometry_error=error,
        )

    if wkb is None:
        return ExtractedFeature(index, layer_name, None, None, properties)
    try:
        geometry = shapely.from_wkb(wkb)
    except shapely.errors.GEOSException as exc:
        reason = str(exc).split(": ", 1)[-1]  # drop the GEOS exception class name
        return unusable(_wkb_geometry_type(wkb), f"The geometry could not be parsed: {reason}.")
    coordinates = shapely.get_coordinates(geometry, include_z=geometry.has_z)
    if not np.isfinite(coordinates).all():
        return unusable(
            geometry.geom_type, "The geometry contains non-finite coordinates (NaN or infinity)."
        )
    return ExtractedFeature(index, layer_name, geometry.geom_type, geometry, properties)


def _dataset_crs(layer_crs: list[CRS | None]) -> CRS | None:
    distinct = set(layer_crs)
    if len(distinct) > 1:
        raise MalformedFileError("The file's layers use different coordinate reference systems.")
    return distinct.pop() if distinct else None


def _kml_attributes(properties: dict[str, Any]) -> dict[str, Any]:
    # KML placemarks have no fixed schema; LIBKML fills fields a placemark lacks with nulls.
    return {
        name: value
        for name, value in properties.items()
        if name not in _KML_PRESENTATION_FIELDS and value is not None
    }


def _to_json_value(value: Any) -> Any:
    """Convert a field value read by pyogrio (NumPy scalars, datetime64, str, None) to a
    JSON-compatible value. Anything unexpected falls back to its string form."""
    if value is None:
        return None
    if isinstance(value, np.datetime64):
        return None if np.isnat(value) else str(np.datetime_as_string(value, unit="auto"))
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return value if math.isfinite(value) else None  # JSON has no NaN or Infinity
    if isinstance(value, bool | int | str):
        return value
    return str(value)


def _wkb_geometry_type(wkb: bytes) -> str | None:
    """Geometry type from a WKB header, for geometries GEOS refuses to construct."""
    if len(wkb) < 5:
        return None
    byte_order: Literal["little", "big"] = "little" if wkb[0] == 1 else "big"
    code = int.from_bytes(wkb[1:5], byte_order) & 0x0FFFFFFF  # drop EWKB Z/M/SRID flags
    return _WKB_GEOMETRY_TYPES.get(code % 1000)  # ISO WKB adds 1000/2000/3000 for Z/M/ZM


def _unreadable(file_format: FileFormat) -> MalformedFileError:
    label = "KML file" if file_format is FileFormat.KML else "Shapefile"
    return MalformedFileError(f"The {label} could not be read.")
