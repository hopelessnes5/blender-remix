"""Only redundant MDL inputs may be removed from authored shader specs."""
import unittest
from pxr import Gf, Sdf, Usd
from rtx_remix_importer.material_defaults import (
    dead_family_inputs,
    remove_default_inputs,
    weaker_layer_authors_prim,
)


class TestMaterialDefaults(unittest.TestCase):
    def test_removes_defaults_but_preserves_changes_and_texture(self):
        stage = Usd.Stage.CreateInMemory()
        shader = stage.DefinePrim('/Shader', 'Shader')
        shader.CreateAttribute('inputs:metallic_constant', Sdf.ValueTypeNames.Float).Set(0.0)
        shader.CreateAttribute('inputs:reflection_roughness_constant', Sdf.ValueTypeNames.Float).Set(.3)
        shader.CreateAttribute('inputs:diffuse_color_constant', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.2))
        shader.CreateAttribute('inputs:normalmap_texture', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('normal.dds'))
        shader.CreateAttribute('inputs:encoding', Sdf.ValueTypeNames.Int).Set(0)
        remove_default_inputs(shader, 'AperturePBR_Opacity')
        self.assertFalse(shader.GetAttribute('inputs:metallic_constant'))
        self.assertFalse(shader.GetAttribute('inputs:diffuse_color_constant'))
        self.assertAlmostEqual(shader.GetAttribute('inputs:reflection_roughness_constant').Get(), .3)
        self.assertEqual(shader.GetAttribute('inputs:encoding').Get(), 0)
        self.assertTrue(shader.GetAttribute('inputs:normalmap_texture'))

    def test_default_override_is_preserved_when_weaker_layer_differs(self):
        root = Sdf.Layer.CreateAnonymous('root.usda')
        weak = Sdf.Layer.CreateAnonymous('weak.usda')
        root.subLayerPaths.append(weak.identifier)
        stage = Usd.Stage.Open(root)
        stage.SetEditTarget(weak)
        shader = stage.DefinePrim('/Shader', 'Shader')
        shader.CreateAttribute('inputs:metallic_constant', Sdf.ValueTypeNames.Float).Set(.7)
        stage.SetEditTarget(root)
        shader.CreateAttribute('inputs:metallic_constant', Sdf.ValueTypeNames.Float).Set(0)
        remove_default_inputs(shader, 'AperturePBR_Opacity')
        self.assertEqual(shader.GetAttribute('inputs:metallic_constant').Get(), 0)
        self.assertTrue(root.GetAttributeAtPath('/Shader.inputs:metallic_constant'))

    def test_translucent_defaults(self):
        stage = Usd.Stage.CreateInMemory()
        shader = stage.DefinePrim('/Shader', 'Shader')
        shader.CreateAttribute('inputs:ior_constant', Sdf.ValueTypeNames.Float).Set(1.3)
        shader.CreateAttribute('inputs:thin_walled', Sdf.ValueTypeNames.Bool).Set(True)
        remove_default_inputs(shader, 'AperturePBR_Translucent')
        self.assertFalse(shader.GetAttribute('inputs:ior_constant'))
        self.assertTrue(shader.GetAttribute('inputs:thin_walled').Get())

    def test_weaker_layer_authors_prim_sees_weak_specs_only(self):
        root = Sdf.Layer.CreateAnonymous('root.usda')
        weak = Sdf.Layer.CreateAnonymous('weak.usda')
        root.subLayerPaths.append(weak.identifier)
        stage = Usd.Stage.Open(root)
        self.assertFalse(weaker_layer_authors_prim(stage, '/Shader'))
        stage.SetEditTarget(weak)
        stage.DefinePrim('/Shader', 'Shader')
        self.assertFalse(weaker_layer_authors_prim(stage, '/Shader'))
        stage.SetEditTarget(root)
        stage.DefinePrim('/Shader', 'Shader')
        self.assertTrue(weaker_layer_authors_prim(stage, '/Shader'))
        # A prim authored only in the edit target still counts as weak-free.
        self.assertFalse(weaker_layer_authors_prim(stage, '/Other'))

    def test_dead_family_inputs_exclude_shared_inputs(self):
        self.assertEqual(
            dead_family_inputs('AperturePBR_Opacity', 'AperturePBR_Translucent'),
            {'diffuse_texture', 'metallic_texture', 'reflectionroughness_texture',
             'opacity_texture', 'specular_texture', 'height_texture',
             'enable_opacity', 'blend_enabled'})
        self.assertEqual(
            dead_family_inputs('AperturePBR_Translucent', 'AperturePBR_Opacity'),
            {'transmittance_texture', 'thin_walled', 'use_diffuse_layer'})
        self.assertFalse(dead_family_inputs('AperturePBR_Opacity', 'AperturePBR_Opacity'))
        self.assertFalse(dead_family_inputs('Unknown', 'AperturePBR_Opacity'))
