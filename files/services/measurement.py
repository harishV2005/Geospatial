"""
files/services/measurement.py

Measurement calculations for Shapely geometries.

Supported geometry types
------------------------
  Polygon / MultiPolygon        → area (m²)
  LineString / MultiLineString  → length (m)
  Point / MultiPoint            → no measurement (None)
  GeometryCollection            → no measurement (None) – handled gracefully
  Unknown / unsupported         → no measurement (None)

All measurements are performed after reprojection to a suitable
projected CRS (see crs_utils.select_projected_crs).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from pyproj import CRS
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from .crs_utils import (
    build_transformer,
    crs_to_string,
    parse_crs,
    select_projected_crs,
)

# Geometry types that support measurement
_POLYGON_TYPES = {"Polygon", "MultiPolygon"}
_LINE_TYPES = {"LineString", "MultiLineString"}
_POINT_TYPES = {"Point", "MultiPoint"}
_UNSUPPORTED_TYPES = {"GeometryCollection"}


@dataclass
class MeasurementResult:
    """Result of measuring a single feature."""

    feature_index: int
    feature_id: str
    geometry_type: str
    geometry_geojson: str
    crs: str
    projected_crs: str
    properties: Dict[str, Any] = field(default_factory=dict)
    area_m2: Optional[float] = None
    length_m: Optional[float] = None
    area_formatted: str = ""
    length_formatted: str = ""
    measurement_error: str = ""


def _format_area(area_m2: float) -> str:
    """Return a human-friendly area string (m², km², or ha)."""
    if area_m2 >= 1_000_000:
        return f"{area_m2 / 1_000_000:.4f} km²"
    if area_m2 >= 10_000:
        return f"{area_m2 / 10_000:.4f} ha"
    return f"{area_m2:.4f} m²"


def _format_length(length_m: float) -> str:
    """Return a human-friendly length string (m or km)."""
    if length_m >= 1000:
        return f"{length_m / 1000:.4f} km"
    return f"{length_m:.4f} m"


def _reproject(geom: BaseGeometry, source_crs: CRS, target_crs: CRS) -> BaseGeometry:
    """Reproject *geom* from *source_crs* to *target_crs*."""
    transformer = build_transformer(source_crs, target_crs)
    return transform(transformer.transform, geom)


def measure_feature(
    feature_index: int,
    feature_id: str,
    raw_geometry: Dict[str, Any],
    properties: Dict[str, Any],
    source_crs_str: str,
) -> MeasurementResult:
    """
    Measure a single feature.

    Parameters
    ----------
    feature_index   : zero-based index of the feature in the file
    feature_id      : string ID from the source file (may be empty)
    raw_geometry    : GeoJSON-like geometry dict (type + coordinates)
    properties      : attribute dictionary
    source_crs_str  : CRS string as read from the file (e.g. "EPSG:4326")

    Returns
    -------
    MeasurementResult with area / length filled in where applicable.
    """
    result = MeasurementResult(
        feature_index=feature_index,
        feature_id=feature_id,
        geometry_type="",
        geometry_geojson="",
        crs=source_crs_str,
        projected_crs="",
        properties=properties or {},
    )

    if raw_geometry is None:
        result.geometry_type = "Unknown"
        result.measurement_error = "Feature has no geometry."
        return result

    # ── 1. Parse geometry ─────────────────────────────────────────────────
    try:
        geom: BaseGeometry = shape(raw_geometry)
    except Exception as exc:
        result.geometry_type = raw_geometry.get("type", "Unknown")
        result.geometry_geojson = json.dumps(raw_geometry)
        result.measurement_error = f"Could not parse geometry: {exc}"
        return result

    result.geometry_type = geom.geom_type
    result.geometry_geojson = json.dumps(mapping(geom))

    # ── 2. Parse source CRS ───────────────────────────────────────────────
    source_crs = parse_crs(source_crs_str)
    if source_crs is None:
        # Default to WGS-84 if CRS is missing / unparseable
        from pyproj import CRS as _CRS

        source_crs = _CRS.from_epsg(4326)
        result.crs = "EPSG:4326 (assumed)"

    # ── 3. Skip unsupported types ─────────────────────────────────────────
    if geom.geom_type in _POINT_TYPES:
        result.projected_crs = crs_to_string(source_crs)
        return result  # Points: no measurement required

    if geom.geom_type in _UNSUPPORTED_TYPES:
        result.projected_crs = crs_to_string(source_crs)
        result.measurement_error = (
            f"Geometry type '{geom.geom_type}' is not supported for measurement."
        )
        return result

    # ── 4. Select projected CRS & reproject ──────────────────────────────
    try:
        proj_crs = select_projected_crs(source_crs, geom)
        result.projected_crs = crs_to_string(proj_crs)
        projected_geom = _reproject(geom, source_crs, proj_crs)
    except Exception as exc:
        result.measurement_error = f"CRS reprojection failed: {exc}"
        return result

    # ── 5. Calculate measurements ─────────────────────────────────────────
    try:
        if geom.geom_type in _POLYGON_TYPES:
            area = projected_geom.area
            result.area_m2 = area
            result.area_formatted = _format_area(area)

        elif geom.geom_type in _LINE_TYPES:
            length = projected_geom.length
            result.length_m = length
            result.length_formatted = _format_length(length)

        else:
            result.measurement_error = (
                f"Geometry type '{geom.geom_type}' is not supported for measurement."
            )
    except Exception as exc:
        result.measurement_error = f"Measurement calculation failed: {exc}"

    return result
