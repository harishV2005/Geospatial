"""
files/services/crs_utils.py

Utilities for CRS detection and automatic projection selection.

Strategy
--------
We use pyproj's `CRS.from_authority()` together with the geometry's
bounding box centroid to pick the most suitable UTM zone or local
projected CRS.  The algorithm:

  1. Parse the source CRS from any string that pyproj understands
     (EPSG code, WKT, PROJ string, etc.).
  2. If the CRS is already projected (i.e. axes are in metres), return
     it as-is.
  3. Otherwise compute the centroid longitude/latitude of the geometry
     bounds and select the corresponding UTM zone (WGS-84 based).
  4. Return the target CRS object so the caller can reproject.

This is a deliberate "good enough" heuristic that handles the vast
majority of real-world files without requiring a geodatabase lookup.
"""

from __future__ import annotations

import math
from typing import Optional

from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry


def parse_crs(crs_string: str) -> Optional[CRS]:
    """
    Parse *crs_string* and return a ``pyproj.CRS`` object, or ``None``
    if parsing fails.
    """
    if not crs_string:
        return None
    try:
        return CRS.from_user_input(crs_string)
    except Exception:
        return None


def get_utm_crs(lon: float, lat: float) -> CRS:
    """
    Return the WGS-84 UTM zone CRS for a given longitude / latitude.

    Parameters
    ----------
    lon : float  Longitude in degrees (−180 … 180)
    lat : float  Latitude in degrees  (−90  … 90)

    Returns
    -------
    pyproj.CRS  A projected CRS whose units are metres.
    """
    # UTM zone number: 1..60
    zone = int((lon + 180) / 6) % 60 + 1
    hemisphere = "north" if lat >= 0 else "south"
    # pyproj supports the "UTM" projection via EPSG codes:
    #   EPSG:326xx  – northern hemisphere
    #   EPSG:327xx  – southern hemisphere
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return CRS.from_epsg(epsg)


def select_projected_crs(source_crs: CRS, geometry: BaseGeometry) -> CRS:
    """
    Return a suitable projected CRS for *geometry* given its *source_crs*.

    If *source_crs* is already projected, it is returned unchanged.
    Otherwise a UTM zone is chosen based on the geometry's bounding box
    centroid (reprojected to WGS-84 first if necessary).
    """
    if source_crs.is_projected:
        return source_crs

    # For geographic CRS: determine centroid in WGS-84 degrees.
    bounds = geometry.bounds  # (minx, miny, maxx, maxy)
    if all(v == 0 for v in bounds):
        # Empty / degenerate geometry → fall back to UTM zone 33N
        return CRS.from_epsg(32633)

    cx = (bounds[0] + bounds[2]) / 2.0  # centroid longitude
    cy = (bounds[1] + bounds[3]) / 2.0  # centroid latitude

    # If the source CRS is not WGS-84, reproject the centroid to WGS-84
    wgs84 = CRS.from_epsg(4326)
    if not source_crs.equals(wgs84):
        try:
            t = Transformer.from_crs(source_crs, wgs84, always_xy=True)
            cx, cy = t.transform(cx, cy)
        except Exception:
            pass  # keep the raw centroid; UTM selection will be approximate

    # Clamp to valid degree ranges
    cx = max(-180.0, min(180.0, cx))
    cy = max(-90.0, min(90.0, cy))

    return get_utm_crs(cx, cy)


def build_transformer(source_crs: CRS, target_crs: CRS) -> Transformer:
    """
    Build a ``pyproj.Transformer`` from *source_crs* to *target_crs*.
    ``always_xy=True`` ensures consistent (lon, lat) / (x, y) ordering.
    """
    return Transformer.from_crs(source_crs, target_crs, always_xy=True)


def crs_to_string(crs: CRS) -> str:
    """
    Return a human-readable authority string (e.g. ``"EPSG:4326"``).
    Falls back to the PROJ string if no authority is available.
    """
    try:
        auth, code = crs.to_authority()
        return f"{auth}:{code}"
    except Exception:
        return crs.to_proj4()
