"""Primvars must resolve completely and have the expected shape."""
import math
import unittest
from pxr import Gf, Sdf, Usd, UsdGeom
from rtx_remix_importer.nvidia_core.geometry import InterpolatedAttribute, extract_mesh_data


class PrimvarValidation(unittest.TestCase):
    def setUp(self):
        self.stage = Usd.Stage.CreateInMemory()
        self.mesh = UsdGeom.Mesh.Define(self.stage, '/Mesh')
        self.mesh.CreatePointsAttr([Gf.Vec3f(0,0,0), Gf.Vec3f(1,0,0), Gf.Vec3f(0,1,0)])
        self.mesh.CreateFaceVertexCountsAttr([3])
        self.mesh.CreateFaceVertexIndicesAttr([0,1,2])

    def test_scalar_uv_and_normals_are_not_accepted(self):
        api = UsdGeom.PrimvarsAPI(self.mesh)
        for name in ('st', 'normals'):
            api.CreatePrimvar(name, Sdf.ValueTypeNames.FloatArray, UsdGeom.Tokens.vertex).Set([1.0, 2.0, 3.0])
        result = extract_mesh_data(self.mesh.GetPrim())
        self.assertIsNone(result.corner_uvs())
        self.assertIsNone(result.corner_normals())

    def test_short_and_nonfinite_values_rejected_without_partial_result(self):
        mesh = extract_mesh_data(self.mesh.GetPrim())
        for bad in ((1.0,), (float('nan'), 0.0), (float('inf'), 0.0), (-float('inf'), 0.0)):
            with self.subTest(bad=bad):
                values = ((0.0, 1.0), bad, (0.0, 1.0))
                self.assertIsNone(mesh.corner_values(InterpolatedAttribute('vertex', values), 2))

    def test_unknown_interpolation_is_not_constant(self):
        mesh = extract_mesh_data(self.mesh.GetPrim())
        self.assertIsNone(mesh.corner_values(InterpolatedAttribute('instance', ((1.0, 2.0),)), 2))

    def test_indexed_constant_and_uniform_resolve(self):
        mesh = extract_mesh_data(self.mesh.GetPrim())
        for interpolation in ('constant', 'uniform'):
            with self.subTest(interpolation=interpolation):
                attr = InterpolatedAttribute(interpolation, ((1.0, 2.0), (3.0, 4.0)), (1,))
                self.assertEqual(mesh.corner_values(attr, 2), [(3.0, 4.0)] * 3)

    def test_valid_high_dynamic_range_uv_is_not_clamped(self):
        mesh = extract_mesh_data(self.mesh.GetPrim())
        uv = (-12345.0, 1e30)
        self.assertEqual(mesh.corner_values(InterpolatedAttribute('constant', (uv,)), 2), [uv] * 3)

    def test_extra_components_are_ignored_as_before(self):
        mesh = extract_mesh_data(self.mesh.GetPrim())
        uv = (1.0, 2.0, 3.0)
        self.assertEqual(mesh.corner_values(InterpolatedAttribute('constant', (uv,)), 2), [(1.0, 2.0)] * 3)
