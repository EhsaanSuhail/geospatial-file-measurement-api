"""Status and type values shared by the services, the database models and the API."""

from enum import StrEnum


class FileFormat(StrEnum):
    KML = "KML"
    SHAPEFILE = "SHAPEFILE"


class FileStatus(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeasurementType(StrEnum):
    AREA = "AREA"
    LENGTH = "LENGTH"
    NONE = "NONE"  # the geometry type has no measurement (points) or none is supported


class MeasurementStatus(StrEnum):
    CALCULATED = "CALCULATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"  # points: no measurement is required
    UNSUPPORTED = "UNSUPPORTED"  # e.g. GeometryCollection
    FAILED = "FAILED"  # measurable type, but no trustworthy value (invalid geometry, CRS, ...)
