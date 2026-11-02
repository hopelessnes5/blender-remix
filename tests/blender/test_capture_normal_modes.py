"""Capture normal import choices preserve geometry and authored primvars."""

import unittest

import bpy
from pxr import Sdf, Usd, UsdGeom

from rtx_remix_importer.blender_bridge.mesh import create_mesh_datablock
from rtx_remix_importer.nvidia_core.geometry import extract_mesh_data


class TestCaptureNormalModes(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.stage = Usd.Stage.CreateInMemory()
        self.usd_mesh = UsdGeom.Mesh.Define(self.stage, "/mesh")
        self.usd_mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
        self.usd_mesh.CreateFaceVertexCountsAttr([3])
        self.usd_mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
        self.usd_mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)

    def _mesh(self, mode=None):
        data = extract_mesh_data(self.usd_mesh.GetPrim())
        self.assertIsNotNone(data)
        if mode is None:
            return create_mesh_datablock(data, "normal_mode_probe")
        return create_mesh_datablock(data, "normal_mode_probe", normal_mode=mode)

    def _mesh_normals(self):
        self.usd_mesh.CreateNormalsAttr([(0, 0, -1)] * 3)
        self.usd_mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)

    def _primvar_normals(self):
        primvar = UsdGeom.PrimvarsAPI(self.usd_mesh).CreatePrimvar(
            "normals", Sdf.ValueTypeNames.Normal3fArray, UsdGeom.Tokens.vertex
        )
        primvar.Set([(0, 0, -1)] * 3)

    def _assert_authored(self, mesh):
        self.assertTrue(mesh.has_custom_normals)
        self.assertTrue(mesh.polygons[0].use_smooth)
        self.assertEqual(tuple(mesh.polygons[0].vertices), (0, 1, 2))
        shading = mesh.corner_normals[mesh.polygons[0].loop_start].vector
        self.assertLess(mesh.polygons[0].normal.dot(shading), -0.99)

    def test_usd_mode_keeps_standard_normals(self):
        self._mesh_normals()
        self._assert_authored(self._mesh("USD"))

    def test_default_mode_keeps_standard_normals(self):
        self._mesh_normals()
        self._assert_authored(self._mesh())

    def test_legacy_ignores_standard_normals_and_smooths(self):
        self._mesh_normals()
        mesh = self._mesh("LEGACY")
        self.assertFalse(mesh.has_custom_normals)
        self.assertTrue(mesh.polygons[0].use_smooth)
        self.assertEqual(tuple(mesh.polygons[0].vertices), (0, 1, 2))
        shading = mesh.corner_normals[mesh.polygons[0].loop_start].vector
        self.assertGreater(mesh.polygons[0].normal.dot(shading), 0.99)

    def test_legacy_keeps_primvar_normals(self):
        self._primvar_normals()
        self._assert_authored(self._mesh("LEGACY"))

    def test_legacy_prefers_primvar_to_standard_normals(self):
        self.usd_mesh.CreateNormalsAttr([(0, 0, 1)] * 3)
        self.usd_mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        self._primvar_normals()
        self._assert_authored(self._mesh("LEGACY"))

    def test_usd_without_normals_is_flat(self):
        mesh = self._mesh("USD")
        self.assertFalse(mesh.has_custom_normals)
        self.assertFalse(mesh.polygons[0].use_smooth)

    def test_legacy_without_normals_is_smooth(self):
        mesh = self._mesh("LEGACY")
        self.assertFalse(mesh.has_custom_normals)
        self.assertTrue(mesh.polygons[0].use_smooth)
