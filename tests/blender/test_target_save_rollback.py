"""DDS, USD and object state remain consistent when target Save fails."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bpy
from pxr import Sdf, Usd
from rtx_remix_importer.core_utils import TextureProcessor
from rtx_remix_importer.operators.export_operator import _export_remix_objects, export_material_replacement


class _Operator:
    def __init__(self, stage):
        self.stage = stage
        self.messages = []
    def get_or_create_stage(self, path):
        return self.stage
    def report(self, level, message):
        self.messages.append((level, message))


class _LightWithMaterial:
    """Light object with a material slot to exercise the export_light branch."""
    def __init__(self, obj, material):
        self.obj = obj
        self.material_slots = [SimpleNamespace(material=material)]
    def __getattr__(self, name):
        return getattr(self.obj, name)
    def __getitem__(self, key):
        return self.obj[key]
    def __setitem__(self, key, value):
        self.obj[key] = value
    def __delitem__(self, key):
        del self.obj[key]
    def __contains__(self, key):
        return key in self.obj
    def get(self, key, default=None):
        return self.obj.get(key, default)


class TestTargetSaveRollback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.added = []
        for name, prop in [
            ('remix_mod_file_path', bpy.props.StringProperty()),
            ('remix_custom_texture_dir', bpy.props.StringProperty(default='textures')),
            ('remix_export_scale', bpy.props.FloatProperty(default=1.0)),
            ('remix_anchor_object_target', bpy.props.PointerProperty(type=bpy.types.Object)),
        ]:
            if not hasattr(bpy.types.Scene, name):
                setattr(bpy.types.Scene, name, prop)
                cls.added.append(name)

    @classmethod
    def tearDownClass(cls):
        for name in cls.added:
            delattr(bpy.types.Scene, name)

    def setUp(self):
        if not TextureProcessor().is_available():
            self.skipTest('texconv unavailable')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'mod.usda')
        self.stage = Usd.Stage.CreateNew(self.path)
        self.stage.DefinePrim('/Existing', 'Xform')
        self.stage.GetRootLayer().Save()
        self.stage.OverridePrim('/Unsaved').CreateAttribute('keep', Sdf.ValueTypeNames.Int).Set(17)
        self.before = self.stage.GetRootLayer().ExportToString()
        with open(self.path, 'rb') as stream:
            self.before_file = stream.read()
        scene = bpy.context.scene
        self.scene_state = (scene.remix_mod_file_path, scene.remix_custom_texture_dir, scene.remix_export_scale, scene.remix_anchor_object_target)
        self.was_set = {name: scene.is_property_set(name) for name in ('remix_mod_file_path', 'remix_custom_texture_dir', 'remix_export_scale', 'remix_anchor_object_target')}
        scene.remix_mod_file_path = self.path
        scene.remix_custom_texture_dir = 'textures'
        scene.remix_export_scale = 1
        scene.remix_anchor_object_target = None
        self.addCleanup(self.restore_scene)
        self.operator = _Operator(self.stage)

    def restore_scene(self):
        scene = bpy.context.scene
        scene.remix_mod_file_path, scene.remix_custom_texture_dir, scene.remix_export_scale, scene.remix_anchor_object_target = self.scene_state
        for name, was_set in self.was_set.items():
            if not was_set:
                scene.property_unset(name)

    def make_material(self, name):
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        self.addCleanup(bpy.data.materials.remove, mat, do_unlink=True)
        image = bpy.data.images.new(name + '_normal', 4, 4, float_buffer=True)
        self.addCleanup(bpy.data.images.remove, image)
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set([.8, .5, .9, 1] * 16)
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        tex = nodes.new('ShaderNodeTexImage'); tex.image = image
        normal = nodes.new('ShaderNodeNormalMap')
        bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
        links.new(tex.outputs['Color'], normal.inputs['Color'])
        links.new(normal.outputs['Normal'], bsdf.inputs['Normal'])
        path = os.path.join(self.tmp.name, 'textures', name + '_normal.dds')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as stream:
            stream.write(('original_' + name).encode())
        return mat, path

    def make_mesh(self, name, material):
        mesh = bpy.data.meshes.new(name + '_mesh')
        self.addCleanup(bpy.data.meshes.remove, mesh)
        obj = bpy.data.objects.new(name, mesh)
        self.addCleanup(bpy.data.objects.remove, obj, do_unlink=True)
        mesh.materials.append(material)
        return obj

    def assert_unchanged(self, paths):
        self.assertEqual(self.stage.GetRootLayer().ExportToString(), self.before)
        with open(self.path, 'rb') as stream:
            self.assertEqual(stream.read(), self.before_file)
        for name, path in paths:
            with open(path, 'rb') as stream:
                self.assertEqual(stream.read(), ('original_' + name).encode())

    def test_two_materials_save_failure_restores_all(self):
        first_mat, first_path = self.make_material('first')
        second_mat, second_path = self.make_material('second')
        first = self.make_mesh('first', first_mat)
        second = self.make_mesh('second', second_mat)
        first['remix_material_path'] = 'previous'
        def material_path(obj, material, context):
            return '/RootNode/Looks/mat_' + ('1111111111111111' if obj == first else '2222222222222222')
        with patch('rtx_remix_importer.operators.export_operator.ensure_mdl_files'), \
             patch('rtx_remix_importer.operators.export_operator.resolve_anchor_prim_path', return_value='/RootNode/meshes/mesh_1111111111111111'), \
             patch('rtx_remix_importer.operators.export_operator.captured_material_path', side_effect=material_path), \
             patch.object(Sdf.Layer, 'Save', return_value=False):
            result = _export_remix_objects(self.operator, bpy.context, self.path, [first, second], True)
        self.assertEqual(result, (False, 0, 0))
        self.assertEqual(first['remix_material_path'], 'previous')
        self.assertNotIn('remix_material_path', second)
        self.assert_unchanged([('first', first_path), ('second', second_path)])

    def test_two_materials_success_publishes_and_saves(self):
        first_mat, first_path = self.make_material('success_a')
        second_mat, second_path = self.make_material('success_b')
        first = self.make_mesh('success_a', first_mat)
        second = self.make_mesh('success_b', second_mat)
        def material_path(obj, material, context):
            return '/RootNode/Looks/mat_' + ('1111111111111111' if obj == first else '2222222222222222')
        with patch('rtx_remix_importer.operators.export_operator.ensure_mdl_files'), \
             patch('rtx_remix_importer.operators.export_operator.resolve_anchor_prim_path', return_value='/RootNode/meshes/mesh_1111111111111111'), \
             patch('rtx_remix_importer.operators.export_operator.captured_material_path', side_effect=material_path):
            result = _export_remix_objects(self.operator, bpy.context, self.path, [first, second], True)
        self.assertEqual(result, (True, 2, 0))
        self.assertEqual(first['remix_material_path'], '/RootNode/Looks/mat_1111111111111111')
        self.assertEqual(second['remix_material_path'], '/RootNode/Looks/mat_2222222222222222')
        for path in (first_path, second_path):
            with open(path, 'rb') as stream:
                self.assertEqual(stream.read(4), b'DDS ')
        reopened = Usd.Stage.Open(self.path)
        for path in ('/RootNode/Looks/mat_1111111111111111/Shader', '/RootNode/Looks/mat_2222222222222222/Shader'):
            self.assertTrue(reopened.GetPrimAtPath(path).GetAttribute('inputs:normalmap_texture').Get())

    def test_second_material_publish_failure_rolls_back_first(self):
        first_mat, first_path = self.make_material('publish_a')
        second_mat, second_path = self.make_material('publish_b')
        first = self.make_mesh('publish_a', first_mat)
        second = self.make_mesh('publish_b', second_mat)
        def material_path(obj, material, context):
            return '/RootNode/Looks/mat_' + ('1111111111111111' if obj == first else '2222222222222222')
        replace = os.replace
        def fail_second(src, dst):
            if os.path.normcase(os.path.abspath(dst)) == os.path.normcase(os.path.abspath(second_path)):
                raise OSError('injected publish failure')
            return replace(src, dst)
        with patch('rtx_remix_importer.operators.export_operator.ensure_mdl_files'), \
             patch('rtx_remix_importer.operators.export_operator.resolve_anchor_prim_path', return_value='/RootNode/meshes/mesh_1111111111111111'), \
             patch('rtx_remix_importer.operators.export_operator.captured_material_path', side_effect=material_path), \
             patch('rtx_remix_importer.operators.export_operator.os.replace', side_effect=fail_second):
            result = _export_remix_objects(self.operator, bpy.context, self.path, [first, second], True)
        self.assertEqual(result, (False, 0, 0))
        self.assertNotIn('remix_material_path', first)
        self.assertNotIn('remix_material_path', second)
        self.assert_unchanged([('publish_a', first_path), ('publish_b', second_path)])

    def test_failed_item_keeps_other_successful_material(self):
        first_mat, first_path = self.make_material('item_fail')
        second_mat, second_path = self.make_material('item_success')
        first = self.make_mesh('item_fail', first_mat)
        second = self.make_mesh('item_success', second_mat)
        first['remix_material_path'] = 'previous'
        def material_path(obj, material, context):
            return '/RootNode/Looks/mat_2222222222222222'
        def export_item(operator, context, obj, stage, root, path, _texture_transaction=None):
            if obj == first:
                stage.OverridePrim('/BadPartial').CreateAttribute('partial', Sdf.ValueTypeNames.Bool).Set(True)
                obj['remix_material_path'] = 'bad'
                raise RuntimeError('injected item failure')
            return export_material_replacement(operator, context, obj, stage, root, path, _texture_transaction=_texture_transaction)
        with patch('rtx_remix_importer.operators.export_operator.ensure_mdl_files'), \
             patch('rtx_remix_importer.operators.export_operator.resolve_anchor_prim_path', return_value='/RootNode/meshes/mesh_1111111111111111'), \
             patch('rtx_remix_importer.operators.export_operator.captured_material_path', side_effect=material_path), \
             patch('rtx_remix_importer.operators.export_operator.export_material_replacement', side_effect=export_item):
            result = _export_remix_objects(self.operator, bpy.context, self.path, [first, second], True)
        self.assertEqual(result, (True, 1, 1))
        self.assertEqual(first['remix_material_path'], 'previous')
        self.assertEqual(second['remix_material_path'], '/RootNode/Looks/mat_2222222222222222')
        self.assertFalse(self.stage.GetPrimAtPath('/BadPartial'))
        with open(first_path, 'rb') as stream:
            self.assertEqual(stream.read(), b'original_item_fail')
        with open(second_path, 'rb') as stream:
            self.assertEqual(stream.read(4), b'DDS ')

    def test_two_lights_save_exception_restores_all(self):
        lights, paths = [], []
        for name in ('light_a', 'light_b'):
            mat, path = self.make_material(name)
            data = bpy.data.lights.new(name, 'POINT')
            self.addCleanup(bpy.data.lights.remove, data)
            obj = bpy.data.objects.new(name, data)
            self.addCleanup(bpy.data.objects.remove, obj, do_unlink=True)
            bpy.context.scene.collection.objects.link(obj)
            lights.append(_LightWithMaterial(obj, mat))
            paths.append((name, path))
        lights[0]['usd_light_path'] = '/RootNode/lights/light_OLD'
        with patch('rtx_remix_importer.operators.export_operator.ensure_mdl_files'), \
             patch('rtx_remix_importer.operators.export_operator.get_game_stage', return_value=None), \
             patch.object(Sdf.Layer, 'Save', side_effect=RuntimeError('injected Save failure')):
            result = _export_remix_objects(self.operator, bpy.context, self.path, lights, False)
        self.assertEqual(result, (False, 0, 0))
        self.assertEqual(lights[0]['usd_light_path'], '/RootNode/lights/light_OLD')
        self.assertNotIn('usd_light_path', lights[1])
        self.assert_unchanged(paths)
