"""Export preserves per-face geometry and material assignments."""
import os
import tempfile
import unittest
from unittest.mock import patch

import bpy
import mathutils
from pxr import Usd, UsdGeom, UsdShade
from rtx_remix_importer.operators.export_operator import write_ingested_mesh_asset, _export_remix_objects


class TestMeshMaterialSplit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.added = not hasattr(bpy.types.Scene, 'remix_custom_texture_dir')
        if cls.added:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(default='textures')

    @classmethod
    def tearDownClass(cls):
        if cls.added:
            del bpy.types.Scene.remix_custom_texture_dir

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'asset.usda')
        self.mesh = bpy.data.meshes.new('split_mesh')
        self.mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0),(1,1,0)], [], [(0,1,2),(1,3,2)])
        self.obj = bpy.data.objects.new('split_obj', self.mesh)
        bpy.context.scene.collection.objects.link(self.obj)
        self.addCleanup(bpy.data.meshes.remove, self.mesh)
        self.addCleanup(bpy.data.objects.remove, self.obj, do_unlink=True)
        self.materials = [bpy.data.materials.new('first'), bpy.data.materials.new('second')]
        for material in self.materials:
            material.use_nodes = True
            self.mesh.materials.append(material)
            self.addCleanup(bpy.data.materials.remove, material, do_unlink=True)
        self.mesh.polygons[1].material_index = 1
        uv = self.mesh.uv_layers.new()
        for i, loop in enumerate(uv.data):
            loop.uv = (i / 10, i / 20)
        bpy.context.view_layer.update()

    def run_export(self, inherit=False):
        stage = Usd.Stage.CreateNew(self.path)
        messages = []
        with patch('rtx_remix_importer.operators.export_operator.ensure_mdl_files'):
            result = write_ingested_mesh_asset(stage, self.obj, bpy.context, 'Mesh', mathutils.Matrix.Diagonal((2, 3, 4, 1)),
                                               self.materials[0], inherit, self.tmp.name, self.path, UsdGeom.Tokens.z,
                                               lambda level, message: messages.append(message))
        return stage, result, messages

    def test_two_parts_preserve_faces_uv_normals_and_bindings(self):
        before_vertices = [tuple(v.co) for v in self.mesh.vertices]
        before_uvs = [tuple(loop.uv) for loop in self.mesh.uv_layers.active.data]
        before_faces = [(tuple(poly.vertices), poly.material_index) for poly in self.mesh.polygons]
        stage, result, messages = self.run_export()
        self.assertTrue(result, messages)
        self.assertEqual([tuple(v.co) for v in self.mesh.vertices], before_vertices)
        self.assertEqual([tuple(loop.uv) for loop in self.mesh.uv_layers.active.data], before_uvs)
        self.assertEqual([(tuple(poly.vertices), poly.material_index) for poly in self.mesh.polygons], before_faces)
        stage = Usd.Stage.Open(self.path)
        parts = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
        self.assertEqual(len(parts), 2)
        bindings = []
        for i, prim in enumerate(parts):
            mesh = UsdGeom.Mesh(prim)
            self.assertEqual(list(mesh.GetFaceVertexCountsAttr().Get()), [3])
            points = mesh.GetPointsAttr().Get()
            expected = [self.mesh.vertices[v].co for v in self.mesh.polygons[i].vertices]
            for point, source in zip(points, expected):
                self.assertAlmostEqual(point[0], source[0] * 2)
                self.assertAlmostEqual(point[1], source[1] * 3)
            self.assertEqual(mesh.GetNormalsInterpolation(), UsdGeom.Tokens.faceVarying)
            normals = mesh.GetNormalsAttr().Get()
            self.assertEqual(len(normals), 3)
            for actual, loop_index in zip(normals, self.mesh.polygons[i].loop_indices):
                source = self.mesh.corner_normals[loop_index].vector
                for axis, scale in enumerate((2, 3, 4)):
                    self.assertAlmostEqual(actual[axis], source[axis] / scale, places=5)
            uvs = UsdGeom.PrimvarsAPI(prim).GetPrimvar('st').Get()
            for actual, loop_index in zip(uvs, self.mesh.polygons[i].loop_indices):
                self.assertAlmostEqual(actual[0], loop_index / 10)
                self.assertAlmostEqual(actual[1], loop_index / 20)
            material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
            self.assertTrue(material)
            bindings.append(str(material.GetPath()))
        self.assertNotEqual(*bindings)

    def test_single_and_inherit_remain_one_mesh(self):
        self.mesh.polygons[1].material_index = 0
        stage, result, messages = self.run_export()
        self.assertTrue(result, messages)
        self.assertTrue(stage.GetPrimAtPath('/ReferenceTarget/XForms/Mesh').IsA(UsdGeom.Mesh))

    def test_inherit_keeps_one_unbound_mesh(self):
        stage, result, messages = self.run_export(inherit=True)
        self.assertTrue(result, messages)
        parts = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
        self.assertEqual(len(parts), 1)
        material, _ = UsdShade.MaterialBindingAPI(parts[0]).ComputeBoundMaterial()
        self.assertFalse(material)

    def test_different_object_linked_slots_used(self):
        self.obj.material_slots[1].link = 'OBJECT'
        self.obj.material_slots[1].material = self.materials[0]
        bpy.context.view_layer.update()
        stage, result, messages = self.run_export()
        self.assertTrue(result, messages)
        self.assertEqual(len([p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]), 1)

    def test_shared_mesh_duplicate_output_name_rejected_before_file_write(self):
        second = bpy.data.objects.new('instance', self.mesh)
        bpy.context.scene.collection.objects.link(second)
        self.addCleanup(bpy.data.objects.remove, second, do_unlink=True)
        second.material_slots[1].link = 'OBJECT'
        second.material_slots[1].material = self.materials[0]
        messages = []
        class Operator:
            def report(self, level, message):
                messages.append(message)
        result = _export_remix_objects(Operator(), bpy.context, self.path, [self.obj, second], False)
        self.assertEqual(result, (False, 0, 0))
        self.assertTrue(any('same export asset name' in message for message in messages))
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_texture_name_collision_rejected_before_authoring(self):
        for material, name in zip(self.materials, ('shared.png', 'shared.jpg')):
            image = bpy.data.images.new(name, 2, 2)
            self.addCleanup(bpy.data.images.remove, image)
            nodes = material.node_tree.nodes
            texture = nodes.new('ShaderNodeTexImage')
            texture.image = image
            bsdf = next(node for node in nodes if node.type == 'BSDF_PRINCIPLED')
            material.node_tree.links.new(texture.outputs['Color'], bsdf.inputs['Base Color'])
        stage = Usd.Stage.CreateNew(self.path)
        before = stage.GetRootLayer().ExportToString()
        with self.assertRaisesRegex(ValueError, 'same export path'):
            write_ingested_mesh_asset(stage, self.obj, bpy.context, 'Mesh', mathutils.Matrix.Identity(4),
                                      self.materials[0], False, self.tmp.name, self.path, UsdGeom.Tokens.z)
        self.assertEqual(stage.GetRootLayer().ExportToString(), before)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, 'textures', 'shared.dds')))

    def test_second_material_failure_restores_layer(self):
        stage = Usd.Stage.CreateNew(self.path)
        before = stage.GetRootLayer().ExportToString()
        from rtx_remix_importer.operators import export_operator
        original = export_operator.export_material
        count = []
        def fail_second(*args, **kwargs):
            count.append(1)
            if len(count) == 2:
                raise RuntimeError('second material failure')
            return original(*args, **kwargs)
        with patch.object(export_operator, 'ensure_mdl_files'), patch.object(export_operator, 'export_material', side_effect=fail_second):
            result = write_ingested_mesh_asset(stage, self.obj, bpy.context, 'Mesh', mathutils.Matrix.Identity(4),
                                               self.materials[0], False, self.tmp.name, self.path, UsdGeom.Tokens.z, lambda *args: None)
        self.assertIsNone(result)
        self.assertEqual(stage.GetRootLayer().ExportToString(), before)
