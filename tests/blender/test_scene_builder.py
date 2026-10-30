"""Blender integration tests for the direct backend."""

from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

import bpy

from rtx_remix_importer import material_utils
from rtx_remix_importer.blender_bridge.scene_builder import (
    LEGACY_DEFAULT_SCENE_SCALE,
    import_with_direct_backend,
    resolve_unit_scale,
)
from rtx_remix_importer.import_core import (
    BACKEND_DIRECT,
    BACKEND_DIRECT_ONLY,
    BACKEND_ENV_VAR,
    BACKEND_LEGACY,
    USDImportError,
    get_import_backend,
    import_rtx_remix_usd_with_materials,
)
from rtx_remix_importer.nvidia_core import composition

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "full_project"
CAPTURE = DATA_DIR / "capture.usda"
FULL_PROJECT = DATA_DIR / "full_project.usda"
INSTANCE = "/RootNode/instances/inst_6CA2F12444DEBE09_0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BlenderTestCase(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        material_utils.clear_material_cache()
        self.context = bpy.context

    def objects_of_type(self, type_name: str):
        return [obj for obj in self.context.scene.objects if obj.type == type_name]


class TestDirectImportCapture(BlenderTestCase):
    def test_capture_import(self):
        objects, lights, cameras, message = import_with_direct_backend(
            self.context, str(CAPTURE), True, True, LEGACY_DEFAULT_SCENE_SCALE
        )

        self.assertEqual(len(objects), 1)
        self.assertEqual(len(lights), 1)
        self.assertEqual(len(cameras), 1)
        self.assertIn("direct pxr backend", message)

        bl_object = next(iter(objects))
        self.assertIn(bl_object.name, self.context.scene.objects)
        self.assertEqual(bl_object["usd_instance_path"], INSTANCE)
        mesh = bl_object.data
        self.assertEqual(len(mesh.vertices), 763)
        self.assertEqual(len(mesh.polygons), 513)
        self.assertEqual(len(mesh.loops), 1539)
        self.assertEqual(mesh.uv_layers.active.name, "st")
        self.assertEqual(len(mesh.uv_layers.active.data), 1539)
        self.assertTrue(all(poly.use_smooth for poly in mesh.polygons))

        self.assertTrue(bl_object.material_slots)
        material = bl_object.material_slots[0].material
        self.assertIsNotNone(material)
        self.assertIn("8D1946B4993CE5A3", material.name)
        self.assertEqual(bl_object.material_slots[0].link, "OBJECT")

        camera = cameras[0]
        self.assertEqual(camera.type, "CAMERA")
        self.assertGreater(camera.data.lens, 0.0)
        self.assertTrue(camera.data.get("is_remix_camera"))

        self.assertAlmostEqual(self.context.scene["remix_import_unit_scale"], 0.01)
        self.assertEqual(self.context.scene["remix_import_backend"], "direct")

    def test_capture_file_is_not_modified(self):
        before = _sha256(CAPTURE)
        import_with_direct_backend(self.context, str(CAPTURE), False, False, LEGACY_DEFAULT_SCENE_SCALE)
        self.assertEqual(_sha256(CAPTURE), before)

    def test_explicit_scale_override(self):
        composition_project = composition.ProjectComposition.open(str(CAPTURE))
        try:
            self.assertEqual(resolve_unit_scale(composition_project, None), 0.01)
            self.assertEqual(resolve_unit_scale(composition_project, 0.01), 0.01)
            self.assertEqual(resolve_unit_scale(composition_project, 1.0), 1.0)
        finally:
            composition_project.close()


class TestDirectImportFullProject(BlenderTestCase):
    def test_composed_project_import(self):
        objects, lights, cameras, _message = import_with_direct_backend(
            self.context, str(FULL_PROJECT), True, True, LEGACY_DEFAULT_SCENE_SCALE
        )
        self.assertEqual(len(objects), 1)
        self.assertEqual(len(cameras), 1)

        bl_object = next(iter(objects))
        material = bl_object.material_slots[0].material
        self.assertIsNotNone(material)
        self.assertIn("8D1946B4993CE5A3", material.name)
        # The instance's /RootNode/Looks binding is valid; the prototype's
        # /Looks binding is not, so the resolver must not blindly take the
        # prototype relationship.
        self.assertEqual(bl_object["usd_prototype_path"], "/RootNode/meshes/mesh_6CA2F12444DEBE09")

        # Capture metadata (Y-up, centimetres) is authoritative for geometry.
        self.assertAlmostEqual(self.context.scene["remix_import_unit_scale"], 0.01)


class TestBackendSelection(BlenderTestCase):
    def tearDown(self):
        os.environ.pop(BACKEND_ENV_VAR, None)

    def test_default_is_direct(self):
        os.environ.pop(BACKEND_ENV_VAR, None)
        self.assertEqual(get_import_backend(None), BACKEND_DIRECT)

    def test_environment_values(self):
        os.environ[BACKEND_ENV_VAR] = "legacy"
        self.assertEqual(get_import_backend(None), BACKEND_LEGACY)
        os.environ[BACKEND_ENV_VAR] = "direct-only"
        self.assertEqual(get_import_backend(None), BACKEND_DIRECT_ONLY)
        os.environ[BACKEND_ENV_VAR] = "nonsense"
        self.assertEqual(get_import_backend(None), BACKEND_DIRECT)

    def test_scene_property(self):
        os.environ.pop(BACKEND_ENV_VAR, None)
        fake = SimpleNamespace(scene={"remix_import_backend": "legacy"})
        self.assertEqual(get_import_backend(fake), BACKEND_LEGACY)

    def test_direct_only_raises(self):
        os.environ[BACKEND_ENV_VAR] = BACKEND_DIRECT_ONLY
        with self.assertRaises(USDImportError):
            import_rtx_remix_usd_with_materials(
                self.context, str(DATA_DIR / "missing.usda"), False, False, LEGACY_DEFAULT_SCENE_SCALE
            )

    def test_legacy_backend_still_works(self):
        os.environ[BACKEND_ENV_VAR] = BACKEND_LEGACY
        objects, _lights, _cameras, _message = import_rtx_remix_usd_with_materials(
            self.context, str(CAPTURE), False, False, LEGACY_DEFAULT_SCENE_SCALE
        )
        self.assertGreaterEqual(len(objects), 1)


if __name__ == "__main__":
    unittest.main()
