"""Regression tests: Blender instances share datablocks; authored overrides split.

Run with Blender's bundled bpy and pxr:
  blender -b --python-expr "import unittest; unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('tests/blender', pattern='test_shared_prototype_datablocks.py'))"
"""
import math
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import bpy
import mathutils
from pxr import Sdf, Usd, UsdGeom, UsdLux

from rtx_remix_importer.light_utils import import_lights_from_usd
from rtx_remix_importer.mod_apply_utils import create_new_blender_light_from_mod
from rtx_remix_importer.nvidia_core import constants
from rtx_remix_importer.ui.operators.mod_loader import ModFileLoader

HASH = 'ABCDEF0123456789'
PROTO = f'/RootNode/meshes/mesh_{HASH}'
INST0 = f'/RootNode/instances/inst_{HASH}_0'
INST1 = f'/RootNode/instances/inst_{HASH}_1'
LIGHT = PROTO + '/ref_nested/Lamp'
MESH = PROTO + '/XForms/Body'


class TestSharedPrototypeDatablocks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.injected_scale = not hasattr(bpy.types.Scene, 'remix_export_scale')
        if cls.injected_scale:
            bpy.types.Scene.remix_export_scale = bpy.props.FloatProperty(default=0.01)

    @classmethod
    def tearDownClass(cls):
        if cls.injected_scale:
            del bpy.types.Scene.remix_export_scale

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='remix_shared_data_')
        self.capture_path = os.path.join(self.temp.name, 'capture.usda')
        self.mod_path = os.path.join(self.temp.name, 'mod.usda')
        self.obj_names = set(bpy.data.objects.keys())
        self.col_names = set(bpy.data.collections.keys())
        self.scene = bpy.context.scene
        self.old_capture = self.scene.get(constants.PROP_SOURCE_CAPTURE)
        self.was_capture = constants.PROP_SOURCE_CAPTURE in self.scene

        capture = Usd.Stage.CreateNew(self.capture_path)
        capture.GetRootLayer().customLayerData = {'lightspeed_layer_type': 'capture'}
        UsdGeom.SetStageUpAxis(capture, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(capture, 0.01)
        UsdGeom.Xform.Define(capture, PROTO)
        UsdGeom.Xform.Define(capture, PROTO + '/XForms')
        mesh = UsdGeom.Mesh.Define(capture, MESH)
        mesh.CreatePointsAttr([(0, 0, 0), (10, 0, 0), (0, 10, 0)])
        mesh.CreateFaceVertexCountsAttr([3])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
        for i, inst_path in enumerate((INST0, INST1)):
            inst = UsdGeom.Xform.Define(capture, inst_path)
            inst.GetPrim().GetReferences().AddInternalReference(PROTO)
            UsdGeom.XformCommonAPI(inst).SetTranslate((100 + 100 * i, 0, 0))
        capture.GetRootLayer().Save()

        mod = Usd.Stage.CreateNew(self.mod_path)
        mod.GetRootLayer().customLayerData = {'lightspeed_layer_type': 'replacement'}
        mod.OverridePrim(PROTO)
        UsdGeom.Xform.Define(mod, PROTO + '/ref_nested')
        light = UsdLux.SphereLight.Define(mod, LIGHT)
        UsdGeom.XformCommonAPI(light).SetTranslate((5, 0, 0))
        light.CreateIntensityAttr(15.0)
        # Real Remix captures can author raw shaping attrs without applying
        # UsdLux.ShapingAPI. This must still become a Blender Spot light.
        light.GetPrim().CreateAttribute('inputs:shaping:cone:angle', Sdf.ValueTypeNames.Float).Set(30.0)
        light.GetPrim().CreateAttribute('inputs:shaping:cone:softness', Sdf.ValueTypeNames.Float).Set(0.2)
        mod.GetRootLayer().Save()

        self.scene[constants.PROP_SOURCE_CAPTURE] = self.capture_path
        self.mod = Usd.Stage.Open(self.mod_path, Usd.Stage.LoadAll)
        self.original_files = {
            path: Path(path).read_bytes() for path in (self.capture_path, self.mod_path)
        }

        for inst, x in ((INST0, 1.0), (INST1, 2.0)):
            mesh_data = bpy.data.meshes.new('SharedLightAnchor')
            obj = bpy.data.objects.new('SharedLightAnchor', mesh_data)
            self.scene.collection.objects.link(obj)
            obj[constants.PROP_USD_INSTANCE_PATH] = inst
            obj[constants.PROP_USD_PROTOTYPE_PATH] = PROTO
            obj[constants.PROP_USD_PRIM_PATH] = MESH
            obj.location.x = x

    def make_loader(self):
        context = SimpleNamespace(scene=self.scene, collection=self.scene.collection)
        loader = ModFileLoader(context, Mock())
        loader.mod_file_path = self.mod_path
        loader.stage = self.mod
        loader.xform_cache = UsdGeom.XformCache(loader.time_code)
        loader.up_axis_is_y = False
        return loader

    def lights(self):
        return {o.get('usd_light_instance_path'): o for o in self.scene.objects
                if o.type == 'LIGHT' and o.get('usd_light_path') == LIGHT}

    def test_point_export_analyzer_does_not_author_cone(self):
        from rtx_remix_importer.export_utils import LightExporter
        point = bpy.data.lights.new('UnshapedPoint', 'POINT')
        try:
            typ, attrs, shaping, extent = LightExporter(self.temp.name)._analyze_light(point)
            self.assertEqual(typ, 'SphereLight')
            self.assertEqual(shaping, {})
        finally:
            bpy.data.lights.remove(point)

    def test_sphere_with_raw_shaping_is_spot(self):
        source = self.mod.GetPrimAtPath(LIGHT)
        obj = create_new_blender_light_from_mod(source, Usd.TimeCode.Default(), 0.01, Mock())
        self.assertEqual(obj.data.type, 'SPOT')
        self.assertAlmostEqual(obj.data.spot_size, math.radians(60.0))
        self.assertAlmostEqual(obj.data.spot_blend, 0.2)
        bpy.data.objects.remove(obj, do_unlink=True)

    def test_two_instanced_lights_share_one_blender_datablock(self):
        loader = self.make_loader()
        loader._sync_prototype_light(self.mod.GetPrimAtPath(LIGHT), self.mod.GetPrimAtPath(PROTO))
        lights = self.lights()
        self.assertEqual(set(lights), {INST0, INST1})
        self.assertIs(lights[INST0].data, lights[INST1].data)
        self.assertEqual(lights[INST0].data.type, 'SPOT')
        self.assertAlmostEqual(lights[INST0].matrix_world.translation.x, 1.05, places=3)
        self.assertAlmostEqual(lights[INST1].matrix_world.translation.x, 2.05, places=3)
        lights[INST0].data.energy = 123.0
        self.assertEqual(lights[INST1].data.energy, 123.0)

        # Repair previously imported independent IDs on a second Apply.
        old = lights[INST1].data.copy()
        lights[INST1].data = old
        self.assertIsNot(lights[INST0].data, lights[INST1].data)
        loader._routed_light_instances.clear()
        loader._sync_prototype_light(self.mod.GetPrimAtPath(LIGHT), self.mod.GetPrimAtPath(PROTO))
        self.assertIs(lights[INST0].data, lights[INST1].data)

    def test_instance_authored_light_override_stays_independent(self):
        loader = self.make_loader()
        composed = loader._get_light_stage()
        with Usd.EditContext(composed, composed.GetSessionLayer()):
            override = composed.OverridePrim(INST1 + '/ref_nested/Lamp')
            override.CreateAttribute('inputs:intensity', Sdf.ValueTypeNames.Float).Set(57.0)
        loader._sync_prototype_light(self.mod.GetPrimAtPath(LIGHT), self.mod.GetPrimAtPath(PROTO))
        lights = self.lights()
        self.assertIsNot(lights[INST0].data, lights[INST1].data)
        self.assertNotEqual(lights[INST0].data.energy, lights[INST1].data.energy)

    def test_new_mesh_placements_share_prototype_mesh_data(self):
        loader = self.make_loader()
        composed = loader._get_light_stage()
        loader.stage = composed
        loader.xform_cache = UsdGeom.XformCache(loader.time_code)
        first = loader._materialize_new_placement(composed.GetPrimAtPath(INST0), INST0, 'first')
        second = loader._materialize_new_placement(composed.GetPrimAtPath(INST1), INST1, 'second')
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        one, two = first[0][1], second[0][1]
        self.assertIs(one.data, two.data)
        one.data.vertices[0].co.x = 7.0
        self.assertEqual(two.data.vertices[0].co.x, 7.0)

    def test_direct_import_shares_light_ids(self):
        loader = self.make_loader()
        composed = loader._get_light_stage()
        collection = bpy.data.collections.new('DirectSharedLightTest')
        self.scene.collection.children.link(collection)
        lights = import_lights_from_usd(composed, collection, 0.01, UsdGeom.Tokens.z)
        self.assertEqual(len(lights), 2)
        self.assertIs(lights[0].data, lights[1].data)
        self.assertTrue(all(o.data.type == 'SPOT' for o in lights))
        self.assertEqual({o.get('usd_light_path') for o in lights}, {LIGHT})
        self.assertEqual({o.get('usd_light_instance_path') for o in lights}, {INST0, INST1})

    def tearDown(self):
        for path, data in self.original_files.items():
            self.assertEqual(Path(path).read_bytes(), data)
        for name in set(bpy.data.objects.keys()) - self.obj_names:
            bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
        for datablocks in (bpy.data.meshes, bpy.data.lights):
            for data in tuple(datablocks):
                if data.users == 0:
                    datablocks.remove(data)
        for name in set(bpy.data.collections.keys()) - self.col_names:
            collection = bpy.data.collections.get(name)
            if collection:
                for parent in bpy.data.collections:
                    if collection.name in parent.children:
                        parent.children.unlink(collection)
                if collection.name in self.scene.collection.children:
                    self.scene.collection.children.unlink(collection)
                if collection.users == 0:
                    bpy.data.collections.remove(collection)
        if self.was_capture:
            self.scene[constants.PROP_SOURCE_CAPTURE] = self.old_capture
        elif constants.PROP_SOURCE_CAPTURE in self.scene:
            del self.scene[constants.PROP_SOURCE_CAPTURE]
        self.temp.cleanup()


if __name__ == '__main__':
    unittest.main()
