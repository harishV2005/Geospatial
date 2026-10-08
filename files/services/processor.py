"""
files/services/processor.py

High-level orchestration layer that ties together:
  1. File parsing   (parsers.py)
  2. Measurement    (measurement.py)
  3. Database saves (Django ORM)

Called synchronously inside the POST /api/files/ view (no Celery needed
for this submission – see README Design Decisions for discussion of
async alternatives).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from django.db import transaction

from files.models import FeatureMeasurement, GeoFile, FileStatus
from .measurement import measure_feature
from .parsers import parse_geo_file

logger = logging.getLogger(__name__)


def process_geo_file(geo_file: GeoFile) -> None:
    """
    Parse and measure every feature in *geo_file*, persisting results to
    the database.

    The function updates ``geo_file.status`` throughout:
      PENDING → PROCESSING → COMPLETED  (or FAILED on error)

    Parameters
    ----------
    geo_file : GeoFile  (must already be saved; only pk is required)
    """
    geo_file.status = FileStatus.PROCESSING
    geo_file.save(update_fields=["status"])

    try:
        # ── 1. Parse the file ─────────────────────────────────────────────
        parse_result = parse_geo_file(geo_file.file_path, geo_file.filename)

        # Update file-level metadata
        geo_file.crs = parse_result.crs
        geo_file.feature_count = parse_result.feature_count
        geo_file.file_type = parse_result.file_type

        # ── 2. Measure features & bulk-insert ─────────────────────────────
        measurements = []
        for feat in parse_result.features:
            result = measure_feature(
                feature_index=feat.index,
                feature_id=feat.feature_id,
                raw_geometry=feat.geometry,
                properties=feat.properties,
                source_crs_str=parse_result.crs,
            )
            measurements.append(
                FeatureMeasurement(
                    geo_file=geo_file,
                    feature_index=result.feature_index,
                    feature_id=result.feature_id,
                    geometry_type=result.geometry_type,
                    geometry_geojson=result.geometry_geojson,
                    crs=result.crs,
                    projected_crs=result.projected_crs,
                    properties=result.properties,
                    area_m2=result.area_m2,
                    length_m=result.length_m,
                    area_formatted=result.area_formatted,
                    length_formatted=result.length_formatted,
                    measurement_error=result.measurement_error,
                )
            )

        with transaction.atomic():
            FeatureMeasurement.objects.bulk_create(measurements, ignore_conflicts=False)
            geo_file.status = FileStatus.COMPLETED
            geo_file.processed_at = datetime.now(timezone.utc)
            geo_file.save(
                update_fields=[
                    "status",
                    "crs",
                    "feature_count",
                    "file_type",
                    "processed_at",
                ]
            )

        logger.info(
            "Processed geo_file %s (%d features).",
            geo_file.id,
            parse_result.feature_count,
        )

    except Exception as exc:
        logger.exception("Failed to process geo_file %s: %s", geo_file.id, exc)
        geo_file.status = FileStatus.FAILED
        geo_file.error_message = str(exc)
        geo_file.save(update_fields=["status", "error_message"])
        raise
