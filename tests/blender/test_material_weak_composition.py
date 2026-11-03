"""Audit A06/A07: defaults and weak-only values under a composed capture.

A replacement exporter writes on top of a capture material. Omitting an MDL
default is only safe when the composed weaker layers are visible and agree
(audit A06); weak-only values that the user turned off must be masked so the
exported graph wins (A07). Switching the MDL family (opaque <-> translucent)
rebinds the ident trio and masks inputs that only the dead family names.
"""

import os
import tempfile
import unittest

import bpy
from pxr import Sdf, Usd
from rtx_remix_importer.operators.export_operator import export_material


class MaterialWeakComposition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.added_property = not hasattr(bpy.types.Scene, 'remix_custom_texture_dir')
        if cls.added_property:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(
                default='rtx-remix/textures')

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

    def with_weak_material(self, family=None, **inputs):
        """Author the capture-side shader in the weak layer."""
        self.stage.SetEditTarget(self.weak)
        shader = self.stage.DefinePrim(self.shaderpath, 'Shader')
        if family is not None:
            shader.CreateAttribute('info:implementationSource',
                                   Sdf.ValueTypeNames.Token).Set('sourceAsset')
            shader.CreateAttribute('info:mdl:sourceAsset',
                                   Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(f'{family}.mdl'))
            shader.CreateAttribute('info:mdl:sourceAsset:subIdentifier',
                                   Sdf.ValueTypeNames.Token).Set(family)
        for name, value in inputs.items():
            if isinstance(value, bool):
                shader.CreateAttribute('inputs:' + name, Sdf.ValueTypeNames.Bool).Set(value)
            elif isinstance(value, Sdf.AssetPath):
                shader.CreateAttribute('inputs:' + name, Sdf.ValueTypeNames.Asset).Set(value)
            else:
                shader.CreateAttribute('inputs:' + name, Sdf.ValueTypeNames.Float).Set(float(value))
        self.stage.SetEditTarget(self.strong)

    def make_material(self, roughness=0.5, metallic=0.0, transmission=None, name='WeakComp'):
        mat = bpy.data.materials.new(name)
        self.addCleanup(bpy.data.materials.remove, mat, do_unlink=True)
        mat.use_nodes = True
        principled = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
        principled.inputs['Roughness'].default_value = roughness
        principled.inputs['Metallic'].default_value = metallic
        if transmission is not None:
            principled.inputs['Transmission Weight'].default_value = transmission
        return mat

    def export(self):
        return export_material(self.mat, self.stage, self.temp.name,
                               os.path.join(self.temp.name, 'mod.usda'),
                               override_material_path=self.path)

    def composed_value(self, name):
        prim = self.stage.GetPrimAtPath(self.shaderpath)
        attr = prim.GetAttribute('inputs:' + name)
        return attr.Get(Usd.TimeCode.Default()) if attr else None

    def composed_ident(self):
        prim = self.stage.GetPrimAtPath(self.shaderpath)
        attr = prim.GetAttribute('info:mdl:sourceAsset:subIdentifier')
        return attr.Get(Usd.TimeCode.Default()) if attr else None

    def strong_local(self, name):
        return self.strong.GetAttributeAtPath(self.shaderpath + '.inputs:' + name)

    def ident_spec(self):
        return self.strong.GetAttributeAtPath(
            self.shaderpath + '.info:mdl:sourceAsset:subIdentifier')

    def test_weak_only_texture_is_masked_when_the_user_has_no_map(self):
        self.with_weak_material(height_texture=Sdf.AssetPath('weaker.dds'))
        self.mat = self.make_material()
        self.export()
        self.assertEqual(self.composed_value('height_texture'), Sdf.AssetPath())
        local = self.strong_local('height_texture')
        self.assertTrue(local)
        self.assertEqual(local.default, Sdf.AssetPath())
        self.assertEqual(
            self.weak.GetAttributeAtPath(self.shaderpath + '.inputs:height_texture').default,
            Sdf.AssetPath('weaker.dds'))

    def test_omitted_default_agrees_with_weak_composition(self):
        self.with_weak_material(reflection_roughness_constant=0.5, metallic_constant=0.0)
        self.mat = self.make_material(roughness=0.5, metallic=0.0)
        self.export()
        self.assertIsNone(self.strong_local('reflection_roughness_constant'))
        self.assertIsNone(self.strong_local('metallic_constant'))
        self.assertEqual(self.composed_value('reflection_roughness_constant'), 0.5)
        self.assertEqual(self.composed_value('metallic_constant'), 0.0)

    def test_disagreeing_weak_value_keeps_explicit_default(self):
        self.with_weak_material(reflection_roughness_constant=0.8)
        self.mat = self.make_material(roughness=0.5)
        self.export()
        local = self.strong_local('reflection_roughness_constant')
        self.assertTrue(local)
        self.assertAlmostEqual(float(local.default), 0.5)
        self.assertEqual(self.composed_value('reflection_roughness_constant'), 0.5)

    def test_defaults_are_kept_without_weaker_layers(self):
        self.mat = self.make_material(roughness=0.5)
        self.export()
        local = self.strong_local('reflection_roughness_constant')
        self.assertTrue(local)
        self.assertAlmostEqual(float(local.default), 0.5)

    def test_weak_only_emission_is_masked_when_disabled(self):
        self.with_weak_material(enable_emission=True)
        self.mat = self.make_material()
        self.export()
        self.assertEqual(self.composed_value('enable_emission'), False)
        self.assertTrue(
            self.weak.GetAttributeAtPath(
                self.shaderpath + '.inputs:enable_emission').default)
        self.assertEqual(
            self.weak.GetAttributeAtPath(self.shaderpath + '.inputs:enable_emission').default,
            True)

    def test_weak_only_opacity_block_is_masked_when_material_is_opaque(self):
        self.with_weak_material(opacity_texture=Sdf.AssetPath('weak.dds'),
                                enable_opacity=True, blend_enabled=True)
        self.mat = self.make_material()
        self.export()
        self.assertEqual(self.composed_value('opacity_texture'), Sdf.AssetPath())
        self.assertEqual(self.composed_value('enable_opacity'), False)
        self.assertEqual(self.composed_value('blend_enabled'), False)
        self.assertEqual(
            self.weak.GetAttributeAtPath(self.shaderpath + '.inputs:opacity_texture').default,
            Sdf.AssetPath('weak.dds'))

    def test_family_switch_to_translucent_rebinds_ident_and_masks_opaque_inputs(self):
        self.with_weak_material('AperturePBR_Opacity',
                                diffuse_texture=Sdf.AssetPath('c.dds'),
                                opacity_texture=Sdf.AssetPath('o.dds'),
                                enable_opacity=True)
        self.mat = self.make_material(transmission=0.9)
        self.export()
        self.assertEqual(self.composed_ident(), 'AperturePBR_Translucent')
        self.assertTrue(self.ident_spec())
        self.assertEqual(self.composed_value('diffuse_texture'), Sdf.AssetPath())
        self.assertEqual(self.composed_value('opacity_texture'), Sdf.AssetPath())
        self.assertEqual(self.composed_value('enable_opacity'), False)
        self.assertEqual(
            self.weak.GetAttributeAtPath(self.shaderpath + '.inputs:diffuse_texture').default,
            Sdf.AssetPath('c.dds'))
        self.assertTrue(os.path.isdir(os.path.join(self.temp.name, 'assets', 'remix_materials')))

    def test_family_switch_back_masks_translucent_only_inputs(self):
        self.with_weak_material('AperturePBR_Translucent',
                                transmittance_texture=Sdf.AssetPath('t.dds'),
                                thin_walled=True)
        self.mat = self.make_material()
        self.export()
        self.assertEqual(self.composed_ident(), 'AperturePBR_Opacity')
        self.assertTrue(self.ident_spec())
        self.assertEqual(self.composed_value('transmittance_texture'), Sdf.AssetPath())
        self.assertEqual(self.composed_value('thin_walled'), False)
        self.assertEqual(
            self.weak.GetAttributeAtPath(
                self.shaderpath + '.inputs:transmittance_texture').default,
            Sdf.AssetPath('t.dds'))

    def test_repeat_export_same_family_leaves_weak_ident(self):
        self.with_weak_material('AperturePBR_Opacity',
                                reflection_roughness_constant=0.8)
        self.mat = self.make_material(roughness=0.5)
        self.export()
        self.assertIsNone(self.ident_spec())
        self.export()
        self.assertIsNone(self.ident_spec())
        self.assertEqual(self.composed_ident(), 'AperturePBR_Opacity')


if __name__ == '__main__':
    unittest.main()
