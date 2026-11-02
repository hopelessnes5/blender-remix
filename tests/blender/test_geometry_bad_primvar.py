"""A malformed primvar must not prevent import of otherwise valid geometry."""
import unittest
import bpy
from pxr import Gf, Sdf, Usd, UsdGeom
from rtx_remix_importer.blender_bridge.scene_builder import _MeshRegistry, _build_prototypes


class MalformedPrimvarImport(unittest.TestCase):
    def test_scalar_uv_does_not_drop_mesh(self):
        stage = Usd.Stage.CreateInMemory()
        mesh = UsdGeom.Mesh.Define(stage, '/RootNode/meshes/mesh_BADUV')
        mesh.CreatePointsAttr([Gf.Vec3f(0,0,0), Gf.Vec3f(1,0,0), Gf.Vec3f(0,1,0)])
        mesh.CreateFaceVertexCountsAttr([3])
        mesh.CreateFaceVertexIndicesAttr([0,1,2])
        UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st', Sdf.ValueTypeNames.FloatArray, UsdGeom.Tokens.vertex).Set([1.0, 2.0, 3.0])
        registry = _MeshRegistry()
        self.assertEqual(_build_prototypes(stage, registry), 1)
        data = registry.find(mesh.GetPrim(), str(mesh.GetPath()))[0]
        self.assertEqual(len(data.polygons), 1)
        self.assertEqual(len(data.uv_layers), 0)
        bpy.data.meshes.remove(data)
