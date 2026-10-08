"""
files/models.py

Two models:
  - GeoFile      : represents an uploaded file and its high-level metadata
  - FeatureMeasurement : one row per feature in the file, stores raw + measured values
"""
import uuid
from django.db import models


class FileStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROCESSING = "PROCESSING", "Processing"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"


class GeoFile(models.Model):
    """
    Stores metadata about an uploaded geospatial file.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    filename = models.CharField(max_length=512)
    file_type = models.CharField(max_length=20)          # 'shapefile' | 'kml'
    file_path = models.CharField(max_length=1024)        # absolute path on disk
    feature_count = models.IntegerField(null=True, blank=True)
    crs = models.CharField(max_length=64, blank=True)    # e.g. "EPSG:4326"
    status = models.CharField(
        max_length=20,
        choices=FileStatus.choices,
        default=FileStatus.PENDING,
    )
    error_message = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return f"{self.filename} ({self.status})"


class GeometryType(models.TextChoices):
    POINT = "Point", "Point"
    MULTIPOINT = "MultiPoint", "MultiPoint"
    LINESTRING = "LineString", "LineString"
    MULTILINESTRING = "MultiLineString", "MultiLineString"
    POLYGON = "Polygon", "Polygon"
    MULTIPOLYGON = "MultiPolygon", "MultiPolygon"
    GEOMETRYCOLLECTION = "GeometryCollection", "GeometryCollection"
    UNKNOWN = "Unknown", "Unknown"


class FeatureMeasurement(models.Model):
    """
    Stores per-feature data including extracted geometry, CRS, properties,
    and calculated measurements (area / length).
    """

    geo_file = models.ForeignKey(
        GeoFile,
        on_delete=models.CASCADE,
        related_name="measurements",
    )
    feature_index = models.IntegerField()
    feature_id = models.CharField(max_length=256, blank=True)
    geometry_type = models.CharField(max_length=32)

    # GeoJSON representation of the geometry (stored as text for portability)
    geometry_geojson = models.TextField(blank=True)

    # Source CRS of the file
    crs = models.CharField(max_length=64, blank=True)

    # Projected CRS used for measurement
    projected_crs = models.CharField(max_length=64, blank=True)

    # Feature properties (JSON blob)
    properties = models.JSONField(default=dict)

    # Measurements (None = not applicable for this geometry type)
    area_m2 = models.FloatField(null=True, blank=True)       # m²
    length_m = models.FloatField(null=True, blank=True)      # m

    # Human-friendly formatted values
    area_formatted = models.CharField(max_length=64, blank=True)
    length_formatted = models.CharField(max_length=64, blank=True)

    # Error if measurement failed for this specific feature
    measurement_error = models.TextField(blank=True)

    class Meta:
        ordering = ["geo_file", "feature_index"]
        unique_together = [("geo_file", "feature_index")]

    def __str__(self) -> str:
        return f"Feature {self.feature_index} of {self.geo_file_id}"
