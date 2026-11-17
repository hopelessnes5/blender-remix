"""A replacement mesh must inherit the captured mesh's material.

The Toolkit authors no material binding on the replacement asset, so the
binding on the capture's ``mesh_<HASH>`` ancestor survives. This is the
default mesh-export behaviour; exporting a new material is opt-in.
"""
import os
import tempfile
import unittest

import bpy
import mathutils
from pxr import Usd, UsdGeom, UsdShade

from rtx_remix_importer.operators.export_operator import (
    author_mesh_replacement,
    write_ingested_mesh_asset,
)


class InheritOriginalMaterialTests(unittest.TestCase):
    HASH = 'ABCDEF0123456789'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project_root = self.temp.name
        mesh = bpy.data.meshes.new('InheritMesh')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        material = bpy.data.materials.new('InheritMaterial')
        material.use_nodes = True
        mesh.materials.append(material)
        obj = bpy.data.objects.new('InheritObject', mesh)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        self.addCleanup(bpy.data.meshes.remove, mesh)
        self.addCleanup(bpy.data.materials.remove, material, do_unlink=True)
        self.addCleanup(bpy.data.objects.remove, obj, do_unlink=True)
        self.obj, self.material = obj, material

    def test_replacement_inherits_capture_material(self):
        asset_path = os.path.join(self.project_root, 'replacement.usda')
        asset_stage = Usd.Stage.CreateNew(asset_path)
        result = write_ingested_mesh_asset(
            asset_stage, self.obj, bpy.context, 'Replacement',
            mathutils.Matrix.Identity(4), self.material, True,
            self.project_root, asset_path, UsdGeom.Tokens.z)
        self.assertTrue(result, result)
        asset_stage = Usd.Stage.Open(asset_path)
        asset_mesh = next(p for p in asset_stage.Traverse() if p.IsA(UsdGeom.Mesh))
        bound, _ = UsdShade.MaterialBindingAPI(asset_mesh).ComputeBoundMaterial()
        self.assertFalse(bound, 'the replacement asset must not bind a material of its own')

        mod_stage = Usd.Stage.CreateNew(os.path.join(self.project_root, 'mod.usda'))
        captured_material = UsdShade.Material.Define(
            mod_stage, f'/RootNode/Looks/mat_{self.HASH}')
        captured_mesh = mod_stage.DefinePrim(f'/RootNode/mesh_{self.HASH}', 'Xform')
        UsdShade.MaterialBindingAPI(captured_mesh).Bind(captured_material)
        holder_path = author_mesh_replacement(
            mod_stage, str(captured_mesh.GetPath()), asset_path,
            lambda *_: None, hide_original=True)
        self.assertTrue(holder_path)

        replacement_mesh = mod_stage.GetPrimAtPath(f'{holder_path}/XForms/Replacement')
        self.assertTrue(replacement_mesh and replacement_mesh.IsA(UsdGeom.Mesh))
        inherited, _ = UsdShade.MaterialBindingAPI(replacement_mesh).ComputeBoundMaterial()
        self.assertTrue(inherited, 'the replacement must resolve the captured binding')
        self.assertEqual(str(inherited.GetPath()), str(captured_material.GetPath()))


if __name__ == '__main__':
    unittest.main()
