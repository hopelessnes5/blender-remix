"""Locally stale material inputs must not reveal weaker-layer values."""
import os
import tempfile
import unittest

import bpy
from pxr import Sdf, Usd
from rtx_remix_importer.operators.export_operator import export_material


class StaleMaterialInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.added_property = not hasattr(bpy.types.Scene, 'remix_custom_texture_dir')
        if cls.added_property:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(default='rtx-remix/textures')

    @classmethod
    def tearDownClass(cls):
        if cls.added_property:
            del bpy.types.Scene.remix_custom_texture_dir

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.weak = Sdf.Layer.CreateAnonymous('weak.usda')
        self.strong = Sdf.Layer.CreateAnonymous('strong.usda')
        self.strong.subLayerPaths.append(self.weak.identifier)
        self.stage = Usd.Stage.Open(self.strong)
        self.path = '/RootNode/Looks/mat_1234567890ABCDEF'
        self.shaderpath = self.path + '/Shader'
        self.stage.SetEditTarget(self.weak)
        weakshader = self.stage.DefinePrim(self.shaderpath, 'Shader')
        for name in ('normalmap_texture', 'height_texture', 'diffuse_texture',
                     'emissive_mask_texture', 'opacity_texture'):
            weakshader.CreateAttribute('inputs:' + name, Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('weaker.dds'))
        for name in ('enable_emission', 'enable_opacity', 'blend_enabled'):
            weakshader.CreateAttribute('inputs:' + name, Sdf.ValueTypeNames.Bool).Set(True)
        self.stage.SetEditTarget(self.strong)
        self.shader = self.stage.OverridePrim(self.shaderpath)
        self.mat = bpy.data.materials.new('StaleInputTest')
        self.mat.use_nodes = True
        self.addCleanup(bpy.data.materials.remove, self.mat, do_unlink=True)
        self.principled = next(n for n in self.mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')

    def run_export(self):
        return export_material(self.mat, self.stage, self.temp.name,
                               os.path.join(self.temp.name, 'mod.usda'),
                               override_material_path=self.path)

    def test_stale_local_textures_mask_weaker_and_preserve_foreign(self):
        names = ('normalmap_texture', 'height_texture', 'diffuse_texture',
                 'emissive_mask_texture', 'opacity_texture')
        for name in names:
            self.shader.CreateAttribute('inputs:' + name, Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('old.dds'))
        self.shader.CreateAttribute('inputs:foreign_custom', Sdf.ValueTypeNames.String).Set('keep')
        self.run_export()
        for name in names:
            self.assertEqual(self.shader.GetAttribute('inputs:' + name).Get(), Sdf.AssetPath())
        self.assertEqual(self.shader.GetAttribute('inputs:foreign_custom').Get(), 'keep')
        self.assertEqual(self.weak.GetAttributeAtPath(self.shaderpath + '.inputs:normalmap_texture').default,
                         Sdf.AssetPath('weaker.dds'))

    def test_second_export_disables_local_flags_over_weaker_layer(self):
        self.principled.inputs['Alpha'].default_value = 0.3
        self.principled.inputs['Emission Strength'].default_value = 2.0
        self.run_export()
        self.principled.inputs['Alpha'].default_value = 1.0
        self.principled.inputs['Emission Strength'].default_value = 0.0
        self.run_export()
        for name in ('enable_emission', 'enable_opacity', 'blend_enabled'):
            self.assertEqual(self.shader.GetAttribute('inputs:' + name).Get(), False)
        self.assertTrue(self.weak.GetAttributeAtPath(self.shaderpath + '.inputs:enable_opacity').default)

    def test_weaker_only_input_and_local_animated_input_are_preserved(self):
        normal = self.shader.CreateAttribute('inputs:normalmap_texture', Sdf.ValueTypeNames.Asset)
        normal.Set(Sdf.AssetPath('animated.dds'), 1)
        normal.Set(Sdf.AssetPath('local.dds'))
        self.run_export()
        self.assertEqual(normal.Get(), Sdf.AssetPath('local.dds'))
        self.assertEqual(self.shader.GetAttribute('inputs:height_texture').Get(), Sdf.AssetPath('weaker.dds'))

    def test_connected_managed_input_is_preserved(self):
        normal = self.shader.CreateAttribute('inputs:normalmap_texture', Sdf.ValueTypeNames.Asset)
        normal.Set(Sdf.AssetPath('connected.dds'))
        normal.AddConnection('/RootNode/Source.outputs:out')
        self.run_export()
        self.assertEqual(normal.Get(), Sdf.AssetPath('connected.dds'))
        self.assertEqual([str(path) for path in normal.GetConnections()], ['/RootNode/Source.outputs:out'])

    def test_weaker_time_samples_are_preserved(self):
        self.stage.SetEditTarget(self.weak)
        weak_normal = self.stage.GetPrimAtPath(self.shaderpath).GetAttribute('inputs:normalmap_texture')
        weak_normal.Set(Sdf.AssetPath('animated.dds'), 1)
        self.stage.SetEditTarget(self.strong)
        normal = self.shader.CreateAttribute('inputs:normalmap_texture', Sdf.ValueTypeNames.Asset)
        normal.Set(Sdf.AssetPath('local.dds'))
        self.run_export()
        self.assertEqual(normal.Get(), Sdf.AssetPath('local.dds'))
