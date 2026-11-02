"""Texture preflight accounts for all evaluated face materials."""
import os
import tempfile
import types
import unittest
from unittest import mock

import bpy
import mathutils
from pxr import Usd, UsdGeom
from rtx_remix_importer.operators import export_operator as exp


class MultiTexturePreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.added = not hasattr(bpy.types.Scene, 'remix_custom_texture_dir')
        if cls.added:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(default='rtx-remix/textures')

    @classmethod
    def tearDownClass(cls):
        if cls.added:
            del bpy.types.Scene.remix_custom_texture_dir

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.images = []
        self.materials = []
        self.addCleanup(self.cleanup_materials)
        self.mesh = bpy.data.meshes.new('MultiPreflight')
        self.addCleanup(bpy.data.meshes.remove, self.mesh, do_unlink=True)
        self.mesh.from_pydata([(0,0,0), (1,0,0), (1,1,0), (0,1,0)], [], [(0,1,2), (0,2,3)])
        self.obj = bpy.data.objects.new('MultiPreflight', self.mesh)
        self.addCleanup(bpy.data.objects.remove, self.obj, do_unlink=True)
        bpy.context.scene.collection.objects.link(self.obj)
        scene = types.SimpleNamespace(remix_mod_file_path=os.path.join(self.temp.name, 'mod.usda'),
                                      remix_inherit_parent_material=False)
        self.context = types.SimpleNamespace(scene=scene, evaluated_depsgraph_get=bpy.context.evaluated_depsgraph_get)

    def cleanup_materials(self):
        for material in self.materials:
            bpy.data.materials.remove(material, do_unlink=True)
        for image in self.images:
            bpy.data.images.remove(image)

    def material(self, image_name):
        image = bpy.data.images.new(image_name, 2, 2)
        self.images.append(image)
        mat = bpy.data.materials.new('MultiMaterial')
        self.materials.append(mat)
        mat.use_nodes = True
        tree = mat.node_tree
        tex = tree.nodes.new('ShaderNodeTexImage')
        tex.image = image
        shader = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
        tree.links.new(tex.outputs['Color'], shader.inputs['Base Color'])
        return mat

    def test_second_slot_collision_stops_before_any_write(self):
        first = self.material('wall.png')
        second = self.material('wall.jpg')
        self.mesh.materials.append(first)
        self.mesh.materials.append(second)
        self.mesh.polygons[1].material_index = 1
        self.obj.modifiers.new('Triangulate', 'TRIANGULATE')
        operator = mock.Mock()
        with mock.patch.object(exp, 'ensure_mdl_files') as mdl:
            result = exp._export_remix_objects(operator, self.context, os.path.join(self.temp.name, 'target.usda'), [self.obj])
        self.assertEqual(result, (False, 0, 0))
        self.assertIn('same export path', str(operator.report.call_args))
        mdl.assert_not_called()
        operator.get_or_create_stage.assert_not_called()
        self.assertEqual(os.listdir(self.temp.name), [])

    def test_unused_slots_ignored_and_object_linked_material_used(self):
        first = self.material('same.png')
        unused = self.material('same.jpg')
        linked = self.material('distinct.png')
        self.mesh.materials.append(first)
        self.mesh.materials.append(unused)
        self.obj.material_slots[0].link = 'OBJECT'
        self.obj.material_slots[0].material = linked
        used = exp._used_mesh_materials(self.obj, self.context)
        self.assertEqual([m.original for m in used], [linked])
        self.mesh.polygons[1].material_index = 1
        used = exp._used_mesh_materials(self.obj, self.context)
        self.assertEqual([m.original for m in used], [linked, unused])

    def test_second_texture_failure_keeps_source_capture_mesh_and_dds(self):
        from rtx_remix_importer.core_utils import TextureProcessor
        first = self.material('first.png')
        second = self.material('second.png')
        sources = {}
        for image in self.images:
            source = os.path.join(self.temp.name, image.name)
            image.filepath_raw = source
            image.file_format = 'PNG'
            image.save()
            with open(source, 'rb') as f:
                sources[source] = f.read()
        for mat in (first, second):
            tree = mat.node_tree
            tex = next(n for n in tree.nodes if n.type == 'TEX_IMAGE')
            shader = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
            tree.links.remove(shader.inputs['Base Color'].links[0])
            normal = tree.nodes.new('ShaderNodeNormalMap')
            tree.links.new(tex.outputs['Color'], normal.inputs['Color'])
            tree.links.new(normal.outputs['Normal'], shader.inputs['Normal'])
        self.mesh.materials.append(first)
        self.mesh.materials.append(second)
        self.mesh.polygons[1].material_index = 1
        target = os.path.join(self.temp.name, 'mesh.usda')
        capture = os.path.join(self.temp.name, 'capture.usda')
        capture_stage = Usd.Stage.CreateNew(capture)
        capture_stage.DefinePrim('/RootNode', 'Xform')
        capture_stage.Save()
        capture_bytes = open(capture, 'rb').read()
        stage = Usd.Stage.CreateNew(target)
        stage.DefinePrim('/Existing', 'Xform')
        stage.Save()
        stage_before = stage.GetRootLayer().ExportToString()
        file_before = open(target, 'rb').read()
        textures = os.path.join(self.temp.name, 'rtx-remix', 'textures')
        os.makedirs(textures)
        first_dds = os.path.join(textures, 'first.dds')
        second_dds = os.path.join(textures, 'second.dds')
        for path in (first_dds, second_dds):
            with open(path, 'wb') as f:
                f.write(b'old DDS')
        count = []
        def convert(processor, image, destination, mode):
            count.append(image.name)
            if len(count) == 2:
                return False
            with open(destination, 'wb') as f:
                f.write(b'new DDS')
            return True
        with mock.patch.object(TextureProcessor, 'export_normal_map', autospec=True, side_effect=convert):
            result = exp.write_ingested_mesh_asset(stage, self.obj, self.context, 'Mesh', mathutils.Matrix.Identity(4),
                                                    first, False, self.temp.name, target, UsdGeom.Tokens.z)
        self.assertIsNone(result)
        self.assertEqual(len(count), 2)
        self.assertEqual(stage.GetRootLayer().ExportToString(), stage_before)
        self.assertEqual(open(target, 'rb').read(), file_before)
        self.assertEqual(open(capture, 'rb').read(), capture_bytes)
        for path, content in sources.items():
            self.assertEqual(open(path, 'rb').read(), content)
        self.assertEqual(open(first_dds, 'rb').read(), b'old DDS')
        self.assertEqual(open(second_dds, 'rb').read(), b'old DDS')

    def test_authoring_failure_on_second_material_restores_mesh_and_staged_dds(self):
        from rtx_remix_importer.core_utils import TextureProcessor
        first = self.material('first_author.png')
        second = self.material('second_author.png')
        for mat in (first, second):
            tree = mat.node_tree
            tex = next(n for n in tree.nodes if n.type == 'TEX_IMAGE')
            shader = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
            tree.links.remove(shader.inputs['Base Color'].links[0])
            normal = tree.nodes.new('ShaderNodeNormalMap')
            tree.links.new(tex.outputs['Color'], normal.inputs['Color'])
            tree.links.new(normal.outputs['Normal'], shader.inputs['Normal'])
        self.mesh.materials.append(first)
        self.mesh.materials.append(second)
        self.mesh.polygons[1].material_index = 1
        target = os.path.join(self.temp.name, 'mesh.usda')
        stage = Usd.Stage.CreateNew(target)
        stage.DefinePrim('/Original', 'Xform')
        stage.Save()
        before = stage.GetRootLayer().ExportToString()
        disk = open(target, 'rb').read()
        real_export = exp.export_material
        calls = []
        def author(*args, **kwargs):
            calls.append(args[0].name)
            if len(calls) == 2:
                raise RuntimeError('injected second authoring failure')
            return real_export(*args, **kwargs)
        def convert(processor, image, destination, mode):
            with open(destination, 'wb') as f:
                f.write(b'staged DDS')
            return True
        with mock.patch.object(exp, 'export_material', side_effect=author), \
             mock.patch.object(TextureProcessor, 'export_normal_map', autospec=True, side_effect=convert):
            result = exp.write_ingested_mesh_asset(stage, self.obj, self.context, 'Mesh', mathutils.Matrix.Identity(4),
                                                    first, False, self.temp.name, target, UsdGeom.Tokens.z)
        self.assertIsNone(result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(stage.GetRootLayer().ExportToString(), before)
        self.assertEqual(open(target, 'rb').read(), disk)
        texture_dir = os.path.join(self.temp.name, 'rtx-remix', 'textures')
        self.assertFalse(os.path.exists(os.path.join(texture_dir, 'first_author.dds')))
