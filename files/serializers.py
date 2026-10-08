"""
files/serializers.py
"""
from rest_framework import serializers
from .models import GeoFile, FeatureMeasurement


class GeoFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeoFile
        fields = [
            "id",
            "filename",
            "file_type",
            "feature_count",
            "crs",
            "status",
            "error_message",
            "uploaded_at",
            "processed_at",
        ]
        read_only_fields = fields


class GeoFileUploadSerializer(serializers.Serializer):
    """Used only for the upload endpoint to document the multipart body."""

    file = serializers.FileField(
        help_text="A .zip containing a Shapefile or a .kml file (max 50 MB)."
    )


class FeatureMeasurementSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeatureMeasurement
        fields = [
            "feature_index",
            "feature_id",
            "geometry_type",
            "geometry_geojson",
            "crs",
            "projected_crs",
            "properties",
            "area_m2",
            "area_formatted",
            "length_m",
            "length_formatted",
            "measurement_error",
        ]


class MeasurementsResponseSerializer(serializers.Serializer):
    """Wrapper that includes file-level context alongside the features list."""

    file_id = serializers.UUIDField()
    filename = serializers.CharField()
    crs = serializers.CharField()
    feature_count = serializers.IntegerField()
    features = FeatureMeasurementSerializer(many=True)
