"""Regression: Apply Mod places prototype lights on capture instances even when
mod.usda is opened by itself (capture is not a mod sublayer).

Run with Blender's bundled bpy/pxr, not standalone CPython.
"""

import math
import os
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

import bpy
import mathutils
from pxr import Sdf, Usd, UsdGeom, UsdLux

from rtx_remix_importer.nvidia_core import constants
from rtx_remix_importer.ui.operators.mod_loader import ModFileLoader


HASH = "ABCDEF0123456789"
PROTOTYPE = f"/RootNode/meshes/mesh_{HASH}"
INSTANCE = f"/RootNode/instances/inst_{HASH}_0"
LIGHT = PROTOTYPE + "/ref_nested/Lamp"


class TestModPrototypeLightComposition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not hasattr(bpy.types.Scene, "remix_export_scale"):
            bpy.types.Scene.remix_export_scale = bpy.props.FloatProperty(default=1.0)
            cls.added_scale_property = True
        else:
            cls.added_scale_property = False

    @classmethod
    def tearDownClass(cls):
        if cls.added_scale_property:
            del bpy.types.Scene.remix_export_scale

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="remix_light_composition_")
        self.capture_path = os.path.join(self.directory.name, "capture.usda")
        self.mod_path = os.path.join(self.directory.name, "mod.usda")
        self.scene = bpy.context.scene
        self.objects_before = set(bpy.data.objects.keys())
        self.collections_before = set(bpy.data.collections.keys())
        self.scene_values_before = {
            k: (k in self.scene, self.scene.get(k))
            for k in (constants.PROP_SOURCE_CAPTURE, "remix_import_unit_scale")
        }

        capture = Usd.Stage.CreateNew(self.capture_path)
        capture.GetRootLayer().customLayerData = {"lightspeed_layer_type": "capture"}
        UsdGeom.SetStageUpAxis(capture, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(capture, 0.01)
        UsdGeom.Xform.Define(capture, Sdf.Path(PROTOTYPE))
        # The Blender mesh is below a non-identity nested Xform, whereas the
        # replacement light lives under a different sibling ref_* branch.
        nested_mesh_path = PROTOTYPE + "/XForms/Body"
        nested = UsdGeom.Xform.Define(capture, Sdf.Path(nested_mesh_path))
        UsdGeom.XformCommonAPI(nested).SetTranslate((0.0, 50.0, 0.0))
        inst = UsdGeom.Xform.Define(capture, Sdf.Path(INSTANCE))
        inst.GetPrim().GetReferences().AddInternalReference(Sdf.Path(PROTOTYPE))
        UsdGeom.XformCommonAPI(inst).SetTranslate((100.0, 0.0, 0.0))
        self.assertTrue(capture.GetRootLayer().Save())

        mod = Usd.Stage.CreateNew(self.mod_path)
        mod.GetRootLayer().customLayerData = {"lightspeed_layer_type": "replacement"}
        mod.OverridePrim(Sdf.Path(PROTOTYPE))
        UsdGeom.Xform.Define(mod, Sdf.Path(PROTOTYPE + "/ref_nested"))
        light = UsdLux.SphereLight.Define(mod, Sdf.Path(LIGHT))
        UsdGeom.XformCommonAPI(light.GetPrim()).SetTranslate((10.0, 20.0, 30.0))
        light.GetIntensityAttr().Set(25.0)
        self.assertTrue(mod.GetRootLayer().Save())
        self.original_bytes = {
            path: Path(path).read_bytes()
            for path in (self.capture_path, self.mod_path)
        }

        mesh = bpy.data.meshes.new("RemixLightTestMesh")
        anchor = bpy.data.objects.new("RemixLightTestAnchor", mesh)
        self.scene.collection.objects.link(anchor)
        anchor[constants.PROP_USD_INSTANCE_PATH] = INSTANCE
        anchor[constants.PROP_USD_PRIM_PATH] = PROTOTYPE + "/XForms/Body"
        # Same transform that direct capture import applies: Y-up -> Z-up,
        # world metresPerUnit scaling, then the captured USD placement.
        anchor.matrix_world = (
            mathutils.Matrix.Rotation(math.pi / 2, 4, "X")
            @ mathutils.Matrix.Scale(0.01, 4)
            @ mathutils.Matrix.Translation((100.0, 50.0, 0.0))
        )
        self.anchor = anchor
        self.scene[constants.PROP_SOURCE_CAPTURE] = self.capture_path
        self.scene["remix_import_unit_scale"] = 0.01

    def tearDown(self):
        for name in set(bpy.data.objects.keys()) - self.objects_before:
            bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
        for collection in (bpy.data.meshes, bpy.data.lights):
            for datablock in list(collection):
                if datablock.users == 0:
                    collection.remove(datablock)
        for key, (was_set, value) in self.scene_values_before.items():
            if was_set:
                self.scene[key] = value
            elif key in self.scene:
                del self.scene[key]
        for collection in list(bpy.data.collections):
            if collection.name in self.collections_before:
                continue
            for parent in list(bpy.data.collections):
                if collection.name in parent.children:
                    parent.children.unlink(collection)
            if collection.name in self.scene.collection.children:
                self.scene.collection.children.unlink(collection)
            if collection.users == 0:
                bpy.data.collections.remove(collection)
        self.directory.cleanup()

    def test_mod_only_stage_maps_light_to_y_up_capture_placement(self):
        context = SimpleNamespace(scene=self.scene, collection=self.scene.collection)
        loader = ModFileLoader(context, Mock())
        loader.mod_file_path = self.mod_path
        loader.stage = Usd.Stage.Open(self.mod_path, Usd.Stage.LoadAll)
        loader.xform_cache = UsdGeom.XformCache(loader.time_code)
        loader.up_axis_is_y = True
        self.assertFalse(loader.stage.GetPrimAtPath(INSTANCE).IsValid())
        light_prim = loader.stage.GetPrimAtPath(LIGHT)
        prototype_prim = loader.stage.GetPrimAtPath(PROTOTYPE)
        self.assertTrue(light_prim.IsValid())

        # The temporary layer contains [mod, capture], so the light is now
        # mapped to an instance as in the actual Toolkit stage composition.
        loader._sync_prototype_light(light_prim, prototype_prim)
        self.assertTrue(loader._get_light_stage().GetPrimAtPath(
            INSTANCE + "/ref_nested/Lamp").IsValid())
        matches = [obj for obj in self.scene.objects
                   if obj.type == "LIGHT" and obj.get("usd_light_path") == LIGHT]
        self.assertEqual(len(matches), 1)
        light_obj = matches[0]
        self.assertIs(light_obj.parent, self.anchor)
        self.assertEqual(light_obj["usd_light_instance_path"], INSTANCE)
        self.assertAlmostEqual(light_obj.matrix_world.translation.x, 1.1, places=4)
        self.assertAlmostEqual(light_obj.matrix_world.translation.y, -0.3, places=4)
        self.assertAlmostEqual(light_obj.matrix_world.translation.z, 0.2, places=4)
        self.assertAlmostEqual(light_obj.data.energy, 25.0, places=4)

        loader._routed_light_instances.clear()
        loader._sync_prototype_light(light_prim, prototype_prim)
        self.assertEqual(len([obj for obj in self.scene.objects
                              if obj.type == "LIGHT" and obj.get("usd_light_path") == LIGHT]), 1)
        for path, data in self.original_bytes.items():
            self.assertEqual(Path(path).read_bytes(), data, "Apply Mod modified an input USD file")


if __name__ == "__main__":
    unittest.main()
