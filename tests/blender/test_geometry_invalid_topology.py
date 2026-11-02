"""Invalid topology aborts facade import, rolls back and never uses legacy."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import bpy
from pxr import Gf, Usd, UsdGeom
from rtx_remix_importer.import_core import USDImportError, import_rtx_remix_usd_with_materials


class InvalidTopologyImport(unittest.TestCase):
    def test_bad_prototype_rolls_back_and_never_falls_back(self):
        for backend in ('direct', 'direct-only'):
            with self.subTest(backend=backend):
                stage = Usd.Stage.CreateInMemory()
                root = '/RootNode/meshes/'
                for name, counts in (('mesh_A_VALID', [3]), ('mesh_Z_BAD', [-1, 3])):
                    mesh = UsdGeom.Mesh.Define(stage, root + name)
                    mesh.CreatePointsAttr([Gf.Vec3f(0,0,0), Gf.Vec3f(1,0,0), Gf.Vec3f(0,1,0)])
                    mesh.CreateFaceVertexCountsAttr(counts)
                    mesh.CreateFaceVertexIndicesAttr([0,1,2])
                comp = SimpleNamespace(stage=stage, up_axis_is_y=False, meters_per_unit=1.0,
                                       capture_layer=None, capture_directory='', close=lambda: None)
                before = (set(bpy.data.objects.keys()), set(bpy.data.meshes.keys()), set(bpy.data.collections.keys()))
                with patch('rtx_remix_importer.import_core.get_import_backend', return_value=backend), \
                     patch('rtx_remix_importer.import_core._legacy_import') as legacy, \
                     patch('rtx_remix_importer.blender_bridge.scene_builder.ProjectComposition.open', return_value=comp):
                    with self.assertRaisesRegex(USDImportError, 'negative vertex count -1'):
                        import_rtx_remix_usd_with_materials(bpy.context, 'capture.usda', False, False, 1.0)
                    legacy.assert_not_called()
                after = (set(bpy.data.objects.keys()), set(bpy.data.meshes.keys()), set(bpy.data.collections.keys()))
                self.assertEqual(before, after)
