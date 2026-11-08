"""Tests for direct AperturePBR_Translucent -> Blender Glass BSDF mapping.

Run using Blender's bundled Python: blender -b --python <test-runner>.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import bpy
from pxr import Gf, Sdf, Usd, UsdShade

from rtx_remix_importer import material_utils, mod_apply_utils
from rtx_remix_importer.export_utils import NodeAnalyzer, ShaderSetup
from rtx_remix_importer.glass_nodes import (
    ensure_emission,
    emission_node,
    ensure_volume_absorption,
    remove_volume_absorption,
    restore_opaque_principled,
    upgrade_legacy_translucent,
    volume_transmittance,
)


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
        self.assertEqual(ensure_emission(mat).as_pointer(), emission.as_pointer())
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


VOLUME_LABEL = 'RTX Remix Translucent Volume Absorption'


class GlassVolumeTests(unittest.TestCase):
    """The absorption-only MDL volume as a labeled Volume Absorption node.

    sigma = -ln(transmittance_color) / transmittance_measurement_distance
    (MDL body) is encoded into the node as sigma = Density * (1 - Color)
    (Cycles OSL), and export reads the connected volume back into a
    transmittance: the volume branch, not the Glass tint, is the authority.
    """

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

    def midpoint_material(self, name):
        mat, node = material_utils.create_default_blender_material(name)
        self.materials_to_remove.append(mat)
        return mat, node

    def input(self, name, typename, value):
        self.shader.CreateInput(name, typename).Set(value)

    def labeled_volume(self, mat):
        return next((n for n in mat.node_tree.nodes
                     if n.type == 'VOLUME_ABSORPTION' and n.label == VOLUME_LABEL), None)

    def output(self, mat):
        return next(n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL')

    def link_user_volume(self, mat):
        volume = mat.node_tree.nodes.new('ShaderNodeVolumeAbsorption')
        volume.inputs['Density'].default_value = 3.0
        volume.inputs['Color'].default_value = (.2, .4, .6, 1.0)
        mat.node_tree.links.new(volume.outputs['Volume'], self.output(mat).inputs['Volume'])
        return volume

    def test_reset_creates_default_labeled_volume(self):
        mat, glass = self.material('RemixVolumeDefault')
        volume = self.labeled_volume(mat)
        self.assertIsNotNone(volume)
        output = self.output(mat)
        self.assertTrue(output.inputs['Volume'].is_linked)
        self.assertEqual(output.inputs['Volume'].links[0].from_node.as_pointer(), volume.as_pointer())
        self.assertAlmostEqual(volume.inputs['Density'].default_value, 1.0)
        expected = 1.0 + math.log(0.97)  # 1 - (-ln 0.97) at the MDL defaults
        for channel in range(3):
            self.assertAlmostEqual(volume.inputs['Color'].default_value[channel], expected, places=5)
        rgb, note = volume_transmittance(mat, 1.0)
        self.assertIsNone(note)
        for channel in range(3):
            self.assertAlmostEqual(rgb[channel], 0.97, places=5)

    def test_capture_encodes_absorption_for_round_trip(self):
        mat, glass = self.material('RemixVolumeStrong')
        self.input('transmittance_color', Sdf.ValueTypeNames.Color3f, Gf.Vec3f(.05, .2, .5))
        self.input('transmittance_measurement_distance', Sdf.ValueTypeNames.Float, 2.0)
        material_utils.process_pbr(self.shader, mat, glass, '')
        volume = self.labeled_volume(mat)
        self.assertIsNotNone(volume)
        sigma = [-math.log(c) / 2.0 for c in (.05, .2, .5)]
        density = max(1.0, max(sigma))
        self.assertAlmostEqual(volume.inputs['Density'].default_value, density, places=5)
        for channel in range(3):
            self.assertAlmostEqual(volume.inputs['Color'].default_value[channel],
                                   1.0 - sigma[channel] / density, places=5)
        rgb, note = volume_transmittance(mat, 2.0)
        self.assertIsNone(note)
        for channel, expected in enumerate((.05, .2, .5)):
            self.assertAlmostEqual(rgb[channel], expected, places=4)

    def test_capture_distance_only_keeps_default_volume(self):
        mat, glass = self.material('RemixVolumeDistanceOnly')
        before = tuple(self.labeled_volume(mat).inputs['Color'].default_value)
        self.input('transmittance_measurement_distance', Sdf.ValueTypeNames.Float, 2.0)
        material_utils.process_pbr(self.shader, mat, glass, '')
        after = tuple(self.labeled_volume(mat).inputs['Color'].default_value)
        self.assertEqual(after, before)

    def test_ensure_volume_is_idempotent(self):
        mat, glass = self.material('RemixVolumeIdempotent')
        first = self.labeled_volume(mat)
        again = ensure_volume_absorption(mat, (.5, .5, .5), 1.0)
        self.assertEqual(again.as_pointer(), first.as_pointer())
        self.assertEqual(sum(n.type == 'VOLUME_ABSORPTION' for n in mat.node_tree.nodes), 1)
        self.assertEqual(self.output(mat).inputs['Volume'].links[0].from_node.as_pointer(),
                         first.as_pointer())

    def test_user_volume_stays_authority(self):
        mat, glass = self.material('RemixVolumeUser')
        mine = self.link_user_volume(mat)
        returned = ensure_volume_absorption(mat, (.5, .5, .5), 1.0)
        self.assertEqual(returned.as_pointer(), mine.as_pointer())
        self.assertAlmostEqual(mine.inputs['Density'].default_value, 3.0)
        self.assertAlmostEqual(mine.inputs['Color'].default_value[0], .2)
        rgb, note = volume_transmittance(mat, 1.0)
        self.assertIsNone(note)
        for channel in range(3):
            self.assertAlmostEqual(rgb[channel], math.exp(-3.0 * (1 - (.2, .4, .6)[channel])), places=4)

    def test_remove_drops_only_labeled_branch(self):
        mat, glass = self.material('RemixVolumeRemove')
        self.link_user_volume(mat)
        self.assertTrue(remove_volume_absorption(mat))
        self.assertIsNone(self.labeled_volume(mat))
        self.assertTrue(self.output(mat).inputs['Volume'].is_linked)
        self.assertFalse(remove_volume_absorption(mat))

    def test_principled_volume_absorption_reads_back(self):
        mat, glass = self.material('RemixVolumePrincipled')
        principled_volume = mat.node_tree.nodes.new('ShaderNodeVolumePrincipled')
        principled_volume.inputs['Color'].default_value = (0, 0, 0, 1)
        principled_volume.inputs['Absorption Color'].default_value = (.25, .25, .25, 1)
        principled_volume.inputs['Density'].default_value = 2.0
        mat.node_tree.links.new(principled_volume.outputs['Volume'], self.output(mat).inputs['Volume'])
        rgb, note = volume_transmittance(mat, 1.0)
        self.assertIsNone(note)
        # sigma = density * (1 - sqrt(0.25)) = 1.0 per the Cycles OSL shader.
        for channel in range(3):
            self.assertAlmostEqual(rgb[channel], math.exp(-1.0), places=4)
        return_handler = ensure_volume_absorption(mat, (.5, .5, .5), 1.0)
        self.assertEqual(return_handler.as_pointer(), principled_volume.as_pointer())

    def test_principled_volume_scattering_is_unsupported(self):
        mat, glass = self.material('RemixVolumeScatter')
        principled_volume = mat.node_tree.nodes.new('ShaderNodeVolumePrincipled')
        principled_volume.inputs['Color'].default_value = (.5, .5, .5, 1)
        mat.node_tree.links.new(principled_volume.outputs['Volume'], self.output(mat).inputs['Volume'])
        rgb, note = volume_transmittance(mat, 1.0)
        self.assertIsNone(rgb)
        self.assertIn('absorption only', note)

    def test_unsupported_volume_node_gets_note(self):
        mat, glass = self.material('RemixVolumeUnsupported')
        emissive = mat.node_tree.nodes.new('ShaderNodeEmission')
        mat.node_tree.links.new(emissive.outputs['Emission'], self.output(mat).inputs['Volume'])
        rgb, note = volume_transmittance(mat, 1.0)
        self.assertIsNone(rgb)
        self.assertIn('Unsupported volume node', note)

    def test_restore_opaque_principled_rebuilds_graph(self):
        mat, glass = self.material('RemixVolumeRestoreOpaque')
        glass.inputs['Color'].default_value = (.1, .2, .3, 1)
        emission = ensure_emission(mat)
        emission.inputs['Strength'].default_value = 5.0
        principled = restore_opaque_principled(mat)
        self.assertEqual(principled.type, 'BSDF_PRINCIPLED')
        self.assertAlmostEqual(principled.inputs['Base Color'].default_value[2], .3)
        self.assertAlmostEqual(principled.inputs['Emission Strength'].default_value, 5.0)
        self.assertEqual(self.output(mat).inputs['Surface'].links[0].from_node.as_pointer(),
                         principled.as_pointer())
        for kind in ('BSDF_GLASS', 'VOLUME_ABSORPTION', 'EMISSION', 'ADD_SHADER'):
            self.assertEqual(sum(n.type == kind for n in mat.node_tree.nodes), 0)

    def test_restore_existing_principled_drops_labeled_volume(self):
        mat, principled = self.midpoint_material('RemixVolumeRestorePrincipled')
        node = mat.node_tree.nodes.new('ShaderNodeVolumeAbsorption')
        node.label = VOLUME_LABEL
        mat.node_tree.links.new(node.outputs['Volume'], self.output(mat).inputs['Volume'])
        returned = restore_opaque_principled(mat)
        self.assertEqual(returned.as_pointer(), principled.as_pointer())
        self.assertEqual(sum(n.type == 'VOLUME_ABSORPTION' for n in mat.node_tree.nodes), 0)


if __name__ == '__main__':
    unittest.main()
