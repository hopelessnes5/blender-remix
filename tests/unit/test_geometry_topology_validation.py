"""Reject malformed topology before building Blender data."""
import unittest

from pxr import Gf, Sdf, Usd, UsdGeom

from rtx_remix_importer.nvidia_core.geometry import extract_mesh_data


class TopologyValidation(unittest.TestCase):
    def setUp(self):
        self.stage = Usd.Stage.CreateInMemory()
        self.mesh = UsdGeom.Mesh.Define(self.stage, '/Mesh')
        self.mesh.CreatePointsAttr([Gf.Vec3f(i, 0, 0) for i in range(5)])

    def extract(self, counts, indices):
        self.mesh.CreateFaceVertexCountsAttr(counts)
        self.mesh.CreateFaceVertexIndicesAttr(indices)
        return extract_mesh_data(self.mesh.GetPrim())

    def test_negative_face_count(self):
        with self.assertRaisesRegex(ValueError, r'/Mesh: face 0 has negative vertex count -1'):
            self.extract([-1, 3], [0, 1, 2])

    def test_short_and_long_index_arrays(self):
        for indices in ([], [0, 1], [0, 1, 2, 3]):
            with self.subTest(indices=indices), self.assertRaisesRegex(ValueError, r'require 3 indices'):
                self.extract([3], indices)

    def test_out_of_bounds_point_index(self):
        for invalid in (-1, 5):
            with self.subTest(index=invalid), self.assertRaisesRegex(ValueError, f'corner 2 has point index {invalid}'):
                self.extract([3], [0, 1, invalid])

    def test_degenerate_face_keeps_later_geometry_and_uv_alignment(self):
        primvar = UsdGeom.PrimvarsAPI(self.mesh).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
        primvar.Set([Gf.Vec2f(i, i) for i in range(5)])
        data = self.extract([2, 3], [0, 1, 2, 3, 4])
        self.assertEqual(data.polygons(), [(2, 3, 4)])
        self.assertEqual(data.corner_uvs(), [(2.0, 2.0), (3.0, 3.0), (4.0, 4.0)])
