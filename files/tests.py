"""
files/tests.py

Unit and integration tests covering:
  - CRS utilities (parse, UTM selection, projected check)
  - Measurement service (polygon area, linestring length, point no-op,
    unsupported type graceful handling, missing geometry)
  - API views (upload validation, file detail, measurements response)
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import GeoFile, FeatureMeasurement, FileStatus
from .services.crs_utils import (
    get_utm_crs,
    parse_crs,
    select_projected_crs,
    crs_to_string,
)
from .services.measurement import measure_feature


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_geo_file(**kwargs) -> GeoFile:
    defaults = dict(
        filename="test.kml",
        file_path="/tmp/test.kml",
        file_type="kml",
        crs="EPSG:4326",
        feature_count=1,
        status=FileStatus.COMPLETED,
    )
    defaults.update(kwargs)
    return GeoFile.objects.create(**defaults)


def _make_zip_shapefile() -> bytes:
    """
    Create a minimal in-memory zipped shapefile using pyshp.
    Returns raw bytes of the zip.
    """
    import shapefile

    buf_shp = io.BytesIO()
    buf_shx = io.BytesIO()
    buf_dbf = io.BytesIO()

    w = shapefile.Writer(shp=buf_shp, shx=buf_shx, dbf=buf_dbf)
    w.field("NAME", "C", 40)
    # Simple polygon: a unit square
    w.poly([[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]])
    w.record("TestFeature")
    w.close()

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("test.shp", buf_shp.getvalue())
        zf.writestr("test.shx", buf_shx.getvalue())
        zf.writestr("test.dbf", buf_dbf.getvalue())
        # Minimal .prj for EPSG:4326
        prj_wkt = (
            'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
            'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
            'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
        )
        zf.writestr("test.prj", prj_wkt)
    return zip_buf.getvalue()


def _make_kml_bytes() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Test Polygon</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              0,0,0 0,1,0 1,1,0 1,0,0 0,0,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>"""


# ---------------------------------------------------------------------------
# CRS utility tests
# ---------------------------------------------------------------------------


class TestCrsUtils(TestCase):
    def test_parse_valid_epsg(self):
        crs = parse_crs("EPSG:4326")
        self.assertIsNotNone(crs)

    def test_parse_invalid_returns_none(self):
        crs = parse_crs("NOT_A_REAL_CRS_STRING_12345")
        self.assertIsNone(crs)

    def test_get_utm_crs_northern(self):
        crs = get_utm_crs(lon=10.0, lat=51.0)  # Central Europe → UTM zone 32N
        self.assertTrue(crs.is_projected)
        auth, code = crs.to_authority()
        self.assertEqual(auth, "EPSG")
        self.assertEqual(int(code), 32632)

    def test_get_utm_crs_southern(self):
        crs = get_utm_crs(lon=25.0, lat=-25.0)  # Southern Africa → UTM zone 35S
        self.assertTrue(crs.is_projected)
        auth, code = crs.to_authority()
        self.assertEqual(auth, "EPSG")
        self.assertEqual(int(code), 32735)

    def test_select_projected_crs_already_projected(self):
        from pyproj import CRS
        from shapely.geometry import box

        proj_crs = CRS.from_epsg(32632)
        geom = box(0, 0, 1, 1)
        result = select_projected_crs(proj_crs, geom)
        self.assertTrue(result.is_projected)

    def test_select_projected_crs_geographic_input(self):
        from pyproj import CRS
        from shapely.geometry import box

        geo_crs = CRS.from_epsg(4326)
        geom = box(10.0, 51.0, 11.0, 52.0)  # Small box in Germany
        result = select_projected_crs(geo_crs, geom)
        self.assertTrue(result.is_projected)

    def test_crs_to_string(self):
        from pyproj import CRS

        crs = CRS.from_epsg(4326)
        s = crs_to_string(crs)
        self.assertIn("4326", s)


# ---------------------------------------------------------------------------
# Measurement service tests
# ---------------------------------------------------------------------------


class TestMeasurementService(TestCase):

    SOURCE_CRS = "EPSG:4326"

    def _polygon_geom(self):
        return {
            "type": "Polygon",
            "coordinates": [
                [
                    [0.0, 0.0],
                    [0.0, 1.0],
                    [1.0, 1.0],
                    [1.0, 0.0],
                    [0.0, 0.0],
                ]
            ],
        }

    def _linestring_geom(self):
        return {
            "type": "LineString",
            "coordinates": [[0.0, 0.0], [1.0, 0.0]],
        }

    def _point_geom(self):
        return {"type": "Point", "coordinates": [10.0, 51.0]}

    def _geometry_collection_geom(self):
        return {"type": "GeometryCollection", "geometries": []}

    def test_polygon_has_area(self):
        result = measure_feature(
            feature_index=0,
            feature_id="1",
            raw_geometry=self._polygon_geom(),
            properties={},
            source_crs_str=self.SOURCE_CRS,
        )
        self.assertEqual(result.geometry_type, "Polygon")
        self.assertIsNotNone(result.area_m2)
        self.assertIsNone(result.length_m)
        self.assertGreater(result.area_m2, 0)
        self.assertNotEqual(result.area_formatted, "")

    def test_linestring_has_length(self):
        result = measure_feature(
            feature_index=1,
            feature_id="2",
            raw_geometry=self._linestring_geom(),
            properties={},
            source_crs_str=self.SOURCE_CRS,
        )
        self.assertEqual(result.geometry_type, "LineString")
        self.assertIsNone(result.area_m2)
        self.assertIsNotNone(result.length_m)
        self.assertGreater(result.length_m, 0)
        self.assertNotEqual(result.length_formatted, "")

    def test_point_has_no_measurement(self):
        result = measure_feature(
            feature_index=2,
            feature_id="3",
            raw_geometry=self._point_geom(),
            properties={},
            source_crs_str=self.SOURCE_CRS,
        )
        self.assertEqual(result.geometry_type, "Point")
        self.assertIsNone(result.area_m2)
        self.assertIsNone(result.length_m)
        self.assertEqual(result.measurement_error, "")

    def test_geometry_collection_graceful(self):
        result = measure_feature(
            feature_index=3,
            feature_id="4",
            raw_geometry=self._geometry_collection_geom(),
            properties={},
            source_crs_str=self.SOURCE_CRS,
        )
        self.assertIsNone(result.area_m2)
        self.assertIsNone(result.length_m)
        self.assertIn("not supported", result.measurement_error)

    def test_missing_geometry_graceful(self):
        result = measure_feature(
            feature_index=4,
            feature_id="5",
            raw_geometry=None,
            properties={},
            source_crs_str=self.SOURCE_CRS,
        )
        self.assertIsNone(result.area_m2)
        self.assertIsNone(result.length_m)
        self.assertNotEqual(result.measurement_error, "")

    def test_polygon_area_uses_projected_crs(self):
        """Area must be in m², not degrees²."""
        result = measure_feature(
            feature_index=0,
            feature_id="1",
            raw_geometry=self._polygon_geom(),
            properties={},
            source_crs_str=self.SOURCE_CRS,
        )
        # 1° × 1° polygon near equator ≈ 12,000 km²
        # If we mistakenly used degrees², we'd get 1.0 (degrees²)
        self.assertGreater(result.area_m2, 1_000_000_000)  # > 1000 km²

    def test_already_projected_crs_not_reprojected_again(self):
        """If source file is already in a projected CRS, no double-projection."""
        from pyproj import CRS

        proj_crs = CRS.from_epsg(32632)  # UTM zone 32N (metres)
        result = measure_feature(
            feature_index=0,
            feature_id="1",
            raw_geometry={
                "type": "Polygon",
                "coordinates": [
                    [[0, 0], [0, 1000], [1000, 1000], [1000, 0], [0, 0]]
                ],
            },
            properties={},
            source_crs_str="EPSG:32632",
        )
        self.assertAlmostEqual(result.area_m2, 1_000_000, delta=1)  # 1 km²


# ---------------------------------------------------------------------------
# API view tests
# ---------------------------------------------------------------------------


class TestFileUploadView(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/files/"

    def test_upload_no_file_returns_400(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_upload_bad_extension_returns_400(self):
        f = io.BytesIO(b"not a geo file")
        f.name = "data.csv"
        response = self.client.post(self.url, {"file": f}, format="multipart")
        self.assertEqual(response.status_code, 400)

    def test_upload_valid_kml_returns_201(self):
        kml_bytes = _make_kml_bytes()
        f = io.BytesIO(kml_bytes)
        f.name = "test.kml"
        response = self.client.post(self.url, {"file": f}, format="multipart")
        data = response.json()
        self.assertEqual(response.status_code, 201, data)
        self.assertEqual(data["status"], "COMPLETED")
        self.assertIn("id", data)
        self.assertEqual(data["filename"], "test.kml")


class TestFileDetailView(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_get_nonexistent_returns_404(self):
        import uuid
        response = self.client.get(f"/api/files/{uuid.uuid4()}/")
        self.assertEqual(response.status_code, 404)

    def test_get_existing_returns_200(self):
        geo_file = _make_geo_file()
        response = self.client.get(f"/api/files/{geo_file.id}/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], str(geo_file.id))
        self.assertEqual(data["status"], "COMPLETED")


class TestFileMeasurementsView(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_pending_file_returns_409(self):
        geo_file = _make_geo_file(status=FileStatus.PENDING)
        response = self.client.get(f"/api/files/{geo_file.id}/measurements/")
        self.assertEqual(response.status_code, 409)

    def test_completed_file_returns_200_with_features(self):
        geo_file = _make_geo_file(status=FileStatus.COMPLETED, feature_count=1)
        FeatureMeasurement.objects.create(
            geo_file=geo_file,
            feature_index=0,
            feature_id="1",
            geometry_type="Polygon",
            geometry_geojson='{"type":"Polygon","coordinates":[]}',
            crs="EPSG:4326",
            projected_crs="EPSG:32632",
            properties={"name": "Test"},
            area_m2=1000.0,
            area_formatted="1000.0000 m²",
        )
        response = self.client.get(f"/api/files/{geo_file.id}/measurements/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["feature_count"], 1)
        self.assertEqual(len(data["features"]), 1)
        self.assertEqual(data["features"][0]["geometry_type"], "Polygon")
        self.assertAlmostEqual(data["features"][0]["area_m2"], 1000.0)
