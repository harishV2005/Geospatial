"""
files/services/parsers.py

Geospatial file parsers.

Supported formats
-----------------
  .zip  – Shapefile (zipped)  – parsed with Fiona
  .kml  – KML                 – parsed with lxml directly (converts to GeoJSON geometry)

Note: Fiona's KML/LIBKML driver is not available on all platforms (e.g. Windows
pre-built wheels). To avoid this dependency we parse KML with lxml, which is
cross-platform and reliable.

CRS strategy:
  - Shapefiles: read from the .prj sidecar via Fiona.
  - KML:        always EPSG:4326 (KML is spec'd as WGS-84).
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import fiona


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FeatureRecord:
    index: int
    feature_id: str
    geometry: Optional[Dict[str, Any]]  # GeoJSON geometry dict or None
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    crs: str                          # e.g. "EPSG:4326"
    feature_count: int
    features: List[FeatureRecord]
    file_type: str                    # "shapefile" | "kml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_crs(crs_wkt: str) -> str:
    """
    Convert a Fiona/PROJ CRS string to a clean ``"AUTHORITY:CODE"`` form.
    Falls back to the raw string if pyproj cannot parse it.
    """
    if not crs_wkt:
        return "EPSG:4326"
    try:
        from pyproj import CRS

        parsed = CRS.from_user_input(crs_wkt)
        try:
            auth, code = parsed.to_authority()
            return f"{auth}:{code}"
        except Exception:
            return parsed.to_proj4()
    except Exception:
        return crs_wkt


def _feature_id(feature) -> str:
    """Extract a string ID from a Fiona feature (may be None or int)."""
    fid = feature.get("id") or feature.get("properties", {}).get("id", "")
    return str(fid) if fid is not None else ""


def _sanitise_properties(props: Dict) -> Dict:
    """
    JSON-serialise property values that are not natively serialisable
    (e.g. fiona.ogrext.Properties objects, dates, etc.).
    """
    out = {}
    for k, v in (props or {}).items():
        try:
            json.dumps(v)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = str(v)
    return out


# ---------------------------------------------------------------------------
# Shapefile parser
# ---------------------------------------------------------------------------


def parse_shapefile(zip_path: str) -> ParseResult:
    """
    Parse a zipped Shapefile.

    Parameters
    ----------
    zip_path : Absolute path to the .zip file on disk.

    Returns
    -------
    ParseResult
    """
    # Extract to a temp directory; Fiona needs all sidecar files (.dbf, .prj…)
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmpdir)

        # Find the .shp file (may be nested in subdirs)
        shp_path = None
        for root, _, filenames in os.walk(tmpdir):
            for fname in filenames:
                if fname.lower().endswith(".shp"):
                    shp_path = os.path.join(root, fname)
                    break
            if shp_path:
                break

        if shp_path is None:
            raise ValueError("No .shp file found inside the ZIP archive.")

        features: List[FeatureRecord] = []
        crs_str = "EPSG:4326"

        with fiona.open(shp_path, "r") as src:
            if src.crs:
                crs_str = _normalise_crs(
                    src.crs.to_wkt() if hasattr(src.crs, "to_wkt") else str(src.crs)
                )

            for idx, feat in enumerate(src):
                geom = dict(feat["geometry"]) if feat["geometry"] else None
                props = _sanitise_properties(dict(feat.get("properties") or {}))
                features.append(
                    FeatureRecord(
                        index=idx,
                        feature_id=_feature_id(feat),
                        geometry=geom,
                        properties=props,
                    )
                )

    return ParseResult(
        crs=crs_str,
        feature_count=len(features),
        features=features,
        file_type="shapefile",
    )


# ---------------------------------------------------------------------------
# KML parser  (pure lxml – no LIBKML dependency)
# ---------------------------------------------------------------------------

KML_NS = "http://www.opengis.net/kml/2.2"
KML_NS_ALT = "http://earth.google.com/kml/2.1"


def _coords_to_list(text: str) -> List[List[float]]:
    """
    Parse a KML coordinates string into a list of [lon, lat] (or [lon, lat, alt]).
    e.g. "10,51,0 11,51,0" → [[10.0, 51.0, 0.0], [11.0, 51.0, 0.0]]
    """
    coords = []
    for token in text.strip().split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                coords.append([float(p) for p in parts])
            except ValueError:
                pass
    return coords


def _find_tag(element, local: str):
    """Find a child element by local name, trying both common KML namespaces."""
    for ns in (KML_NS, KML_NS_ALT, ""):
        tag = f"{{{ns}}}{local}" if ns else local
        child = element.find(tag)
        if child is not None:
            return child
    return None


def _find_all_tag(element, local: str):
    """Find all child elements by local name, trying both common KML namespaces."""
    for ns in (KML_NS, KML_NS_ALT, ""):
        tag = f"{{{ns}}}{local}" if ns else local
        children = element.findall(tag)
        if children:
            return children
    return []


def _parse_geometry(geom_elem) -> Optional[Dict[str, Any]]:
    """
    Convert a KML geometry element to a GeoJSON-like dict.
    Supports: Point, LineString, LinearRing, Polygon, MultiGeometry.
    """
    if geom_elem is None:
        return None

    # Strip namespace from tag
    tag = geom_elem.tag.split("}")[-1] if "}" in geom_elem.tag else geom_elem.tag

    if tag == "Point":
        coords_el = _find_tag(geom_elem, "coordinates")
        if coords_el is None or not coords_el.text:
            return None
        pts = _coords_to_list(coords_el.text)
        if not pts:
            return None
        return {"type": "Point", "coordinates": pts[0]}

    elif tag == "LineString":
        coords_el = _find_tag(geom_elem, "coordinates")
        if coords_el is None or not coords_el.text:
            return None
        pts = _coords_to_list(coords_el.text)
        return {"type": "LineString", "coordinates": pts}

    elif tag == "LinearRing":
        coords_el = _find_tag(geom_elem, "coordinates")
        if coords_el is None or not coords_el.text:
            return None
        pts = _coords_to_list(coords_el.text)
        # Treat as a closed LineString (no standalone LinearRing in GeoJSON)
        return {"type": "LineString", "coordinates": pts}

    elif tag == "Polygon":
        outer = _find_tag(geom_elem, "outerBoundaryIs")
        if outer is None:
            return None
        ring_el = _find_tag(outer, "LinearRing")
        if ring_el is None:
            return None
        coords_el = _find_tag(ring_el, "coordinates")
        if coords_el is None or not coords_el.text:
            return None
        exterior = _coords_to_list(coords_el.text)
        holes = []
        for inner in _find_all_tag(geom_elem, "innerBoundaryIs"):
            inner_ring = _find_tag(inner, "LinearRing")
            if inner_ring is not None:
                ic = _find_tag(inner_ring, "coordinates")
                if ic is not None and ic.text:
                    holes.append(_coords_to_list(ic.text))
        return {
            "type": "Polygon",
            "coordinates": [exterior] + holes,
        }

    elif tag == "MultiGeometry":
        geometries = []
        for child in geom_elem:
            g = _parse_geometry(child)
            if g:
                geometries.append(g)
        if not geometries:
            return None
        return {"type": "GeometryCollection", "geometries": geometries}

    return None


def _extract_placemarks(root):
    """
    Recursively yield all Placemark elements from the KML tree,
    descending into Document and Folder elements.
    """
    from lxml import etree

    tag_local = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    if tag_local == "Placemark":
        yield root
    else:
        for child in root:
            yield from _extract_placemarks(child)


def parse_kml(kml_path: str) -> ParseResult:
    """
    Parse a KML file using lxml.

    Parameters
    ----------
    kml_path : Absolute path to the .kml file on disk.

    Returns
    -------
    ParseResult  (crs is always "EPSG:4326" – KML is WGS-84)
    """
    from lxml import etree

    tree = etree.parse(kml_path)
    root = tree.getroot()

    features: List[FeatureRecord] = []

    for idx, placemark in enumerate(_extract_placemarks(root)):
        # ── Extract name/description for properties ──────────────────────
        name_el = _find_tag(placemark, "name")
        desc_el = _find_tag(placemark, "description")
        props: Dict[str, Any] = {}
        if name_el is not None and name_el.text:
            props["name"] = name_el.text.strip()
        if desc_el is not None and desc_el.text:
            props["description"] = desc_el.text.strip()

        # ── Extended data (SimpleData) ────────────────────────────────────
        extended = _find_tag(placemark, "ExtendedData")
        if extended is not None:
            for schema_data in _find_all_tag(extended, "SchemaData"):
                for simple in _find_all_tag(schema_data, "SimpleData"):
                    name_attr = simple.get("name", "")
                    if name_attr and simple.text:
                        props[name_attr] = simple.text
            for data in _find_all_tag(extended, "Data"):
                name_attr = data.get("name", "")
                val_el = _find_tag(data, "value")
                if name_attr and val_el is not None and val_el.text:
                    props[name_attr] = val_el.text

        # ── Geometry ──────────────────────────────────────────────────────
        geometry = None
        for child in placemark:
            g = _parse_geometry(child)
            if g is not None:
                geometry = g
                break

        feature_id = props.get("name", "") or str(idx)

        features.append(
            FeatureRecord(
                index=idx,
                feature_id=feature_id,
                geometry=geometry,
                properties=props,
            )
        )

    return ParseResult(
        crs="EPSG:4326",
        feature_count=len(features),
        features=features,
        file_type="kml",
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def parse_geo_file(file_path: str, original_filename: str) -> ParseResult:
    """
    Dispatch to the correct parser based on *original_filename*.

    Parameters
    ----------
    file_path         : Absolute path to the saved file on disk.
    original_filename : Original name as uploaded by the client.

    Raises
    ------
    ValueError : If the file extension is not supported.
    """
    lower = original_filename.lower()
    if lower.endswith(".zip"):
        return parse_shapefile(file_path)
    if lower.endswith(".kml"):
        return parse_kml(file_path)
    raise ValueError(
        f"Unsupported file format: '{original_filename}'. "
        "Please upload a .zip (Shapefile) or .kml file."
    )
