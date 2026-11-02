"""A10: raw normal DDS cannot represent Blender's Normal Map strength."""
import os
import tempfile
import types
import unittest
from unittest import mock

import bpy
from pxr import Usd
from rtx_remix_importer.operators import export_operator as export


class NormalStrengthGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.created_property = not hasattr(bpy.types.Scene, 'remix_custom_texture_dir')
        if cls.created_property:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(default='rtx-remix/textures')

    @classmethod
    def tearDownClass(cls):
        if cls.created_property:
            del bpy.types.Scene.remix_custom_texture_dir

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mat = bpy.data.materials.new('NormalStrengthGuard')
        self.mat.use_nodes = True
        self.image = bpy.data.images.new('GuardNormal.png', 2, 2)
        self.addCleanup(bpy.data.images.remove, self.image)
        self.addCleanup(bpy.data.materials.remove, self.mat, do_unlink=True)
        self.tree = self.mat.node_tree
        self.shader = next(n for n in self.tree.nodes if n.type == 'BSDF_PRINCIPLED')
        self.normal = self.tree.nodes.new('ShaderNodeNormalMap')
        self.texture = self.tree.nodes.new('ShaderNodeTexImage')
        self.texture.image = self.image
        self.tree.links.new(self.texture.outputs['Color'], self.normal.inputs['Color'])
        self.tree.links.new(self.normal.outputs['Normal'], self.shader.inputs['Normal'])

    def test_unity_and_no_normal_map_are_accepted(self):
        export._validate_normal_map_strength(self.shader)
        self.normal.inputs['Strength'].default_value = 0.3
        self.tree.links.remove(self.shader.inputs['Normal'].links[0])
        export._validate_normal_map_strength(self.shader)
        self.tree.links.new(self.texture.outputs['Color'], self.shader.inputs['Normal'])
        export._validate_normal_map_strength(self.shader)

    def test_nonunity_rejected_before_standalone_stage_or_file_changes(self):
        self.normal.inputs['Strength'].default_value = 0.5
        path = os.path.join(self.temp.name, 'mod.usda')
        stage = Usd.Stage.CreateNew(path)
        stage.Save()
        before = stage.GetRootLayer().ExportToString()
        with open(path, 'rb') as f:
            disk = f.read()
        with mock.patch.object(export, 'ensure_mdl_files') as mdl:
            with self.assertRaisesRegex(ValueError, 'Normal Map strength'):
                export.export_material(self.mat, stage, self.temp.name, path)
        mdl.assert_not_called()
        self.assertEqual(stage.GetRootLayer().ExportToString(), before)
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), disk)
        self.assertEqual(os.listdir(self.temp.name), ['mod.usda'])

    def test_unity_strength_exports_normal_texture(self):
        from rtx_remix_importer.core_utils import TextureProcessor
        path = os.path.join(self.temp.name, 'mod.usda')
        stage = Usd.Stage.CreateNew(path)
        def convert(processor, image, destination, mode):
            with open(destination, 'wb') as f:
                f.write(b'DDS test')
            return True
        with mock.patch.object(TextureProcessor, 'export_normal_map', autospec=True, side_effect=convert):
            material_path = export.export_material(self.mat, stage, self.temp.name, path,
                                                   override_material_path='/RootNode/Looks/mat_1234567890ABCDEF')
        shader = stage.GetPrimAtPath(str(material_path) + '/Shader')
        self.assertTrue(shader.GetAttribute('inputs:normalmap_texture').Get().path.endswith('GuardNormal.dds'))

    def test_linked_strength_rejected_during_selection_preflight(self):
        value = self.tree.nodes.new('ShaderNodeValue')
        value.outputs[0].default_value = 1.0
        self.tree.links.new(value.outputs[0], self.normal.inputs['Strength'])
        obj = types.SimpleNamespace(type='MESH', material_slots=[types.SimpleNamespace(material=self.mat)])
        scene = types.SimpleNamespace(remix_mod_file_path=os.path.join(self.temp.name, 'mod.usda'),
                                      remix_inherit_parent_material=False)
        operator = mock.Mock()
        with mock.patch.object(export, 'ensure_mdl_files') as mdl, \
             mock.patch.object(export, '_used_mesh_materials', return_value=[self.mat]), \
             mock.patch.object(export, 'get_mesh_name_from_object', return_value='normal_strength_mesh'):
            result = export._export_remix_objects(operator, types.SimpleNamespace(scene=scene),
                                                   os.path.join(self.temp.name, 'target.usda'), [obj])
        self.assertEqual(result, (False, 0, 0))
        self.assertIn('Normal Map strength', str(operator.report.call_args))
        mdl.assert_not_called()
        operator.get_or_create_stage.assert_not_called()
        self.assertEqual(os.listdir(self.temp.name), [])
