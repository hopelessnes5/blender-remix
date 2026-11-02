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


class _DirectImportCheckpoint:
    """Remember existing Blender IDs so a failed direct import can be undone.

    The import runs synchronously on Blender's main thread. Existing IDs are
    never removed; newly created data with an external user is kept.
    """

    _ID_COLLECTIONS = ('objects', 'collections', 'meshes', 'lights', 'cameras', 'materials', 'node_groups', 'images')
    _SCENE_KEYS = ('source_capture_path', 'project_id', 'remix_import_backend', 'remix_import_unit_scale')

    def __init__(self, context):
        import bpy
        from . import material_utils, texture_loader

        self._context = context
        self._existing = {
            name: {item.as_pointer() for item in getattr(bpy.data, name)}
            for name in self._ID_COLLECTIONS
        }
        self._scene_values = {key: context.scene[key] for key in self._SCENE_KEYS if key in context.scene}
        self._active_collection = context.view_layer.active_layer_collection
        self._caches = (
            (material_utils._material_cache, dict(material_utils._material_cache)),
            (material_utils._global_material_cache, dict(material_utils._global_material_cache)),
            (texture_loader._loaded_textures, dict(texture_loader._loaded_textures)),
        )

    def rollback(self):
        import bpy

        scene = self._context.scene
        for key in self._SCENE_KEYS:
            if key in self._scene_values:
                scene[key] = self._scene_values[key]
            elif key in scene:
                del scene[key]
        self._context.view_layer.active_layer_collection = self._active_collection
        for cache, original in self._caches:
            cache.clear()
            cache.update(original)

        # Remove new objects first so their mesh/material references are
        # released, then collections and orphaned import-created datablocks.
        for name in self._ID_COLLECTIONS:
            collection = getattr(bpy.data, name)
            for item in list(collection):
                if item.as_pointer() in self._existing[name]:
                    continue
                if name in ('objects', 'collections'):
                    collection.remove(item, do_unlink=True)
                elif item.users == 0:
                    collection.remove(item)


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

    checkpoint = _DirectImportCheckpoint(context)
    try:
        from .blender_bridge.scene_builder import import_with_direct_backend

        return import_with_direct_backend(
            context, usd_file_path, import_materials, import_lights, scene_scale
        )
    except Exception as exc:
        try:
            checkpoint.rollback()
        except Exception as rollback_exc:
            raise USDImportError(
                f"Direct import failed for '{usd_file_path}' and rollback failed: {rollback_exc}"
            ) from exc
        if backend == BACKEND_DIRECT_ONLY:
            raise USDImportError(f"Direct import failed for '{usd_file_path}': {exc}") from exc
        print(f"Direct import failed for '{usd_file_path}': {exc}")
        traceback.print_exc()
        print("Falling back to the legacy importer (set REMIX_IMPORT_BACKEND=direct-only to disable)")
        return _legacy_import(context, usd_file_path, import_materials, import_lights, scene_scale)
