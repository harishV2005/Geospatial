# Geospatial File Measurement API

A production-quality **Django + Django REST Framework** backend that accepts geospatial files (Shapefile .zip or KML), processes every feature, reprojects geometries to an appropriate projected CRS, and returns area/length measurements.

---

## Table of Contents

1. [Setup](#setup)
2. [API Documentation](#api-documentation)
3. [Architecture](#architecture)
4. [Design Decisions](#design-decisions)
5. [Learning & Future Scope](#learning--future-scope)

---

## Setup

### Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.10+ |
| pip | latest |

### Install & Run

```bash
# 1. Clone the repo
git clone <your-repo-url>
cd <repo-folder>

# 2. Create and activate a virtual environment
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy environment file
cp .env.example .env        # Windows: copy .env.example .env

# 5. Apply migrations
python manage.py migrate

# 6. Start the development server
python manage.py runserver
```

The server will be available at **http://127.0.0.1:8000/**.

### Interactive API Docs

| UI | URL |
|----|-----|
| Swagger UI | http://127.0.0.1:8000/api/docs/ |
| ReDoc | http://127.0.0.1:8000/api/redoc/ |
| OpenAPI Schema (JSON) | http://127.0.0.1:8000/api/schema/ |

### Run Tests

```bash
python manage.py test files --verbosity=2
```

---

## API Documentation

### `POST /api/files/`

Upload a geospatial file for processing.

**Request** — `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `file` | File | `.zip` containing a Shapefile, or a `.kml` file (max 50 MB) |

**Example (curl)**

```bash
# Upload KML
curl -X POST http://127.0.0.1:8000/api/files/ \
     -F "file=@/path/to/survey.kml"

# Upload Shapefile (zipped)
curl -X POST http://127.0.0.1:8000/api/files/ \
     -F "file=@/path/to/shapefile.zip"
```

**Success Response** — `201 Created`

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "filename": "survey.kml",
  "file_type": "kml",
  "feature_count": 12,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error_message": "",
  "uploaded_at": "2024-01-15T10:30:00Z",
  "processed_at": "2024-01-15T10:30:02Z"
}
```

**Error Responses**

| Status | Reason |
|--------|--------|
| `400` | No file provided, or unsupported extension |
| `422` | File is valid format but contents are malformed / unreadable |

---

### `GET /api/files/{id}/`

Retrieve metadata for a previously uploaded file.

**Example**

```bash
curl http://127.0.0.1:8000/api/files/3fa85f64-5717-4562-b3fc-2c963f66afa6/
```

**Response** — `200 OK`

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "filename": "survey.kml",
  "file_type": "kml",
  "feature_count": 12,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error_message": "",
  "uploaded_at": "2024-01-15T10:30:00Z",
  "processed_at": "2024-01-15T10:30:02Z"
}
```

---

### `GET /api/files/{id}/measurements/`

Retrieve per-feature measurements for a processed file.

**Example**

```bash
curl http://127.0.0.1:8000/api/files/3fa85f64-5717-4562-b3fc-2c963f66afa6/measurements/
```

**Response** — `200 OK`

```json
{
  "file_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "filename": "survey.kml",
  "crs": "EPSG:4326",
  "feature_count": 3,
  "features": [
    {
      "feature_index": 0,
      "feature_id": "survey_boundary",
      "geometry_type": "Polygon",
      "geometry_geojson": "{\"type\": \"Polygon\", \"coordinates\": [...]}",
      "crs": "EPSG:4326",
      "projected_crs": "EPSG:32643",
      "properties": {"name": "Survey Boundary"},
      "area_m2": 1234567.89,
      "area_formatted": "123.4568 ha",
      "length_m": null,
      "length_formatted": "",
      "measurement_error": ""
    },
    {
      "feature_index": 1,
      "feature_id": "road_section",
      "geometry_type": "LineString",
      "geometry_geojson": "{\"type\": \"LineString\", \"coordinates\": [...]}",
      "crs": "EPSG:4326",
      "projected_crs": "EPSG:32643",
      "properties": {"name": "Road Section A"},
      "area_m2": null,
      "area_formatted": "",
      "length_m": 4523.12,
      "length_formatted": "4.5231 km",
      "measurement_error": ""
    },
    {
      "feature_index": 2,
      "feature_id": "site_pin",
      "geometry_type": "Point",
      "geometry_geojson": "{\"type\": \"Point\", \"coordinates\": [77.2, 28.6]}",
      "crs": "EPSG:4326",
      "projected_crs": "EPSG:4326",
      "properties": {"name": "Site Pin"},
      "area_m2": null,
      "area_formatted": "",
      "length_m": null,
      "length_formatted": "",
      "measurement_error": ""
    }
  ]
}
```

**Error Responses**

| Status | Reason |
|--------|--------|
| `404` | File ID not found |
| `409` | File is still PENDING or PROCESSING |

---

## Architecture

### Application Structure

```
geo_api_project/
├── config/                    # Django project settings
│   ├── settings.py            # All Django & DRF configuration
│   ├── urls.py                # Root URL routing (admin + api/)
│   ├── wsgi.py / asgi.py
│   └── __init__.py
│
├── files/                     # Core application
│   ├── models.py              # GeoFile + FeatureMeasurement models
│   ├── views.py               # FileUploadView, FileDetailView, FileMeasurementsView
│   ├── serializers.py         # DRF serializers + OpenAPI schema hints
│   ├── urls.py                # App-level URL patterns
│   ├── admin.py               # Django admin registrations
│   ├── tests.py               # 21 unit + integration tests
│   └── services/
│       ├── parsers.py         # Shapefile (Fiona) + KML (lxml) parsers
│       ├── measurement.py     # Geometry measurement logic
│       ├── crs_utils.py       # CRS parsing + UTM zone selection
│       └── processor.py       # Orchestration: parse → measure → persist
│
├── uploads/                   # Uploaded files (auto-created)
├── manage.py
├── requirements.txt
└── .env.example
```

### File-Processing Flow

```
POST /api/files/
       │
       ▼
  views.py: FileUploadView.post()
       │  1. Validate extension (.zip / .kml)
       │  2. Save raw file to uploads/ directory
       │  3. Create GeoFile record (status=PENDING)
       │  4. Call process_geo_file()
       ▼
  services/processor.py: process_geo_file()
       │  status → PROCESSING
       │
       ├──▶ services/parsers.py: parse_geo_file()
       │         ├── .zip  → parse_shapefile()  [Fiona]
       │         └── .kml  → parse_kml()        [lxml]
       │         Returns: ParseResult(crs, features[])
       │
       ├──▶ For each feature:
       │    services/measurement.py: measure_feature()
       │         ├── Parse geometry with Shapely
       │         ├── Determine geometry type
       │         ├── Select projected CRS (crs_utils.py)
       │         ├── Reproject geometry
       │         └── Calculate area (Polygon) or length (LineString)
       │
       └──▶ Bulk-insert FeatureMeasurement rows
            status → COMPLETED (or FAILED on error)
```

### Measurement Calculation Flow

| Geometry Type | Action |
|---------------|--------|
| `Polygon` / `MultiPolygon` | Reproject → `shapely_geom.area` (m²) |
| `LineString` / `MultiLineString` | Reproject → `shapely_geom.length` (m) |
| `Point` / `MultiPoint` | No measurement (returns `null`) |
| `GeometryCollection` | Gracefully skipped (returns error message in feature row) |
| Missing / unparseable geometry | Gracefully skipped (error message stored per-feature) |

Measurements are per-feature: one bad geometry never aborts the entire file.

### CRS Handling

**Strategy: Automatic UTM Zone Selection**

1. Parse the source CRS using `pyproj.CRS.from_user_input()` (supports EPSG codes, WKT, PROJ strings).
2. If already projected (units are metres) → use it as-is, no reprojection needed.
3. If geographic (lat/lon degrees):
   - Compute the geometry bounding-box centroid.
   - If source CRS ≠ WGS-84, reproject centroid to WGS-84 first.
   - Determine UTM zone number: `zone = int((lon + 180) / 6) % 60 + 1`
   - Select EPSG `326xx` (northern hemisphere) or `327xx` (southern hemisphere).
4. Reproject geometry using `pyproj.Transformer` + `shapely.ops.transform`.

This is a deliberate "good enough" heuristic; for large or cross-zone geometries a custom equal-area CRS (e.g. EPSG:6933 NSIDC) could be used instead.

---

## Design Decisions

### Why synchronous processing (no Celery)?

For a submission/demo context, synchronous processing keeps the stack simple (no Redis/broker required). The trade-off is that large files will block the request. **Alternative considered:** Celery with Redis; the processor function (`process_geo_file`) is already isolated and stateless — dropping in a `@shared_task` decorator would be trivial.

### Why lxml for KML instead of Fiona's LIBKML driver?

Fiona's pre-built Windows wheels do not include the LIBKML driver. Using `lxml` (pure XML parsing) is cross-platform, has zero additional dependencies, and gives full control over the geometry extraction logic. On Linux/macOS, Fiona's `driver="KML"` would work, but portability won out.

### Why Shapely + pyproj instead of GeoDjango / PostGIS?

GeoDjango requires GDAL/GEOS system libraries and a spatial database (PostGIS). Shapely + pyproj achieves the same measurement results with pure Python wheels — no system packages, zero `apt install` steps for the reviewer. This is a deliberate developer-experience trade-off.

### Why UUID primary keys?

UUIDs avoid leaking sequential row counts to clients (no enumeration attack), are safe to generate without a DB round-trip, and are URL-friendly. The cost is slightly larger storage and slightly slower indexed lookups — acceptable at submission scale.

### Why bulk_create for FeatureMeasurement?

For files with thousands of features, individual `save()` calls per feature would be extremely slow. `bulk_create()` reduces N round-trips to 1.

### Why store geometry as GeoJSON text?

Portable across all database backends (SQLite, PostgreSQL, MySQL). No spatial database extension required. If migrating to PostGIS, this column could be swapped for a native `geometry` field with no logic changes elsewhere.

---

## Learning & Future Scope

### What I learned / reinforced

- **pyproj's authority model:** `CRS.to_authority()` sometimes fails for datum-ensemble CRSes (e.g. `OGC:CRS84` → `EPSG:4326` mapping); adding a fallback to `to_proj4()` is essential.
- **Fiona driver availability:** Platform-specific wheel builds omit certain GDAL drivers; always test on the target OS or use a pure-Python alternative.
- **Shapely `shape()` + `mapping()`:** Clean round-trip between GeoJSON dicts and Shapely objects simplifies the entire geometry pipeline.

### Future Scope

| Feature | Description |
|---------|-------------|
| **Async processing** | Replace synchronous `process_geo_file()` call with a Celery task + Redis broker for non-blocking uploads |
| **GeoJSON input** | Add a third parser for `.geojson` / `.json` files |
| **Pagination** | Add cursor-based pagination to the measurements endpoint for files with thousands of features |
| **PostGIS support** | Replace JSONField geometry storage with a native `geometry` column for spatial queries |
| **File validation** | Validate ZIP contents (virus scan, magic bytes) before extraction |
| **Authentication** | Add token/JWT authentication so files are scoped to users |
| **Streaming uploads** | Support chunked multipart uploads for very large files (> 50 MB) |
| **Export** | Add `GET /api/files/{id}/measurements.csv` for tabular download |
| **Docker** | Add `Dockerfile` + `docker-compose.yml` for one-command setup |
| **CI** | GitHub Actions workflow to run tests on every push |
