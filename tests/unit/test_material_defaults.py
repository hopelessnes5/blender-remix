"""Only redundant MDL inputs may be removed from authored shader specs."""
import unittest
from pxr import Gf, Sdf, Usd
from rtx_remix_importer.material_defaults import remove_default_inputs


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
