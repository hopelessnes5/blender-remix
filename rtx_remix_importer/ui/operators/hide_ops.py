"""UI operators that hide/show captured meshes via the mod layer only.

The buttons never export geometry: they author the same
``over "<prim>" ( references = None )`` override the official RTX Remix Toolkit
writes when a captured asset is removed, so the game stops drawing the captured
mesh while the capture file itself stays untouched.

The operation targets the captured ``mesh_<HASH>`` prototype, so all of its
instances are addressed without changing their references or transforms.
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
    """Find the mesh prototype, preferring the captured prototype metadata."""
    candidates = [obj.get(constants.PROP_USD_PROTOTYPE_PATH),
                  obj.get(constants.PROP_USD_PRIM_PATH)]
    if obj.data:
        candidates.append(obj.data.get(constants.PROP_USD_PRIM_PATH))
    candidates.extend((resolve_anchor_prim_path(obj), obj.get(constants.PROP_USD_INSTANCE_PATH)))
    for path in candidates:
        mesh_path = remix_mesh_prim_path(path) if isinstance(path, str) and path else None
        if mesh_path:
            return mesh_path
    return None


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
        """Map selected Blender objects to distinct mesh prototypes."""
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

    def _run(self, context, apply, restore_legacy_instances=False):
        """Apply *apply* to every selected mesh and report a single summary."""
        targets, skipped = self._selected_meshes(context)
        if not targets:
            self.report(
                {"ERROR"},
                "Select at least one imported RTX Remix mesh (mesh_<HASH> or inst_<HASH> path required).",
            )
            return {"CANCELLED"}

        if restore_legacy_instances:
            seen = {path for _, path in targets}
            for obj in context.selected_objects:
                if obj.type != 'MESH' or not _resolve_mask_path(obj):
                    continue
                path = obj.get(constants.PROP_USD_INSTANCE_PATH)
                if isinstance(path, str) and re.match(constants.REGEX_INSTANCE_PATH, path) and path not in seen:
                    targets.append((obj.name, path))
                    seen.add(path)

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
        "Mask the selected mesh prototypes in the mod layer; all instances of "
        "each mesh are affected. The capture file is not modified"
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
        return self._run(context, hide.unhide_mesh, restore_legacy_instances=True)
