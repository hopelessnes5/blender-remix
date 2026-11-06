"""Tests for direct AperturePBR_Translucent -> Blender Glass BSDF mapping.

Run using Blender's bundled Python: blender -b --python <test-runner>.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import bpy
from pxr import Gf, Sdf, Usd, UsdShade

from rtx_remix_importer import material_utils, mod_apply_utils
from rtx_remix_importer.export_utils import NodeAnalyzer, ShaderSetup
from rtx_remix_importer.glass_nodes import ensure_emission, emission_node, upgrade_legacy_translucent


class GlassImportTests(unittest.TestCase):
    def setUp(self):
        self.stage = Usd.Stage.CreateInMemory()
        self.shader = UsdShade.Shader.Define(self.stage, '/Material/Shader')
        self.materials_to_remove = []

    def tearDown(self):
        for mat in self.materials_to_remove:
            if mat.name in bpy.data.materials:
                bpy.data.materials.remove(mat, do_unlink=True)

    def material(self, name):
        mat, node = material_utils.create_translucent_blender_material(name)
        self.materials_to_remove.append(mat)
        return mat, node

    def input(self, name, typename, value):
        self.shader.CreateInput(name, typename).Set(value)

    def test_import_uses_glass_without_principled(self):
        mat, glass = self.material('RemixGlassCreate')
        self.assertEqual(glass.type, 'BSDF_GLASS')
        self.assertAlmostEqual(glass.inputs['IOR'].default_value, 1.3)
        self.assertFalse(any(n.type == 'BSDF_PRINCIPLED' for n in mat.node_tree.nodes))
        self.assertEqual(NodeAnalyzer.find_surface_shader(mat), glass)
        self.assertEqual(ShaderSetup.detect_material_type(mat), 'AperturePBR_Translucent')

    def test_capture_color_ior_and_emission(self):
        mat, glass = self.material('RemixGlassImport')
        self.input('transmittance_color', Sdf.ValueTypeNames.Color3f, Gf.Vec3f(.2, .6, .8))
        self.input('ior_constant', Sdf.ValueTypeNames.Float, 1.52)
        self.input('enable_emission', Sdf.ValueTypeNames.Bool, True)
        self.input('emissive_color', Sdf.ValueTypeNames.Color3f, Gf.Vec3f(.8, .2, .1))
        self.input('emissive_intensity', Sdf.ValueTypeNames.Float, 12.0)
        material_utils.process_pbr(self.shader, mat, glass, '')
        self.assertAlmostEqual(glass.inputs['Color'].default_value[0], .2)
        self.assertAlmostEqual(glass.inputs['IOR'].default_value, 1.52)
        emission = emission_node(mat)
        self.assertIsNotNone(emission)
        self.assertAlmostEqual(emission.inputs['Strength'].default_value, 12.0)
        self.assertEqual(NodeAnalyzer.find_surface_shader(mat), glass)
        self.assertEqual(ShaderSetup.detect_material_type(mat), 'AperturePBR_Translucent')
        self.assertIs(ensure_emission(mat), emission)
        self.assertEqual(sum(n.type == 'EMISSION' for n in mat.node_tree.nodes), 1)

    def test_explicit_zero_emission_stays_off(self):
        mat, glass = self.material('RemixGlassZeroEmission')
        self.input('transmittance_color', Sdf.ValueTypeNames.Color3f, Gf.Vec3f(.5, .5, .5))
        self.input('enable_emission', Sdf.ValueTypeNames.Bool, True)
        self.input('emissive_intensity', Sdf.ValueTypeNames.Float, 0.0)
        material_utils.process_pbr(self.shader, mat, glass, '')
        emitter = emission_node(mat)
        self.assertIsNotNone(emitter)
        self.assertAlmostEqual(emitter.inputs['Strength'].default_value, 0.0)

    def test_mod_creation_uses_glass(self):
        mat, glass = mod_apply_utils.create_mod_default_blender_material_util(
            'RemixGlassMod', lambda *_: None, is_translucent=True)
        self.materials_to_remove.append(mat)
        self.assertEqual(glass.type, 'BSDF_GLASS')
        self.assertAlmostEqual(glass.inputs['IOR'].default_value, 1.3)

    def test_partial_mod_preserves_glass_and_other_inputs(self):
        base, glass = self.material('RemixGlassPartialBase')
        glass.inputs['Color'].default_value = (.3, .4, .6, 1)
        self.input('ior_constant', Sdf.ValueTypeNames.Float, 1.61)
        result, node = mod_apply_utils.create_mod_material_nodes_util(
            '/Material', self.stage, '', '', lambda *_: None, base)
        self.assertIsNotNone(result)
        self.materials_to_remove.append(result)
        self.assertEqual(node.type, 'BSDF_GLASS')
        self.assertAlmostEqual(node.inputs['IOR'].default_value, 1.61)
        self.assertAlmostEqual(node.inputs['Color'].default_value[0], .3)

    def test_legacy_principled_migrates_without_losing_color(self):
        old, principled = material_utils.create_default_blender_material('OldTransmissionGlass')
        self.materials_to_remove.append(old)
        material_utils.set_principled_transmission(principled, 1.0)
        principled.inputs['Base Color'].default_value = (.1, .3, .5, 1)
        glass = upgrade_legacy_translucent(old)
        self.assertEqual(glass.type, 'BSDF_GLASS')
        self.assertAlmostEqual(glass.inputs['Color'].default_value[1], .3)
        self.assertIsNone(next((n for n in old.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None))
        self.assertEqual(NodeAnalyzer.find_surface_shader(old), glass)


if __name__ == '__main__':
    unittest.main()
