# Geospatial File Measurement API

A REST API that takes a geospatial file, reads every feature in it and calculates its area or
length. I built it as my take-home assignment for the backend internship at Aereo.

## About the Project

You upload either a **KML file** or a **ZIP file containing a Shapefile**. The API:

1. reads every feature in the file: its geometry, geometry type, CRS and properties,
2. calculates the **area** of each polygon (in m²) and the **length** of each line (in m),
3. stores the file, its features and the measurements in PostgreSQL,
4. returns the results through a few simple endpoints.

Points are stored too, but they don't get a measurement.

The tricky part is the coordinate system. Most KML files, and many Shapefiles, store
coordinates as longitude and latitude in degrees (EPSG:4326). You can't calculate a correct
area or length directly from degrees, so the API first converts each feature into a projected
CRS that uses metres, and only then measures it. [CRS Handling](#crs-handling) explains how.

To try it:

```bash
docker compose up --build
```

Then open http://localhost:8000/docs.

## Features

- Upload KML files and ZIP files with a Shapefile
- Read the features of every KML folder and every Shapefile record
- Store each feature's index, geometry type, geometry (as GeoJSON), CRS and properties
- Calculate polygon area (m²) and LineString length (m), including multi-part geometries
- Store points without calculating a measurement
- Mark unsupported or invalid geometries on that feature only, instead of failing the whole file
- Handle both geographic CRSs (like EPSG:4326) and projected CRSs (like UTM)
- Save the CRS that was used for each measurement
- Check every upload before processing it (file type, ZIP contents, size limits)
- Store files, features and measurements in PostgreSQL, with Alembic migrations
- Swagger documentation at `/docs`
- Run everything with one Docker Compose command

## Assignment Requirements

| Requirement | Where it is done |
|---|---|
| FastAPI or Django backend | FastAPI ([`app/main.py`](app/main.py)) |
| Upload a `.kml` file or a `.zip` with a Shapefile | `POST /api/v1/files` |
| For every feature: ID/index, geometry type, geometry, CRS, properties | [`geospatial_processor.py`](app/services/geospatial_processor.py), shown by `GET /api/v1/files/{id}/features` |
| Handle unsupported geometry types without crashing | `UNSUPPORTED` status for that feature |
| Polygon area, LineString length, nothing for points | [`measurement_service.py`](app/services/measurement_service.py) |
| Don't measure in degrees; transform to a projected CRS first | [`crs_service.py`](app/services/crs_service.py) |
| `GET /api/files/{id}/` and `GET /api/files/{id}/measurements/` | `GET /api/v1/files/{id}` and `GET /api/v1/files/{id}/measurements` (the `/api/files/...` paths work too) |
| README with setup, API, architecture, design decisions, learning and future scope | this file |

## Tech Stack

| Technology | Used for |
|---|---|
| Python 3.12 | the language |
| FastAPI | the REST API and the Swagger docs |
| Pydantic | response models and settings |
| PostgreSQL | the database |
| SQLAlchemy 2 and Alembic | database access and migrations |
| pyogrio (GDAL) | reading KML files and Shapefiles |
| Shapely | geometry checks and area/length calculations |
| PyProj | converting between CRSs and choosing the measurement CRS |
| pytest | tests |
| Ruff | linting and formatting |
| Docker, Docker Compose, uv | running the project and installing exact dependency versions |
| React, TypeScript, Vite | the optional demo page in `frontend/` |

GDAL, GEOS and PROJ come bundled inside the pyogrio, Shapely and PyProj packages, so nothing
GIS-related has to be installed on the machine separately.

**Why not GeoPandas?** `geopandas.read_file` reads all the geometries of a file at once, so a
single broken geometry (like a polygon ring that isn't closed) makes the whole file fail. It
also reads only the first folder of a KML file unless you read each layer yourself. I used
pyogrio directly instead (GeoPandas uses it internally anyway), so each geometry can be read and
checked on its own.

## Project Structure

```
app/
├── main.py                       creates the FastAPI app
├── api/                          routes, error handling and middleware
├── core/                         settings, enums, exceptions and logging
├── db/                           database connection and base model
├── models/                       database tables: files, features, measurements
├── schemas/                      response models for the API
├── repositories/                 database queries
└── services/                     the main logic
    ├── file_service.py           runs the whole upload process
    ├── file_validation.py        checks the upload and unzips Shapefiles safely
    ├── geospatial_processor.py   reads the features with GDAL
    ├── crs_service.py            chooses the CRS used for measuring
    └── measurement_service.py    calculates area and length
frontend/                         optional demo web page (see Demo Interface)
migrations/                       Alembic database migrations
tests/                            automated tests and test files
sample_data/                      example files to try the API with
scripts/                          a script that recreates the sample Shapefiles
```

The code is split into layers so that each part has one job:

```
request → API routes → FileService ─┬─→ GIS code (validation, reading, CRS, measuring)
                                    └─→ repositories → PostgreSQL
```

The routes don't contain any GIS logic, and the GIS code never touches the database.
`FileService` connects the two and decides when data is saved.

## How It Works

When a file is uploaded with `POST /api/v1/files`:

1. **The user uploads a file.** It is copied into a temporary folder. Uploads over 50 MB are
   rejected.
2. **The API validates it.** The extension must be `.kml` or `.zip`, and the content has to
   match: a `.kml` must really be a KML document, and a `.zip` must contain exactly one
   Shapefile (`.shp`, `.shx` and `.dbf`, plus an optional `.prj` and `.cpg`).
3. **The ZIP is extracted.** Only the Shapefile files are taken out, and they get new names in
   the temporary folder.
4. **The file is recorded.** A row is added to the `files` table with the status `PROCESSING`.
5. **Features and properties are read.** GDAL reads every KML folder and every Shapefile record.
   Each geometry is checked on its own, so one bad geometry doesn't stop the others.
6. **The CRS is checked.** KML always uses EPSG:4326. For a Shapefile, the CRS comes from its
   `.prj` file.
7. **A projected CRS is chosen** for each polygon and line (see [CRS Handling](#crs-handling)).
8. **Area or length is calculated** in that CRS and converted to metres.
9. **The results are stored.** All features and measurements are saved in one database
   transaction, and the file is marked `COMPLETED`. If something goes wrong, nothing partial is
   kept and the file is marked `FAILED`, with the reason.
10. **The API returns** the file's details: id, name, format, CRS, number of features and status.

The temporary folder is deleted at the end, whether the upload worked or not.

What gets measured:

| Geometry type | Measurement | Unit |
|---|---|---|
| Polygon, MultiPolygon | area (holes are subtracted, parts are added up) | m² |
| LineString, MultiLineString | length (parts are added up) | m |
| Point, MultiPoint | none, status `NOT_APPLICABLE` | — |
| GeometryCollection and other types | none, status `UNSUPPORTED` | — |

Measurements are flat (2D): heights (Z values) are ignored.

## CRS Handling

### Why EPSG:4326 can't be used directly

In EPSG:4326, coordinates are longitude and latitude in degrees. A degree isn't a fixed
distance: one degree of longitude is about 111 km at the equator, about 79 km at 45° latitude,
and it shrinks to zero at the poles. An "area" calculated from these numbers would be in square
degrees, which don't match any real size on the ground. So the coordinates first have to be
converted to a **projected CRS**, which uses metres.

### Why UTM

Every projected CRS distorts the Earth a little, because it flattens a curved surface. UTM
keeps this small by dividing the world into 60 zones, each 6° of longitude wide, with a
separate projection for each zone. Inside its own zone, UTM is accurate to about 0.1%. It's
also a standard choice for local and regional data, and every zone has an EPSG code (for
example EPSG:32643 is UTM zone 43N, which covers Bengaluru).

### How the UTM zone is selected

The zone is chosen **for each feature**, not once per file, because one file can have features
in different zones.

1. The API finds the centre of the feature.
2. The zone number comes from the centre's longitude:
   `zone = floor((longitude + 180) / 6) + 1`, which gives zones 1 to 60 (180° wraps around to
   zone 1).
3. The latitude decides the hemisphere: EPSG:326xx for north, EPSG:327xx for south.

The centre is calculated on a sphere instead of by simply averaging the longitudes. This matters
for features that cross the 180° line: averaging 179.9° and −179.9° gives 0°, which is on the
other side of the world.

### Checking that the CRS is accurate enough

After choosing a CRS, the API checks how much that projection stretches the feature at each of
its vertices, using PROJ's scale factors. The limits are:

- **0.25%** for lengths
- **0.5%** for areas

Inside its own zone, UTM stays within these limits. If a feature is so large that it would be
stretched more than this (for example a polygon 40° wide), the API doesn't return a distorted
number: it marks that measurement as `FAILED` and explains why.

### If the file already uses a projected CRS

If a Shapefile already uses a good projected CRS (for example a UTM zone, or a US State Plane
system), the API measures in that CRS directly. It only does this if the CRS has an EPSG code
(or another authority code), uses metres or feet (feet are converted to metres), isn't Web
Mercator, and passes the same accuracy check. Otherwise the feature is converted to UTM like any
geographic data.

### Why Web Mercator is not used for measuring

Web Mercator (EPSG:3857) is made for web maps like Google Maps and OpenStreetMap, not for
measuring. It stretches things more and more the further you go from the equator: in Berlin,
areas come out about 2.7 times too big. It isn't exact even at the equator, because it uses
formulas for a sphere on coordinates that belong to the WGS 84 ellipsoid. So the API never
measures in Web Mercator, even if the file uses it.

### Axis order

EPSG:4326 officially lists latitude first, but KML and Shapefile coordinates are stored with
longitude first. If this goes wrong, the code still runs, but points end up thousands of
kilometres away. Every conversion uses PyProj's `always_xy=True`, so longitude is always treated
as x and latitude as y. A test checks this with a known point in Berlin.

### The measurement CRS is stored

Each measurement saves the CRS it was calculated in (`measurement_crs`, for example
`EPSG:32643`), so anyone can check the number again in QGIS or PostGIS.

### Missing CRS

If a Shapefile has no `.prj` file, the API still saves its features and properties, but it
doesn't guess a CRS. Its area and length measurements are marked `FAILED`, with a message
saying that the CRS is missing. The API also reports coordinates that can't be right for the
file's CRS, for example metres in a file that claims to be EPSG:4326.

### Checking the results

To check that the measurements are correct, the tests compare them with the area calculated
directly on the Earth's ellipsoid using `pyproj.Geod`:

| Test case | CRS used | Difference |
|---|---|---|
| Polygons in Berlin and Sydney | EPSG:32633, EPSG:32756 | about 0.05% and 0.01% |
| Polygon in Bengaluru | EPSG:32643 | about 0.12% |
| Polygon crossing the 180° line (Fiji) | EPSG:32701 | about 0.18% |
| Polygon at 88°N | EPSG:32631 | about 0.08% |
| Web Mercator file (Berlin) | EPSG:32633 | about 0.05% |
| New York State Plane file (US feet) | EPSG:2263, used directly | about 0.0004% |

These small differences are expected. They come from UTM's own distortion, which depends on
where the feature is inside its zone.

## API Endpoints

| Method | Endpoint | What it does |
|---|---|---|
| `POST` | `/api/v1/files` | upload and process a file (form field `file`) |
| `GET` | `/api/v1/files/{id}` | file details and processing status |
| `GET` | `/api/v1/files/{id}/measurements` | the measurement of each feature, with a summary |
| `GET` | `/api/v1/files/{id}/features` | each feature's index, layer, geometry type, geometry, CRS and properties |
| `GET` | `/health` | checks that the API and the database are working |

The assignment writes the paths as `/api/files/...` with a trailing slash, so those paths work
too.

The `measurements` and `features` endpoints return 100 items by default. You can change this
with `limit` (up to 1000) and skip items with `offset`; `total` shows how many items there are.

Swagger UI is available at http://localhost:8000/docs.

### Upload a file

```bash
curl -F "file=@sample_data/site_survey.kml" http://localhost:8000/api/v1/files
```

(In Windows PowerShell, use `curl.exe`.)

Response, `201 Created`:

```json
{
  "id": "a62fa4af-5a08-4ff4-9a96-563512a678ee",
  "filename": "site_survey.kml",
  "format": "KML",
  "file_size_bytes": 2542,
  "crs": "EPSG:4326",
  "feature_count": 6,
  "status": "COMPLETED",
  "error_message": null,
  "created_at": "2026-10-07T14:12:49.786134Z",
  "updated_at": "2026-10-07T14:12:49.876857Z"
}
```

`GET /api/v1/files/{id}` returns the same object.

### Get the measurements

```bash
curl http://localhost:8000/api/v1/files/a62fa4af-5a08-4ff4-9a96-563512a678ee/measurements
```

Response (two of the six features shown):

```json
{
  "file_id": "a62fa4af-5a08-4ff4-9a96-563512a678ee",
  "file_status": "COMPLETED",
  "total": 6,
  "limit": 100,
  "offset": 0,
  "summary": { "calculated": 3, "not_applicable": 2, "unsupported": 1, "failed": 0 },
  "items": [
    {
      "feature_id": "4c6290e0-d477-4ee7-acf1-12af7bac549d",
      "feature_index": 0,
      "geometry_type": "Polygon",
      "measurement_type": "AREA",
      "value": 168279.16903746937,
      "unit": "m²",
      "measurement_crs": "EPSG:32643",
      "status": "CALCULATED",
      "error_message": null
    },
    {
      "feature_id": "f44362ea-3d1c-4566-8153-a2e5274a1ddf",
      "feature_index": 5,
      "geometry_type": "GeometryCollection",
      "measurement_type": "NONE",
      "value": null,
      "unit": null,
      "measurement_crs": null,
      "status": "UNSUPPORTED",
      "error_message": "Measurement is not supported for GeometryCollection geometries."
    }
  ]
}
```

### Get the features

```bash
curl "http://localhost:8000/api/v1/files/a62fa4af-5a08-4ff4-9a96-563512a678ee/features?limit=1"
```

```json
{
  "file_id": "a62fa4af-5a08-4ff4-9a96-563512a678ee",
  "file_status": "COMPLETED",
  "total": 6,
  "limit": 1,
  "offset": 0,
  "items": [
    {
      "id": "4c6290e0-d477-4ee7-acf1-12af7bac549d",
      "feature_index": 0,
      "layer": "Site boundary",
      "geometry_type": "Polygon",
      "crs": "EPSG:4326",
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[77.748, 12.969], [77.752, 12.969], [77.752, 12.9725], [77.748, 12.9725], [77.748, 12.969]]]
      },
      "geometry_error": null,
      "properties": {
        "Name": "Survey boundary",
        "surveyor": "Field team B",
        "site_code": "WFD-07",
        "description": "Boundary agreed with the client on 2026-09-14"
      }
    }
  ]
}
```

### Sample files

| File | What it shows |
|---|---|
| `sample_data/site_survey.kml` | KML folders, extra data, a polygon, a multipolygon, a line, points and an unsupported geometry collection |
| `sample_data/parcels_utm43n.zip` | a Shapefile already in UTM 43N (EPSG:32643), measured directly: exactly 1000, 875 and 2150 m² |
| `sample_data/roads_wgs84.zip` | a Shapefile in EPSG:4326, converted to UTM 43N before measuring |
| `sample_data/parcels_no_crs.zip` | a Shapefile without a `.prj` file: features are saved, measurements fail with a clear message |

## Error Handling

All errors have the same format:

```json
{
  "error": {
    "code": "UNSUPPORTED_FILE_FORMAT",
    "message": "Only KML files (.kml) and ZIP archives containing a Shapefile (.zip) are supported."
  }
}
```

There are two kinds of problems, and they are handled differently.

**Problems with the whole file** stop the upload and return an error:

| Problem | HTTP status | Error code |
|---|---|---|
| Not a `.kml` or `.zip` file | 415 | `UNSUPPORTED_FILE_FORMAT` |
| File too big, or more than 50,000 features | 413 | `FILE_TOO_LARGE` |
| Content doesn't match the extension, or the file can't be read | 422 | `MALFORMED_FILE` |
| ZIP without exactly one complete Shapefile, or with unsafe contents | 422 | `INVALID_SHAPEFILE_ARCHIVE` |
| Missing `file` field, invalid id or invalid `limit`/`offset` | 422 | `INVALID_REQUEST` |
| Upload body that can't be read as a form | 400 | `INVALID_REQUEST` |
| Unknown file id | 404 | `FILE_NOT_FOUND` |
| Unexpected error while processing | 500 | `PROCESSING_FAILED` |
| Database not available | 503 | `DATABASE_ERROR` |

If the error happens after the file record was created, the response includes the file's id,
and the file is marked `FAILED` with the reason.

**Problems with one feature** don't stop the upload. They are saved as that feature's
measurement status, with a message:

- **Points**: `NOT_APPLICABLE`, because no measurement is needed
- **Geometry collections**: `UNSUPPORTED`
- **Invalid geometry**, like a polygon that crosses itself: `FAILED`, with the reason from GEOS.
  The API doesn't "fix" invalid geometries automatically, because the fix can change the shape
  that gets measured.
- **Missing or unreadable geometry**, or coordinates like `NaN`: `FAILED`
- **Missing CRS**: `FAILED`
- **Feature too large for one UTM zone**: `FAILED`

Error messages never show stack traces, file paths or database errors; those only go to the
server logs. Every response has an `X-Request-ID` header, and the same id appears in the logs,
so it's easy to find the log lines of a request.

### Upload safety

- Files from a ZIP are never written using the paths stored inside the archive. They get new
  names, so a path like `../../etc/passwd` can't write outside the temporary folder. Archives
  with such paths, symbolic links or passwords are rejected anyway.
- There are limits on the upload size (50 MB), the unzipped size (250 MB), the number of files
  in a ZIP (100) and the number of features (50,000).
- KML files with a `DOCTYPE` are rejected. KML never needs one, and blocking it prevents XML
  entity attacks.
- The Shapefile parts are checked before GDAL reads them, because GDAL quietly accepts a broken
  `.dbf` file and returns the features without their attributes.
- GDAL doesn't download the links inside KML files (there is a test for this).

## Running the Project

### With Docker

You need Docker with Docker Compose v2.24 or newer.

```bash
docker compose up --build
```

This starts PostgreSQL and the API, runs the database migrations, and serves the API at
http://localhost:8000 (Swagger UI at `/docs`).

Other useful commands:

```bash
docker compose --profile test run --rm --build tests   # run the tests in Docker
docker compose logs -f api                             # follow the API logs
docker compose down                                    # stop (add -v to delete the database too)
```

### Without Docker

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) (pip works too).

```bash
docker compose up -d db                  # start only PostgreSQL
uv sync --extra dev                      # or: python -m venv .venv, then pip install -e ".[dev]"
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload
```

Settings are read from environment variables (or from `.env`):

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | required | PostgreSQL connection URL |
| `MAX_UPLOAD_SIZE_MB` | 50 | largest upload allowed |
| `MAX_UNCOMPRESSED_SIZE_MB` | 250 | largest unzipped Shapefile allowed |
| `MAX_ARCHIVE_MEMBERS` | 100 | most files allowed in a ZIP |
| `MAX_FEATURE_COUNT` | 50000 | most features allowed in one file |
| `LOG_LEVEL` | INFO | logging level |

`.env.example` uses `127.0.0.1` instead of `localhost` on purpose: on my Windows machine,
`localhost` resolved to IPv6 first, and that made every new database connection wait 5 seconds.

### Database

There are three tables:

- `files`: one row per upload, with the name, format, size, CRS, feature count, status and
  error message
- `features`: one row per feature, with its index, layer, geometry type, geometry (GeoJSON in
  the file's original CRS) and properties
- `measurements`: one row per feature, with the type, value, unit, the CRS it was measured in,
  the status and the error message

Foreign keys connect the tables, and deleting a file also deletes its features and
measurements. Measurements have their own table because they are calculated values: they can
be recalculated, or new ones (like perimeter) added, without touching the original features.
Migrations are done with Alembic, and they run automatically when the Docker container starts.

## Demo Interface

The `frontend/` folder contains a small web page for trying the API without curl or Swagger.
You pick a KML file or a Shapefile ZIP, and the page sends it to `POST /api/v1/files`. It then
shows the file details, the measurements and any feature-level errors, using
`GET /api/v1/files/{id}`, `/measurements` and `/features`.

It is only a demonstration layer. The page doesn't do any GIS work: validation, CRS handling,
the area and length calculations and the database work all happen in the backend. The page just
calls the API and shows what it returns. It's built with React, TypeScript and Vite, and React
is its only runtime dependency.

To start it, with the backend already running on port 8000:

```bash
cd frontend
npm install
npm run dev          # then open http://localhost:5173
```

This needs Node.js 20.19+ or 22.12+. The Vite server forwards `/api`, `/health` and `/docs` to
the backend, so the page and the API share one address and the backend needs no CORS settings.

You can also run it in Docker, without installing Node:

```bash
docker compose --profile demo up --build    # then open http://localhost:5173
```

## Testing

The project has **199 automated tests**. They cover file validation, reading KML files and
Shapefiles, CRS handling, measurements, API behaviour, database transactions and the upload
safety checks. Together they cover 99% of the lines and branches in `app/`.

The tests run against a real PostgreSQL test database, which is created and migrated
automatically. They don't need internet access, and they treat warnings as errors.

```bash
uv run pytest                                          # needs PostgreSQL: docker compose up -d db
uv run pytest --cov=app --cov-branch                   # with coverage
docker compose --profile test run --rm --build tests   # inside Docker
```

Some examples of what is tested:

- bad uploads: wrong extension, wrong content, empty files, broken or truncated ZIPs, missing
  Shapefile parts, unsafe paths inside a ZIP, zip bombs, broken `.dbf`/`.shp`/`.shx` files
- KML folders (including nested ones), properties, and geometries that can't be read
- UTM zone selection in both hemispheres and across the 180° line, axis order, State Plane files
  in feet, Web Mercator files, and the exact 0.25% and 0.5% limits
- areas and lengths, compared with known values and with `pyproj.Geod`
- a failed save rolls everything back and marks the file `FAILED`
- every endpoint and every error response
- the database migration matches the models

## Design Decisions

- **FastAPI instead of Django REST Framework.** The project is only an API, with no admin pages
  or templates. FastAPI gives typed request and response models and automatic Swagger docs with
  less setup.
- **Synchronous processing.** The assignment asks for "upload and process", and typical survey
  files are small enough to process during the request. The processing code doesn't depend on
  the HTTP request, so it could be moved to a background worker later without changing the GIS
  code.
- **pyogrio instead of GeoPandas**, so that problems can be reported for each feature (see
  [Tech Stack](#tech-stack)).
- **One UTM zone per feature.** Other options I considered:
  - *Web Mercator*: very wrong away from the equator.
  - *One UTM zone for the whole file*: doesn't work when the features are spread across zones.
  - *Geodesic calculations on the ellipsoid* (`pyproj.Geod`): very accurate, especially for
    large features, but the assignment asks to transform to a projected CRS. I use it in the
    tests to check the results instead.
  - *A custom projection centred on each feature*: accurate, but it has no EPSG code, so the
    result is harder for someone else to reproduce.
- **No automatic geometry repair.** `make_valid` can turn a polygon into a mix of polygons and
  lines, which changes what gets measured. It's better to report the problem so the data can be
  fixed at the source.
- **GeoJSON in JSONB instead of PostGIS.** Nothing in the assignment needs spatial queries, and
  JSONB keeps the original coordinates exactly. If searching by location is needed later,
  PostGIS would be the next step.
- **Versioned `/api/v1` paths**, so the API can change later without breaking existing clients,
  plus the `/api/files/` paths from the assignment.

## Performance

I measured this on my laptop (Intel i7-12700H, Windows 11, Docker Desktop with WSL 2) with other
programs running, so the numbers change a bit between runs:

- **50,000 small polygons** (a 2.5 MB ZIP), uploaded and processed in the Docker container:
  about 14–17 seconds, using about 210 MB of extra memory.
- **A 15.8 MB KML file with 50,000 placemarks**: reading it takes about 2 seconds and about
  160 MB of extra memory, because GDAL's KML driver loads the whole file into memory (roughly 10
  times the file size).

When I profiled a 100,000-polygon file, the slowest step was inserting the rows into
PostgreSQL, which took about half of the total time. The default limit of 50,000 features keeps
a single upload under about 20 seconds.

## What I Learned

One of the main things I learned from this project was that calculating area or distance from
latitude and longitude isn't as simple as using the coordinate values directly. Working out why
degrees aren't a unit of length helped me understand what a projected CRS really does, why UTM
splits the world into zones, and why every projection is only accurate in a limited area.

I also learned how easy it is to get the axis order wrong. EPSG:4326 officially puts latitude
first, but the files store longitude first. With the wrong order, the code still runs without
any error and just puts the points thousands of kilometres away. That's why I added a test that
converts a known point in Berlin and checks the exact result.

Reading the files was harder than I expected. GDAL treats every KML folder as a separate layer,
so reading a KML file the simple way only returned the features of the first folder. And when
one geometry in a file was broken, like a polygon ring that wasn't closed, GeoPandas failed the
whole file. This taught me that file processing can fail in many different ways, and that a
problem with one feature shouldn't always fail the whole file. So the API now handles the two
separately: problems with the file stop the upload, while problems with a single feature are
saved as that feature's status.

Some problems don't produce any error at all. When I tested a Shapefile with a broken `.dbf`
file, GDAL still opened it and simply returned the features without their attributes. Since the
whole point of the API is to extract those attributes, I added my own checks of the Shapefile
files before GDAL reads them.

While testing the CRS logic, I found an issue with geometries crossing the antimeridian (the 180°
line). My original check only looked at the corners of the feature's convex hull, but in
longitude/latitude the hull of a feature that crosses 180° doesn't contain its real eastern and
western ends. A test line was accepted even though its error was almost three times the limit.
Now every vertex of the feature is checked. Fixing this showed me why geospatial edge cases are
important, and why it's not enough to test with "normal" data.

I also learned that tests need to check real values, not just that the code runs. Comparing my
results with pyproj's geodesic calculations gave me a reference to test against, although even
the reference needed care: one test polygon's hole was being added instead of subtracted,
because that function depends on the direction of the rings. I also tried breaking parts of the
code on purpose to see whether the tests would notice. They caught most changes, but not when I
made the accuracy limits four times looser, so I added tests for exactly those limits.

On the database side, I learned to read generated code carefully. Alembic's autogenerated
migration created every check constraint twice, so I rewrote it by hand and added a test that
compares the migrated database with the models. I also had to think about transactions: a file
should never be marked `COMPLETED` if its features weren't saved, so they are all saved in one
transaction.

## Limitations

- Processing is synchronous, so the upload request waits until the whole file is processed.
  That's why there is a default limit of 50,000 features. Much larger files would need
  background processing.
- UTM is only accurate near the middle of each zone. If part of a feature is more than about
  4.4° of longitude from its zone's centre line (near the equator; a bit more further north or
  south), the feature isn't measured. The API reports it as `FAILED` instead of returning a
  less accurate number.
- Polygons that go around a pole, or that are stored as a band covering every longitude, are not
  handled correctly.
- Invalid geometries are reported but not repaired, geometry collections aren't measured, and
  heights (Z) are ignored.
- There is no PostGIS, because the assignment doesn't need spatial queries. This means the API
  can't search features by location.
- GDAL's KML driver loads the whole KML file into memory (about 10 times the file size).
- Only one Shapefile per ZIP. KMZ, GeoJSON and GeoPackage files aren't supported.
- A Shapefile without a `.cpg` file is read with GDAL's default text encoding, so non-English
  characters in its attributes can come out wrong.
- In KML files, `NetworkLink`s are not followed, and a `GroundOverlay` is read as a polygon (its
  outline). If several placemarks are placed directly under `<kml>` without a `<Document>`, only
  the last one is read, because the KML standard allows only one there.
- There is no login or rate limiting. If the server stops in the middle of an upload, that file
  stays `PROCESSING`.

## Future Improvements

- Process large files in a background worker, so the upload returns right away and the client
  checks the status later.
- Use PostgreSQL `COPY` for the bulk inserts, which are the slowest part right now.
- Read KML files in a streaming way, so large files need less memory.
- Add PostGIS if the API ever needs spatial queries, like searching features by location.
- Add a CI pipeline (for example GitHub Actions) that runs Ruff and the tests on every push.
- Use geodesic calculations (`pyproj.Geod`) for features that are too large for a UTM zone, and
  handle polygons around the poles.

## License

MIT, see [LICENSE](LICENSE).
