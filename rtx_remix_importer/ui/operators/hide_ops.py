"""UI operators that hide/show captured meshes via the mod layer only.

The buttons never export geometry: they author the same
``over "<prim>" ( references = None )`` override the official RTX Remix Toolkit
writes when a captured asset is removed, so the game stops drawing the captured
mesh while the capture file itself stays untouched.

Captured geometry is defined inline on the ``mesh_<HASH>`` prototype; the
``inst_<HASH>_<N>`` prims own the reference that instantiates it.  Imported
instance objects therefore mask their instance path, which is the prim that
actually carries the capture reference.
"""

from __future__ import annotations

import os
import re

import bpy

try:
    from pxr import Tf, Usd

    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False

from ...nvidia_core import composition, constants, hide
from ...operators.export_operator import (
    invalidate_game_stage_cache,
    remix_mesh_prim_path,
    resolve_anchor_prim_path,
)

__all__ = ["HideCapturedMesh", "ShowCapturedMesh"]


def _resolve_mask_path(obj):
    """Return the captured prim that carries *obj*'s reference.

    Imported instance objects store their own ``usd_instance_path``; objects that
    only know a prototype path fall back to the ``mesh_<HASH>`` prim.
    """
    instance_path = obj.get(constants.PROP_USD_INSTANCE_PATH)
    if isinstance(instance_path, str) and re.match(constants.REGEX_INSTANCE_PATH, instance_path):
        return instance_path
    anchor = resolve_anchor_prim_path(obj)
    if not anchor:
        return None
    if re.match(constants.REGEX_INSTANCE_PATH, anchor):
        return anchor
    return remix_mesh_prim_path(anchor)


class _MeshMaskOperator(bpy.types.Operator):
    """Shared plumbing for the hide/show buttons."""

    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        if not USD_AVAILABLE:
            return False
        return bool(context.scene.remix_mod_file_path)

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _selected_meshes(context):
        """Map selected Blender objects to the prims that own their references."""
        targets = []
        skipped = []
        seen = set()
        for obj in context.selected_objects:
            if obj.type != "MESH":
                continue
            prim_path = _resolve_mask_path(obj)
            if not prim_path:
                skipped.append(obj.name)
                continue
            if prim_path in seen:
                continue
            seen.add(prim_path)
            targets.append((obj.name, prim_path))
        return targets, skipped

    @staticmethod
    def _open_mod_layer(context):
        """Open the loaded project and return ``(stage, mod_layer, mod_path)``."""
        mod_path = bpy.path.abspath(context.scene.remix_mod_file_path)
        if not mod_path or not os.path.exists(mod_path):
            raise RuntimeError(f"Mod file not found: {mod_path or '(none)'}")
        stage = Usd.Stage.Open(mod_path)
        if stage is None:
            raise RuntimeError(f"Could not open mod stage: {mod_path}")
        layer = composition.find_mod_layer(stage)
        if layer is None:
            # A mod file without layer metadata still has its own layer to edit;
            # hide._ensure_mod_layer rejects it if it turns out to be a capture.
            layer = stage.GetRootLayer()
        return stage, layer, mod_path

    def _run(self, context, apply):
        """Apply *apply* to every selected mesh and report a single summary."""
        targets, skipped = self._selected_meshes(context)
        if not targets:
            self.report(
                {"ERROR"},
                "Select at least one imported RTX Remix mesh (mesh_<HASH> or inst_<HASH> path required).",
            )
            return {"CANCELLED"}

        try:
            _stage, layer, mod_path = self._open_mod_layer(context)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        changed = 0
        unchanged = 0
        failures = []
        for object_name, mesh_path in targets:
            try:
                if apply(layer, mesh_path):
                    changed += 1
                else:
                    unchanged += 1
            except (ValueError, RuntimeError, Tf.ErrorException) as exc:
                failures.append(f"{object_name}: {exc}")

        if changed:
            try:
                layer.Save()
            except Tf.ErrorException as exc:
                self.report({"ERROR"}, f"Could not save {layer.identifier}: {exc}")
                return {"CANCELLED"}
            invalidate_game_stage_cache(mod_path)

        for failure in failures:
            print(f"[RTX Remix] {self.bl_label}: {failure}")

        if not changed and failures:
            self.report({"ERROR"}, failures[0] if len(failures) == 1 else f"{len(failures)} meshes failed")
            return {"CANCELLED"}

        summary = f"{self.bl_label}: {changed} changed, {unchanged} already in that state"
        if failures:
            summary += f", {len(failures)} failed"
        if skipped:
            summary += f", {len(skipped)} object(s) without a Remix mesh path"
        self.report({"INFO"}, summary)
        return {"FINISHED"}


class HideCapturedMesh(_MeshMaskOperator):
    """Hide selected captured meshes by masking their reference in the mod layer"""

    bl_idname = "object.rtx_remix_hide_mesh"
    bl_label = "Hide Captured Meshes"
    bl_description = (
        "Write 'references = None' for the selected captured mesh(es) into the "
        "mod layer. No geometry is exported and the capture file is not modified"
    )

    def execute(self, context):
        return self._run(context, hide.hide_mesh)


class ShowCapturedMesh(_MeshMaskOperator):
    """Restore selected captured meshes by removing their mod-layer mask"""

    bl_idname = "object.rtx_remix_show_mesh"
    bl_label = "Restore Captured Meshes"
    bl_description = (
        "Remove the 'references = None' mask the mod layer holds for the selected "
        "captured mesh(es) so the game renders them again"
    )

    def execute(self, context):
        return self._run(context, hide.unhide_mesh)
