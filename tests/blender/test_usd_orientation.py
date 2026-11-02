"""USD topology orientation is independent of authored shading normals."""

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import bpy
from pxr import Usd, UsdGeom

from rtx_remix_importer.blender_bridge.mesh import create_mesh_datablock
from rtx_remix_importer.nvidia_core.geometry import extract_mesh_data


class TestUsdOrientation(unittest.TestCase):
    def test_polygon_mesh_without_normals_is_faceted(self):
        stage = Usd.Stage.CreateInMemory()
        mesh = UsdGeom.Mesh.Define(stage, '/Mesh')
        mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 1)])
        mesh.CreateFaceVertexCountsAttr([3, 3])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 1, 3, 2])
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        result = create_mesh_datablock(extract_mesh_data(mesh.GetPrim()), 'faceted_usd_mesh')
        try:
            self.assertTrue(all(not face.use_smooth for face in result.polygons))
        finally:
            bpy.data.meshes.remove(result)

    def test_smooth_corner_normals_preserve_authored_directions(self):
        stage = Usd.Stage.CreateInMemory()
        mesh = UsdGeom.Mesh.Define(stage, '/Mesh')
        mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 1)])
        mesh.CreateFaceVertexCountsAttr([3, 3])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 1, 3, 2])
        mesh.CreateNormalsAttr([(0, 0, -1)] * 4)
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        result = create_mesh_datablock(extract_mesh_data(mesh.GetPrim()), 'smooth_usd_normals')
        try:
            self.assertTrue(all(face.use_smooth for face in result.polygons))
            for actual in result.corner_normals:
                self.assertAlmostEqual(actual.vector.x, 0, delta=0.001)
                self.assertAlmostEqual(actual.vector.y, 0, delta=0.001)
                self.assertAlmostEqual(actual.vector.z, -1, delta=0.001)
        finally:
            bpy.data.meshes.remove(result)

    def test_winding_follows_orientation_not_authored_normals(self):
        for orientation, vertices in (('rightHanded', [0, 1, 2]), ('leftHanded', [2, 1, 0])):
            for normal in ((0, 0, 1), (0, 0, -1)):
                with self.subTest(orientation=orientation, normal=normal):
                    stage = Usd.Stage.CreateInMemory()
                    mesh = UsdGeom.Mesh.Define(stage, '/Mesh')
                    mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
                    mesh.CreateFaceVertexCountsAttr([3])
                    mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
                    mesh.CreateOrientationAttr(orientation)
                    mesh.CreateNormalsAttr([normal] * 3)
                    mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
                    result = create_mesh_datablock(extract_mesh_data(mesh.GetPrim()), 'usd_orientation')
                    try:
                        self.assertEqual(list(result.polygons[0].vertices), vertices)
                        for actual in result.corner_normals:
                            self.assertAlmostEqual(actual.vector.z, normal[2], places=4)
                    finally:
                        bpy.data.meshes.remove(result)


if __name__ == '__main__':
    unittest.main(argv=['test_usd_orientation'])
