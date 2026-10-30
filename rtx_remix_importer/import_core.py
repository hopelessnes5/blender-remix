"""Import facade for RTX Remix captures.

The add-on used to contain a single importer (now
:mod:`rtx_remix_importer.legacy_import_core`).  The direct backend lives in
:mod:`rtx_remix_importer.blender_bridge.scene_builder` and runs NVIDIA's
algorithms inside Blender with its bundled ``pxr`` -- no RTX Remix Toolkit /
Omniverse Kit process, no REST/MCP/HTTP.

Backend selection, in priority order:

1. ``REMIX_IMPORT_BACKEND`` environment variable.
2. ``scene["remix_import_backend"]`` custom property.
3. ``direct`` (NVIDIA algorithms in-process, legacy importer as a logged
   fallback if the direct path raises).

Supported values: ``direct`` (default; falls back to legacy on error),
``direct-only`` (no fallback), ``legacy`` (old importer).
"""

from __future__ import annotations

import os
import traceback

from .legacy_import_core import USDImportError
from .legacy_import_core import import_rtx_remix_usd_with_materials as _legacy_import

__all__ = [
    "BACKEND_DIRECT",
    "BACKEND_DIRECT_ONLY",
    "BACKEND_ENV_VAR",
    "BACKEND_LEGACY",
    "USDImportError",
    "get_import_backend",
    "import_rtx_remix_usd_with_materials",
]

BACKEND_ENV_VAR = "REMIX_IMPORT_BACKEND"
BACKEND_SCENE_PROP = "remix_import_backend"
BACKEND_DIRECT = "direct"
BACKEND_DIRECT_ONLY = "direct-only"
BACKEND_LEGACY = "legacy"

_ALIASES = {
    "": BACKEND_DIRECT,
    "auto": BACKEND_DIRECT,
    "native": BACKEND_DIRECT,
    "nvidia": BACKEND_DIRECT,
    "direct": BACKEND_DIRECT,
    "direct-only": BACKEND_DIRECT_ONLY,
    "direct_only": BACKEND_DIRECT_ONLY,
    "strict": BACKEND_DIRECT_ONLY,
    "legacy": BACKEND_LEGACY,
    "old": BACKEND_LEGACY,
}


def get_import_backend(context=None) -> str:
    """Resolve the configured import backend.

    Args:
        context: Optional Blender context; its scene property is consulted
            when the environment variable is unset.

    Returns:
        One of :data:`BACKEND_DIRECT`, :data:`BACKEND_DIRECT_ONLY` or
        :data:`BACKEND_LEGACY`.
    """
    value = os.environ.get(BACKEND_ENV_VAR)
    if not value and context is not None:
        scene = getattr(context, "scene", None)
        if scene is not None:
            try:
                value = scene.get(BACKEND_SCENE_PROP)
            except (KeyError, TypeError, AttributeError):
                value = None
    normalized = str(value or "").strip().lower()
    backend = _ALIASES.get(normalized)
    if backend is None:
        print(f"Unknown {BACKEND_ENV_VAR}={value!r}; using {BACKEND_DIRECT}")
        backend = BACKEND_DIRECT
    return backend


def import_rtx_remix_usd_with_materials(
    context, usd_file_path, import_materials, import_lights, scene_scale
):
    """Import an RTX Remix capture/project; see module docstring for backends.

    Keeps the legacy signature and return value:
    ``(objects, lights, cameras, message)``.
    """
    backend = get_import_backend(context)

    if backend == BACKEND_LEGACY:
        return _legacy_import(context, usd_file_path, import_materials, import_lights, scene_scale)

    try:
        from .blender_bridge.scene_builder import import_with_direct_backend

        return import_with_direct_backend(
            context, usd_file_path, import_materials, import_lights, scene_scale
        )
    except Exception as exc:
        if backend == BACKEND_DIRECT_ONLY:
            raise USDImportError(f"Direct import failed for '{usd_file_path}': {exc}") from exc
        print(f"Direct import failed for '{usd_file_path}': {exc}")
        traceback.print_exc()
        print("Falling back to the legacy importer (set REMIX_IMPORT_BACKEND=direct-only to disable)")
        return _legacy_import(context, usd_file_path, import_materials, import_lights, scene_scale)
