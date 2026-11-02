"""Replacement defaults must survive composition with a separate capture."""
import os
import tempfile
import unittest
from unittest.mock import patch

import bpy
from pxr import Gf, Sdf, Usd
from rtx_remix_importer.operators.export_operator import export_material, export_material_replacement


class Operator:
    def report(self, level, message):
        raise AssertionError((level, message))


class TestReplacementDefaults(unittest.TestCase):
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
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mat = bpy.data.materials.new('default_material')
        self.mat.use_nodes = True
        self.addCleanup(bpy.data.materials.remove, self.mat, do_unlink=True)
        self.node = next(n for n in self.mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
        self.node.inputs['Roughness'].default_value = .5
        self.node.inputs['Metallic'].default_value = 0
        self.node.inputs['Base Color'].default_value = (.2, .2, .2, 1)
        self.mesh = bpy.data.meshes.new('default_mesh')
        self.addCleanup(bpy.data.meshes.remove, self.mesh)
        self.obj = bpy.data.objects.new('default_object', self.mesh)
        self.addCleanup(bpy.data.objects.remove, self.obj, do_unlink=True)
        self.mesh.materials.append(self.mat)

    def test_replacement_overrides_capture_after_reopen(self):
        capture = os.path.join(self.temp.name, 'capture.usda')
        target = os.path.join(self.temp.name, 'target.usda')
        root = os.path.join(self.temp.name, 'root.usda')
        path = '/RootNode/Looks/mat_9BF7DE6807F288EC'
        stage = Usd.Stage.CreateNew(capture)
        shader = stage.DefinePrim(path + '/Shader', 'Shader')
        shader.CreateAttribute('inputs:reflection_roughness_constant', Sdf.ValueTypeNames.Float).Set(.8)
        shader.CreateAttribute('inputs:metallic_constant', Sdf.ValueTypeNames.Float).Set(.7)
        shader.CreateAttribute('inputs:diffuse_color_constant', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.8))
        stage.GetRootLayer().Save()
        with open(capture, 'rb') as f:
            before = f.read()
        stage = Usd.Stage.CreateNew(target)
        with patch('rtx_remix_importer.operators.export_operator.resolve_anchor_prim_path', return_value='/RootNode/mesh'), \
             patch('rtx_remix_importer.operators.export_operator.captured_material_path', return_value=path):
            self.assertTrue(export_material_replacement(Operator(), bpy.context, self.obj, stage, self.temp.name, target))
        stage.GetRootLayer().Save()
        composed = Usd.Stage.CreateNew(root)
        composed.GetRootLayer().subLayerPaths = ['target.usda', 'capture.usda']
        composed.GetRootLayer().Save()
        composed = Usd.Stage.Open(root)
        shader = composed.GetPrimAtPath(path + '/Shader')
        self.assertAlmostEqual(shader.GetAttribute('inputs:reflection_roughness_constant').Get(), .5)
        self.assertAlmostEqual(shader.GetAttribute('inputs:metallic_constant').Get(), 0)
        for value in shader.GetAttribute('inputs:diffuse_color_constant').Get():
            self.assertAlmostEqual(value, .2)
        with open(capture, 'rb') as f:
            self.assertEqual(f.read(), before)

    def test_new_asset_stays_compact(self):
        stage = Usd.Stage.CreateInMemory()
        with patch('rtx_remix_importer.operators.export_operator.ensure_mdl_files'):
            path = export_material(self.mat, stage, self.temp.name, os.path.join(self.temp.name, 'asset.usda'), material_scope_path='/Looks')
        shader = stage.GetPrimAtPath(path.AppendPath('Shader'))
        for name in ('reflection_roughness_constant', 'metallic_constant', 'diffuse_color_constant'):
            self.assertFalse(shader.GetAttribute('inputs:' + name))
