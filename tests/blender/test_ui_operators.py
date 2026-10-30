"""End-to-end tests through the add-on's UI operators.

The direct backend is also reachable from the panels (`remix.import_capture`,
`remix.scan_capture_folder`), so these tests register the add-on and drive the
real operators instead of calling the backend functions directly.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import bpy
from pxr import Usd

import rtx_remix_importer
from rtx_remix_importer import material_utils
from rtx_remix_importer.import_core import BACKEND_ENV_VAR

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "full_project"
CAPTURE = DATA_DIR / "capture.usda"
MOD = DATA_DIR / "mod.usda"
INSTANCE_PATH = "/RootNode/instances/inst_6CA2F12444DEBE09_0"
INSTANCE_MESH = INSTANCE_PATH + "/mesh"


def _valid_mesh_child(stage: Usd.Stage):
    """Return the composed instance mesh, or ``None`` when it was masked."""
    prim = stage.GetPrimAtPath(INSTANCE_MESH)
    return prim if prim is not None and prim.IsValid() else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def setUpModule():
    rtx_remix_importer.register()


def tearDownModule():
    rtx_remix_importer.unregister()


class OperatorTestCase(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        material_utils.clear_material_cache()
        os.environ.pop(BACKEND_ENV_VAR, None)
        self.scene = bpy.context.scene
        self.scene.remix_capture_scene_scale = 0.01
        self.scene.remix_capture_import_materials = True
        self.scene.remix_capture_import_lights = True

    def tearDown(self):
        os.environ.pop(BACKEND_ENV_VAR, None)
        if "remix_import_backend" in self.scene:
            del self.scene["remix_import_backend"]


class TestImportCaptureOperator(OperatorTestCase):
    def test_import_uses_direct_backend(self):
        result = bpy.ops.remix.import_capture(filepath=str(CAPTURE))

        self.assertEqual(result, {"FINISHED"})
        objects = [obj for obj in self.scene.objects if obj.type == "MESH"]
        self.assertEqual(len(objects), 1)
        self.assertEqual(self.scene["remix_import_backend"], "direct")
        self.assertAlmostEqual(self.scene["remix_import_unit_scale"], 0.01)
        self.assertEqual(len(objects[0].data.vertices), 763)
        self.assertTrue(objects[0].material_slots)
        self.assertIsNotNone(objects[0].material_slots[0].material)

        camera_names = [obj.name for obj in self.scene.objects if obj.type == "CAMERA"]
        self.assertEqual(len(camera_names), 1)
        self.assertEqual(self.scene.remix_last_imported_camera, camera_names[0])

    def test_scene_property_switches_to_legacy(self):
        self.scene["remix_import_backend"] = "legacy"
        result = bpy.ops.remix.import_capture(filepath=str(CAPTURE))

        self.assertEqual(result, {"FINISHED"})
        self.assertGreaterEqual(len([o for o in self.scene.objects if o.type == "MESH"]), 1)
        # The direct backend records its bookkeeping; the legacy one does not.
        self.assertNotIn("remix_import_unit_scale", self.scene)

    def test_direct_only_reports_error_instead_of_falling_back(self):
        os.environ[BACKEND_ENV_VAR] = "direct-only"
        # Blender turns an operator's ``{'ERROR'}`` report into RuntimeError.
        with self.assertRaises(RuntimeError) as raised:
            bpy.ops.remix.import_capture(filepath=str(DATA_DIR / "missing.usda"))

        self.assertIn("Direct import failed", str(raised.exception))
        self.assertEqual(len([o for o in self.scene.objects if o.type == "MESH"]), 0)


class TestScanCaptureFolderOperator(OperatorTestCase):
    def test_only_captures_are_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy2(CAPTURE, Path(tmp) / "capture.usda")
            shutil.copy2(MOD, Path(tmp) / "mod.usda")
            # A plain, non-capture layer that lives in the same folder.
            (Path(tmp) / "notes.usda").write_text("#usda 1.0\n", encoding="utf-8")

            self.scene.remix_capture_folder_path = tmp
            result = bpy.ops.remix.scan_capture_folder()

            self.assertEqual(result, {"FINISHED"})
            names = [item.name for item in self.scene.remix_captures]
            self.assertEqual(names, ["capture.usda"])
            self.assertTrue(self.scene.remix_captures[0].full_path.endswith("capture.usda"))


class TestHideMeshOperators(OperatorTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "project"
        shutil.copytree(DATA_DIR, self.project)
        self.mod_path = self.project / "mod.usda"
        self.capture_hash = _sha256(self.project / "capture.usda")
        self.scene.remix_mod_file_path = str(self.mod_path)

        result = bpy.ops.remix.import_capture(filepath=str(CAPTURE))
        self.assertEqual(result, {"FINISHED"})
        self.mesh_object = next(
            obj for obj in self.scene.objects if obj.type == "MESH" and obj.get("usd_instance_path")
        )
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        self.mesh_object.select_set(True)
        bpy.context.view_layer.objects.active = self.mesh_object

    def tearDown(self):
        self.scene.remix_mod_file_path = ""
        self._tmp.cleanup()
        super().tearDown()

    def composed(self) -> Usd.Stage:
        return Usd.Stage.Open(str(self.project / "full_project.usda"))

    def test_hide_writes_mod_layer_without_exporting_geometry(self):
        mod_before = _sha256(self.mod_path)
        self.assertIsNotNone(_valid_mesh_child(self.composed()))

        result = bpy.ops.object.rtx_remix_hide_mesh()

        self.assertEqual(result, {"FINISHED"})
        text = self.mod_path.read_text(encoding="utf-8")
        self.assertIn('over "inst_6CA2F12444DEBE09_0"', text)
        self.assertIn("references = None", text)
        self.assertNotEqual(_sha256(self.mod_path), mod_before)
        self.assertEqual(_sha256(self.project / "capture.usda"), self.capture_hash)
        # No new asset/mesh file was written by hiding.
        self.assertFalse((self.project / "assets").exists())
        self.assertIsNone(_valid_mesh_child(self.composed()))

        # Hiding only authors USD: the Blender mesh itself is untouched.
        self.assertEqual(len(self.mesh_object.data.vertices), 763)

    def test_show_restores_the_capture_mesh(self):
        self.assertEqual(bpy.ops.object.rtx_remix_hide_mesh(), {"FINISHED"})
        self.assertIsNone(_valid_mesh_child(self.composed()))

        self.assertEqual(bpy.ops.object.rtx_remix_show_mesh(), {"FINISHED"})
        self.assertIsNotNone(_valid_mesh_child(self.composed()))
        self.assertNotIn("references = None", self.mod_path.read_text(encoding="utf-8"))
        self.assertEqual(_sha256(self.project / "capture.usda"), self.capture_hash)

    def test_hide_requires_a_capture_mesh_selection(self):
        camera = next(obj for obj in self.scene.objects if obj.type == "CAMERA")
        self.mesh_object.select_set(False)
        camera.select_set(True)
        bpy.context.view_layer.objects.active = camera

        with self.assertRaises(RuntimeError):
            bpy.ops.object.rtx_remix_hide_mesh()


if __name__ == "__main__":
    unittest.main()
