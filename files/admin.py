"""
files/admin.py – Register models in Django admin for easy inspection.
"""
from django.contrib import admin
from .models import GeoFile, FeatureMeasurement


@admin.register(GeoFile)
class GeoFileAdmin(admin.ModelAdmin):
    list_display = ["id", "filename", "file_type", "crs", "feature_count", "status", "uploaded_at"]
    list_filter = ["status", "file_type"]
    search_fields = ["filename", "id"]
    readonly_fields = ["id", "uploaded_at", "processed_at"]


@admin.register(FeatureMeasurement)
class FeatureMeasurementAdmin(admin.ModelAdmin):
    list_display = [
        "geo_file",
        "feature_index",
        "geometry_type",
        "crs",
        "projected_crs",
        "area_formatted",
        "length_formatted",
    ]
    list_filter = ["geometry_type"]
    search_fields = ["geo_file__filename"]
    readonly_fields = list_display
