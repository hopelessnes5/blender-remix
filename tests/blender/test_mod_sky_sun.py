"""Regression for sky_<HASH> Blender meshes with mod-owned DistantLights.

Run in Blender's Python (bpy + pxr):
  blender -b --python-expr "import unittest; unittest.main(module='tests.blender.test_mod_sky_sun')"

A capture sky object may have NO ``usd_instance_path`` and be called
``sky_<HASH>``, while mod.usda records its sun under ``mesh_<HASH>``.
Both direct and ref_ asset lights must still be materialized as SUN.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

import bpy
from pxr import Sdf, Usd, UsdGeom, UsdLux

from rtx_remix_importer.nvidia_core import constants  # corrected below on packaging
from rtx_remix_importer.ui.operators.mod_loader import ModFileLoader

HASH = '5E68DDDA519FEA31'
PROTOTYPE = f'/RootNode/meshes/mesh_{HASH}'
SUN_DIRECT = PROTOTYPE + '/light_17d87578'
REF = PROTOTYPE + '/ref_1234567890abcdef1234567890abcdef'
SUN_NESTED = REF + '/light_bf922cef'


class TestSkyDistantLights(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.added_export_scale = not hasattr(bpy.types.Scene, 'remix_export_scale')
        if cls.added_export_scale:
            bpy.types.Scene.remix_export_scale = bpy.props.FloatProperty(default=1.0)

    @classmethod
    def tearDownClass(cls):
        if cls.added_export_scale:
            del bpy.types.Scene.remix_export_scale

    def setUp(self):
        self.dir = TemporaryDirectory(prefix='remix_sky_sun_')
        root = Path(self.dir.name)
        self.capture_path = str(root / 'capture.usda')
        self.mod_path = str(root / 'mod.usda')
        self.asset_path = str(root / 'sun_asset.usda')
        self.scene = bpy.context.scene
        self.original_objects = set(bpy.data.objects.keys())
        self.original_collections = set(bpy.data.collections.keys())
        self.old_props = {
            key: (key in self.scene, self.scene.get(key))
            for key in (constants.PROP_SOURCE_CAPTURE, 'remix_import_unit_scale')
        }

        capture = Usd.Stage.CreateNew(self.capture_path)
        capture.GetRootLayer().customLayerData = {'lightspeed_layer_type': 'capture'}
        UsdGeom.Xform.Define(capture, Sdf.Path(PROTOTYPE))
        UsdGeom.SetStageUpAxis(capture, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(capture, 0.01)
        self.assertTrue(capture.GetRootLayer().Save())

        asset = Usd.Stage.CreateNew(self.asset_path)
        target = UsdGeom.Xform.Define(asset, Sdf.Path('/ReferenceTarget'))
        asset.SetDefaultPrim(target.GetPrim())
        UsdLux.DistantLight.Define(asset, Sdf.Path('/ReferenceTarget/light_bf922cef'))\
            .GetIntensityAttr().Set(11.0)
        self.assertTrue(asset.GetRootLayer().Save())

        mod = Usd.Stage.CreateNew(self.mod_path)
        mod.OverridePrim(Sdf.Path(PROTOTYPE))
        UsdLux.DistantLight.Define(mod, Sdf.Path(SUN_DIRECT)).GetIntensityAttr().Set(22.0)
        ref = UsdGeom.Xform.Define(mod, Sdf.Path(REF)).GetPrim()
        ref.GetReferences().AddReference(self.asset_path)
        ref.CreateAttribute('IsRemixRef', Sdf.ValueTypeNames.Bool, custom=True).Set(True)
        self.assertTrue(mod.GetRootLayer().Save())

        mesh = bpy.data.meshes.new('sky_' + HASH + '_data')
        mesh.from_pydata(((0, 0, 0), (1, 0, 0), (0, 1, 0)), [], ((0, 1, 2),))
        mesh.update()
        uv = mesh.uv_layers.new(name='st')
        uv.data[0].uv = (0., 0.)
        uv.data[1].uv = (1., 0.)
        uv.data[2].uv = (0., 1.)
        self.sky = bpy.data.objects.new('sky_' + HASH, mesh)
        self.scene.collection.objects.link(self.sky)
        self.sky_data = mesh
        # Intentionally NO usd_instance_path or usd_prim_path -- this is the
        # legacy capture naming case that previously failed to link sky suns.
        self.scene[constants.PROP_SOURCE_CAPTURE] = self.capture_path
        self.scene['remix_import_unit_scale'] = 0.01
        self.input_bytes = {x: Path(x).read_bytes() for x in
                            (self.mod_path, self.capture_path, self.asset_path)}

    def tearDown(self):
        for name in set(bpy.data.objects.keys()) - self.original_objects:
            bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
        for store in (bpy.data.lights, bpy.data.meshes):
            for data in list(store):
                if data.users == 0:
                    store.remove(data)
        for name in set(bpy.data.collections.keys()) - self.original_collections:
            coll = bpy.data.collections[name]
            for parent in list(bpy.data.collections):
                if coll.name in parent.children:
                    parent.children.unlink(coll)
            if coll.name in self.scene.collection.children:
                self.scene.collection.children.unlink(coll)
            if coll.users == 0:
                bpy.data.collections.remove(coll)
        for k, (had, value) in self.old_props.items():
            if had:
                self.scene[k] = value
            elif k in self.scene:
                del self.scene[k]
        self.dir.cleanup()

    def test_sky_suns_created_once_from_direct_and_reference(self):
        loader = ModFileLoader(SimpleNamespace(scene=self.scene, collection=self.scene.collection), Mock())
        loader.stage = Usd.Stage.Open(self.mod_path, Usd.Stage.LoadAll)
        loader.mod_file_path = self.mod_path
        loader.xform_cache = UsdGeom.XformCache(loader.time_code)
        loader._build_object_map()
        layers = loader._get_layers_with_changes()
        changed, _ = loader._get_changed_prims(layers)
        self.assertIn(SUN_DIRECT, changed)
        self.assertIn(REF, changed)
        self.assertIn(PROTOTYPE, loader._mod_authored_prototype_paths)
        for path in sorted(changed):
            prim = loader.stage.GetPrimAtPath(path)
            if prim and prim.IsValid():
                loader._process_prim(prim)
        lights = [obj for obj in self.scene.objects if obj.type == 'LIGHT'
                  and obj.get('usd_light_path') in {SUN_DIRECT, SUN_NESTED}]
        self.assertEqual({obj.get('usd_light_path') for obj in lights}, {SUN_DIRECT, SUN_NESTED})
        self.assertTrue(all(obj.data.type == 'SUN' for obj in lights))
        self.assertTrue(all(obj.get('usd_light_unanchored') for obj in lights))
        self.assertIs(self.sky.data, self.sky_data)
        self.assertEqual(len(self.sky.data.uv_layers), 1)
        self.assertFalse(self.sky.hide_viewport)

        loader._routed_light_instances.clear()
        for path in sorted(changed):
            prim = loader.stage.GetPrimAtPath(path)
            if prim and prim.IsValid():
                loader._process_prim(prim)
        new_lights = [obj for obj in self.scene.objects if obj.type == 'LIGHT'
                      and obj.get('usd_light_path') in {SUN_DIRECT, SUN_NESTED}]
        self.assertEqual(len(new_lights), 2)
        for path, contents in self.input_bytes.items():
            self.assertEqual(Path(path).read_bytes(), contents)


if __name__ == '__main__':
    unittest.main()
