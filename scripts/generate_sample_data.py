"""Regenerate the zipped Shapefiles in sample_data/ (run from the repository root).

    python scripts/generate_sample_data.py

The features are fixed, so the output is the same on every run (except for the "last update"
date GDAL writes into each .dbf header).
"""

import io
import tempfile
import warnings
import zipfile
from pathlib import Path

import numpy as np
import shapely
from pyogrio import raw
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "sample_data"


def plot(easting: float, northing: float, width: float, height: float) -> Polygon:
    return Polygon(
        [
            (easting, northing),
            (easting + width, northing),
            (easting + width, northing + height),
            (easting, northing + height),
        ]
    )


def write_zip(
    name: str,
    geometry_type: str,
    geometries: list[BaseGeometry],
    fields: dict[str, list[object]],
    crs: str | None,
) -> None:
    with tempfile.TemporaryDirectory() as directory, warnings.catch_warnings():
        # pyogrio warns when no CRS is given; parcels_no_crs.zip omits it on purpose.
        warnings.filterwarnings("ignore", message="'crs' was not provided")
        stem = Path(name).stem
        raw.write(
            Path(directory) / f"{stem}.shp",
            geometry=np.array([shapely.to_wkb(geometry) for geometry in geometries], dtype=object),
            field_data=[np.asarray(values) for values in fields.values()],
            fields=list(fields),
            geometry_type=geometry_type,
            crs=crs,
            driver="ESRI Shapefile",
            encoding="UTF-8",
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(Path(directory).iterdir()):
                if crs is None and path.suffix == ".prj":
                    continue
                # A fixed timestamp keeps the archive layout reproducible.
                entry = zipfile.ZipInfo(path.name, date_time=(1980, 1, 1, 0, 0, 0))
                archive.writestr(entry, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    (OUTPUT_DIR / name).write_bytes(buffer.getvalue())
    print(f"wrote sample_data/{name} ({len(geometries)} features, crs={crs})")


def main() -> None:
    # Cadastral plots in WGS 84 / UTM zone 43N: measured directly in the source CRS.
    plots = [plot(611_200, 1_434_300, 40, 25), plot(611_240, 1_434_300, 35, 25)]
    plots.append(plot(611_200, 1_434_325, 75, 30).difference(plot(611_230, 1_434_335, 10, 10)))
    write_zip(
        "parcels_utm43n.zip",
        "Polygon",
        plots,
        {"plot_no": [101, 102, 103], "owner": ["K. Rao", "S. Iyer", "Municipal"]},
        "EPSG:32643",
    )

    # Roads in WGS 84 longitude/latitude: reprojected to UTM 43N before measuring.
    roads = [
        LineString([(77.7482, 12.9692), (77.7500, 12.9695), (77.7518, 12.9722)]),
        LineString([(77.7490, 12.9700), (77.7490, 12.9720)]),
    ]
    write_zip(
        "roads_wgs84.zip",
        "LineString",
        roads,
        {"name": ["Main haul road", "Service road"], "surface": ["gravel", "earth"]},
        "EPSG:4326",
    )

    # The same plots without a .prj: features are extracted, measurements fail explicitly.
    write_zip(
        "parcels_no_crs.zip",
        "Polygon",
        plots,
        {"plot_no": [101, 102, 103], "owner": ["K. Rao", "S. Iyer", "Municipal"]},
        None,
    )


if __name__ == "__main__":
    main()
