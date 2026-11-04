"""Audit A16: a mod authored by this add-on imports back with its geometry.

An ingested asset composes the Mesh several levels below the prototype
(``mesh_<HASH>/ref_x/XForms/<name>``).  The direct import must find it and
place the object on the full transform chain: prototype holders for the
standalone fallback, the mapped subtree under an instance.  The prototype
container's own transform is a reference opinion on the instance placement
and is therefore not folded (USD semantics).
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bpy
from pxr import Gf, Sdf, Usd, UsdGeom

from rtx_remix_importer.import_core import import_rtx_remix_usd_with_materials


def _author_triangle(stage, path):
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(
        [Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0), Gf.Vec3f(0, 1, 0)])
    mesh.CreateFaceVertexCountsAttr([3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
    return mesh


def _place(prim, translation):
    prim.CreateAttribute('xformOp:transform', Sdf.ValueTypeNames.Matrix4d).Set(
        Gf.Matrix4d(1.0).SetTranslate(Gf.Vec3d(*translation)))
    prim.CreateAttribute('xformOpOrder', Sdf.ValueTypeNames.TokenArray).Set(
        ['xformOp:transform'])


class NestedPrototypeRoundtrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not hasattr(bpy.types.Scene, 'remix_custom_texture_dir'):
            cls.added_property = True
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(
                default='rtx-remix/textures')
        else:
            cls.added_property = False

    @classmethod
    def tearDownClass(cls):
        if cls.added_property:
            del bpy.types.Scene.remix_custom_texture_dir

    def _cleanup(self, objects):
        for obj in objects:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None and data.users == 0:
                bpy.data.meshes.remove(data)

    def _ingested_asset_stage(self, directory, depth_transform=None):
        """Write an ingested asset the way ``_write_ingested_mesh_asset_impl`` does."""
        asset_path = os.path.join(directory, 'asset.usda')
        layer = Sdf.Layer.CreateNew(asset_path)
        stage = Usd.Stage.Open(layer)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        layer.defaultPrim = 'ReferenceTarget'
        ref_target = UsdGeom.Xform.Define(stage, '/ReferenceTarget').GetPrim()
        Usd.ModelAPI(ref_target).SetKind('group')
        xforms = UsdGeom.Xform.Define(stage, '/ReferenceTarget/XForms').GetPrim()
        Usd.ModelAPI(xforms).SetKind('group')
        if depth_transform is not None:
            _place(xforms, depth_transform)
        _author_triangle(stage, '/ReferenceTarget/XForms/PartA')
        stage.Save()
        return asset_path

    def _mod_path(self, directory, asset_path, with_instance, deep_holder):
        """Author a replacement/new-mesh layout and save it as a mod file."""
        mod_path = os.path.join(directory, 'mod.usda')
        layer = Sdf.Layer.CreateNew(mod_path)
        stage = Usd.Stage.Open(layer)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        proto = UsdGeom.Xform.Define(stage, '/RootNode/meshes/mesh_6CA2F12444DEBE09').GetPrim()
        Usd.ModelAPI(proto).SetKind('assembly')
        if deep_holder:
            _place(proto, (10, 2, 0))
            holder = UsdGeom.Xform.Define(stage, proto.GetPath().AppendChild('ref_x')).GetPrim()
            holder.GetReferences().AddReference(asset_path)
        else:
            _author_triangle(stage, proto.GetPath().AppendPath('ref_x'))
        if with_instance:
            inst = UsdGeom.Xform.Define(stage, '/RootNode/instances/inst_6CA2F12444DEBE09_0').GetPrim()
            _place(inst, (5, 0, 0))
            inst.GetReferences().AddInternalReference('/RootNode/meshes/mesh_6CA2F12444DEBE09')
        stage.Save()
        return mod_path

    def _import(self, mod_path):
        layer = Sdf.Layer.FindOrOpen(mod_path)
        stage = Usd.Stage.Open(layer)
        comp = SimpleNamespace(stage=stage, up_axis_is_y=False, meters_per_unit=1.0,
                               capture_layer=None, capture_directory='',
                               close=lambda: None)
        with patch('rtx_remix_importer.import_core.get_import_backend', return_value='direct'), \
                patch('rtx_remix_importer.import_core._legacy_import') as legacy, \
                patch('rtx_remix_importer.blender_bridge.scene_builder.ProjectComposition.open',
                      return_value=comp):
            result = import_rtx_remix_usd_with_materials(
                bpy.context, mod_path, False, False, 1.0)
        legacy.assert_not_called()
        return sorted(result[0])

    def test_deep_asset_geometry_imports_at_the_mapped_chain(self):
        """Instance placement evaluates the mapped instance -> holder -> mesh chain."""
        with tempfile.TemporaryDirectory() as directory:
            asset_path = self._ingested_asset_stage(directory, depth_transform=(2, 0, 0))
            mod_path = self._mod_path(directory, asset_path, with_instance=True,
                                      deep_holder=True)
            objects = self._import(mod_path)
            try:
                self.assertEqual(len(objects), 1)
                self.assertEqual(len(objects[0].data.vertices), 3)
                # instance (5,0,0) + XForms (2,0,0); the prototype container
                # transform (10,2,0) is overridden by the instance placement.
                translation = objects[0].matrix_world.to_translation()
                for got, expected in zip(translation, (7.0, 0.0, 0.0)):
                    self.assertAlmostEqual(got, expected, places=6)
                self.assertEqual(
                    objects[0].get('usd_prim_path'),
                    '/RootNode/meshes/mesh_6CA2F12444DEBE09')
            finally:
                self._cleanup(objects)

    def test_prototype_without_instances_places_the_full_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            asset_path = self._ingested_asset_stage(directory, depth_transform=(2, 0, 0))
            mod_path = self._mod_path(directory, asset_path, with_instance=False,
                                      deep_holder=True)
            objects = self._import(mod_path)
            try:
                self.assertEqual(len(objects), 1)
                translation = objects[0].matrix_world.to_translation()
                for got, expected in zip(translation, (12.0, 2.0, 0.0)):
                    self.assertAlmostEqual(got, expected, places=6)
            finally:
                self._cleanup(objects)

    def test_capture_layout_placement_is_unchanged(self):
        """A Mesh directly under the prototype stays on the instance matrix."""
        with tempfile.TemporaryDirectory() as directory:
            mod_path = self._mod_path(directory, None, with_instance=True,
                                      deep_holder=False)
            objects = self._import(mod_path)
            try:
                self.assertEqual(len(objects), 1)
                translation = objects[0].matrix_world.to_translation()
                for got, expected in zip(translation, (5.0, 0.0, 0.0)):
                    self.assertAlmostEqual(got, expected, places=6)
            finally:
                self._cleanup(objects)


if __name__ == '__main__':
    unittest.main()
