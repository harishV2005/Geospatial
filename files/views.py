"""
files/views.py

Three API views:
  POST   /api/files/                 → FileUploadView
  GET    /api/files/{id}/            → FileDetailView
  GET    /api/files/{id}/measurements/ → FileMeasurementsView
"""

from __future__ import annotations

import os
import uuid
import logging
from pathlib import Path

from django.conf import settings
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiExample
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import GeoFile, FileStatus
from .serializers import (
    GeoFileSerializer,
    GeoFileUploadSerializer,
    FeatureMeasurementSerializer,
    MeasurementsResponseSerializer,
)
from .services.processor import process_geo_file

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".zip", ".kml"}


def _save_uploaded_file(uploaded_file) -> str:
    """
    Persist the in-memory upload to disk under MEDIA_ROOT.
    Returns the absolute path to the saved file.
    """
    upload_dir = Path(settings.MEDIA_ROOT)
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(uploaded_file.name).suffix.lower()
    unique_name = f"{uuid.uuid4()}{ext}"
    dest_path = upload_dir / unique_name

    with open(dest_path, "wb") as f:
        for chunk in uploaded_file.chunks():
            f.write(chunk)

    return str(dest_path)


class FileUploadView(APIView):
    """
    Upload a geospatial file (.zip Shapefile or .kml) for processing.
    """

    parser_classes = [MultiPartParser]

    @extend_schema(
        request=GeoFileUploadSerializer,
        responses={
            201: GeoFileSerializer,
            400: OpenApiResponse(description="Validation error (bad file type, etc.)"),
            422: OpenApiResponse(description="Processing error (malformed file)"),
        },
        summary="Upload and process a geospatial file",
        description=(
            "Accepts a `.zip` containing a Shapefile or a `.kml` file. "
            "The file is processed synchronously; the response includes "
            "the file metadata and processing status."
        ),
        examples=[
            OpenApiExample(
                "Success",
                value={
                    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "filename": "survey.kml",
                    "file_type": "kml",
                    "feature_count": 12,
                    "crs": "EPSG:4326",
                    "status": "COMPLETED",
                    "error_message": "",
                    "uploaded_at": "2024-01-15T10:30:00Z",
                    "processed_at": "2024-01-15T10:30:02Z",
                },
                response_only=True,
                status_codes=["201"],
            )
        ],
    )
    def post(self, request: Request) -> Response:
        if "file" not in request.FILES:
            return Response(
                {"error": "No file provided. Send the file as multipart/form-data with key 'file'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        uploaded_file = request.FILES["file"]
        original_name: str = uploaded_file.name
        ext = Path(original_name).suffix.lower()

        if ext not in ALLOWED_EXTENSIONS:
            return Response(
                {
                    "error": (
                        f"Unsupported file extension '{ext}'. "
                        f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Save file to disk ────────────────────────────────────────────
        try:
            file_path = _save_uploaded_file(uploaded_file)
        except OSError as exc:
            logger.exception("Could not save uploaded file: %s", exc)
            return Response(
                {"error": "Failed to save the uploaded file."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # ── Create DB record ─────────────────────────────────────────────
        geo_file = GeoFile.objects.create(
            filename=original_name,
            file_path=file_path,
            file_type=ext.lstrip("."),   # preliminary; overwritten by processor
            status=FileStatus.PENDING,
        )

        # ── Process synchronously ────────────────────────────────────────
        try:
            process_geo_file(geo_file)
        except Exception as exc:
            # Status is already FAILED in the DB (set by processor)
            geo_file.refresh_from_db()
            return Response(
                GeoFileSerializer(geo_file).data,
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        geo_file.refresh_from_db()
        return Response(
            GeoFileSerializer(geo_file).data,
            status=status.HTTP_201_CREATED,
        )


class FileDetailView(APIView):
    """
    Retrieve metadata for a previously uploaded file.
    """

    @extend_schema(
        responses={200: GeoFileSerializer, 404: OpenApiResponse(description="Not found")},
        summary="Get file metadata",
        description="Returns status, CRS, feature count, and other metadata for a processed file.",
    )
    def get(self, request: Request, pk: str) -> Response:
        geo_file = get_object_or_404(GeoFile, pk=pk)
        return Response(GeoFileSerializer(geo_file).data)


class FileMeasurementsView(APIView):
    """
    Retrieve per-feature measurements for a processed file.
    """

    @extend_schema(
        responses={
            200: MeasurementsResponseSerializer,
            404: OpenApiResponse(description="Not found"),
            409: OpenApiResponse(description="File not yet processed"),
        },
        summary="Get feature measurements",
        description=(
            "Returns a list of features with geometry type, CRS, properties, "
            "and calculated measurements (area for polygons, length for linestrings). "
            "Points are included but have no measurement values."
        ),
    )
    def get(self, request: Request, pk: str) -> Response:
        geo_file = get_object_or_404(GeoFile, pk=pk)

        if geo_file.status == FileStatus.PENDING:
            return Response(
                {"error": "File is still pending processing."},
                status=status.HTTP_409_CONFLICT,
            )

        if geo_file.status == FileStatus.PROCESSING:
            return Response(
                {"error": "File is currently being processed. Please retry shortly."},
                status=status.HTTP_409_CONFLICT,
            )

        features = geo_file.measurements.all()
        feature_data = FeatureMeasurementSerializer(features, many=True).data

        response_data = {
            "file_id": str(geo_file.id),
            "filename": geo_file.filename,
            "crs": geo_file.crs,
            "feature_count": geo_file.feature_count,
            "features": feature_data,
        }
        return Response(response_data)
