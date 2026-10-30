"""Tests for geometry extraction (n-gons, left-handed winding, primvars)."""

from __future__ import annotations

import unittest
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom

from rtx_remix_importer.nvidia_core import geometry

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "full_project"
CAPTURE = DATA_DIR / "capture.usda"
MESH_PATH = "/RootNode/meshes/mesh_6CA2F12444DEBE09/mesh"


class TestFixtureMesh(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage = Usd.Stage.Open(str(CAPTURE))
        prim = cls.stage.GetPrimAtPath(MESH_PATH)
        cls.mesh_prim = prim
        cls.data = geometry.extract_mesh_data(prim)
        cls.usd_mesh = UsdGeom.Mesh(prim)

    def test_counts(self):
        self.assertIsNotNone(self.data)
        self.assertEqual(len(self.data.points), 763)
        self.assertEqual(len(self.data.face_vertex_counts), 513)
        self.assertEqual(self.data.total_corners, 1539)
        self.assertEqual(self.data.orientation, "leftHanded")

    def test_left_handed_faces_are_reversed(self):
        indices = list(self.usd_mesh.GetFaceVertexIndicesAttr().Get())
        first = self.data.polygons()[0]
        self.assertEqual(first, tuple(reversed(indices[:3])))
        self.assertTrue(all(len(poly) == 3 for poly in self.data.polygons()))

    def test_vertex_uvs_follow_corner_order(self):
        uvs = self.data.corner_uvs()
        self.assertIsNotNone(uvs)
        self.assertEqual(len(uvs), self.data.total_corners)
        values = list(UsdGeom.PrimvarsAPI(self.mesh_prim).GetPrimvar("st").Get())
        indices = list(self.usd_mesh.GetFaceVertexIndicesAttr().Get())
        # The first triangle is reversed, so its first loop uses the UV of the
        # original third corner.
        expected = tuple(float(component) for component in values[indices[2]])
        self.assertEqual(uvs[0], expected)

    def test_missing_normals_return_none(self):
        self.assertIsNone(self.data.corner_normals())


class TestNgonsAndPrimvarIndices(unittest.TestCase):
    def setUp(self):
        layer = Sdf.Layer.CreateAnonymous("ngon.usda")
        self.stage = Usd.Stage.Open(layer)
        mesh = UsdGeom.Mesh.Define(self.stage, "/Mesh")
        points = [(float(i), float(i), 0.0) for i in range(11)]
        mesh.CreatePointsAttr(points)
        mesh.CreateFaceVertexCountsAttr([4, 5, 2])
        # 0..3 quad, 4..8 pentagon, 9..10 degenerate (skipped).
        mesh.CreateFaceVertexIndicesAttr(list(range(11)))
        mesh.CreateOrientationAttr(UsdGeom.Tokens.leftHanded)

        primvar = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying
        )
        primvar.Set([(float(i), 0.5) for i in range(11)])
        primvar.SetIndices(list(range(11)))
        self.data = geometry.extract_mesh_data(mesh.GetPrim())

    def test_ngons_are_preserved_and_degenerate_face_skipped(self):
        self.assertEqual(self.data.face_vertex_counts, (4, 5))
        self.assertEqual(self.data.face_ids, (0, 1))
        self.assertEqual(self.data.face_corner_offsets, (0, 4))
        self.assertEqual(self.data.original_corner_count, 11)
        self.assertEqual(len(self.data.polygons()[0]), 4)
        self.assertEqual(len(self.data.polygons()[1]), 5)

    def test_left_handed_winding(self):
        self.assertEqual(self.data.polygons()[0], (3, 2, 1, 0))
        self.assertEqual(self.data.polygons()[1], (8, 7, 6, 5, 4))

    def test_face_varying_uvs_are_reordered(self):
        uvs = self.data.corner_uvs()
        self.assertEqual(
            uvs[:4],
            [(3.0, 0.5), (2.0, 0.5), (1.0, 0.5), (0.0, 0.5)],
        )
        self.assertEqual(
            uvs[4:9],
            [(8.0, 0.5), (7.0, 0.5), (6.0, 0.5), (5.0, 0.5), (4.0, 0.5)],
        )

    def test_uniform_primvar_uses_original_face_index(self):
        primvar = UsdGeom.PrimvarsAPI(self.stage.GetPrimAtPath("/Mesh")).CreatePrimvar(
            "normals", Sdf.ValueTypeNames.Normal3fArray, UsdGeom.Tokens.uniform
        )
        primvar.Set([(0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)])
        data = geometry.extract_mesh_data(self.stage.GetPrimAtPath("/Mesh"))
        normals = data.corner_normals()
        self.assertIsNotNone(normals)
        self.assertTrue(all(normal == (0.0, 0.0, 1.0) for normal in normals[:4]))
        self.assertTrue(all(normal == (0.0, 1.0, 0.0) for normal in normals[4:9]))


class TestMatrixConversion(unittest.TestCase):
    def test_row_vector_translation_becomes_column(self):
        matrix = Gf.Matrix4d().SetTranslate(Gf.Vec3d(1.0, 2.0, 3.0))
        converted = geometry.matrix_usd_to_blender(matrix)
        self.assertEqual(converted[0][3], 1.0)
        self.assertEqual(converted[1][3], 2.0)
        self.assertEqual(converted[2][3], 3.0)
        self.assertEqual(converted[3][3], 1.0)


class TestFindMeshPrim(unittest.TestCase):
    def test_mesh_direct_and_nested(self):
        stage = Usd.Stage.Open(Sdf.Layer.CreateAnonymous("find.usda"))
        container = stage.DefinePrim("/RootNode/meshes/mesh_0123456789ABCDEF", "Xform")
        mesh = UsdGeom.Mesh.Define(stage, container.GetPath().AppendChild("mesh"))
        self.assertEqual(geometry.find_mesh_prim(container), mesh.GetPrim())
        self.assertEqual(geometry.find_mesh_prim(mesh.GetPrim()), mesh.GetPrim())

    def test_missing_mesh(self):
        stage = Usd.Stage.Open(Sdf.Layer.CreateAnonymous("find.usda"))
        container = stage.DefinePrim("/RootNode/meshes/mesh_0123456789ABCDEF", "Xform")
        self.assertIsNone(geometry.find_mesh_prim(container))
        self.assertIsNone(geometry.find_mesh_prim(Usd.Prim()))


if __name__ == "__main__":
    unittest.main()
