"""Reject unsupported multipart prototypes without a lossy fallback."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import bpy
from pxr import Gf, Usd, UsdGeom
from rtx_remix_importer.import_core import USDImportError, import_rtx_remix_usd_with_materials


class MultipartFailClosed(unittest.TestCase):
    def test_multiple_sibling_or_nested_meshes_cancel_and_rollback(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                stage = Usd.Stage.CreateInMemory()
                good = UsdGeom.Mesh.Define(stage, '/RootNode/meshes/mesh_A_VALID')
                good.CreatePointsAttr([Gf.Vec3f(0,0,0), Gf.Vec3f(1,0,0), Gf.Vec3f(0,1,0)])
                good.CreateFaceVertexCountsAttr([3]); good.CreateFaceVertexIndicesAttr([0,1,2])
                root = '/RootNode/meshes/mesh_Z_MULTIPLE'
                UsdGeom.Xform.Define(stage, root)
                UsdGeom.Mesh.Define(stage, root + '/A')
                UsdGeom.Mesh.Define(stage, root + ('/Group/B' if nested else '/B'))
                comp = SimpleNamespace(stage=stage, up_axis_is_y=False, meters_per_unit=1.0,
                                       capture_layer=None, capture_directory='', close=lambda: None)
                before = (set(bpy.data.objects.keys()), set(bpy.data.meshes.keys()), set(bpy.data.collections.keys()))
                with patch('rtx_remix_importer.import_core.get_import_backend', return_value='direct'), \
                     patch('rtx_remix_importer.import_core._legacy_import') as legacy, \
                     patch('rtx_remix_importer.blender_bridge.scene_builder.ProjectComposition.open', return_value=comp):
                    with self.assertRaisesRegex(USDImportError, 'Multiple meshes.*not supported'):
                        import_rtx_remix_usd_with_materials(bpy.context, 'multipart.usda', False, False, 1.0)
                    legacy.assert_not_called()
                after = (set(bpy.data.objects.keys()), set(bpy.data.meshes.keys()), set(bpy.data.collections.keys()))
                self.assertEqual(before, after)
