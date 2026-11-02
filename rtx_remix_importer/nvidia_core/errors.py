"""Errors shared by the direct importer and its facade."""


class InvalidTopologyError(ValueError):
    """A mesh topology error that must not fall back to a lossy importer."""


class UnsupportedGeometryError(ValueError):
    """A mesh prototype cannot be imported without losing geometry."""
