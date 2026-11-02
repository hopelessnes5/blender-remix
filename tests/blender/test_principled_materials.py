"""Material translation checks, run with Blender's bundled USD bindings."""

import unittest
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import bpy
from pxr import Gf, Sdf, Usd, UsdShade

from rtx_remix_importer import material_utils, mod_apply_utils
from rtx_remix_importer.export_utils import MaterialExporter, ProgressTracker, ShaderSetup


class TestPrincipledMaterials(unittest.TestCase):
    def setUp(self):
        self.stage = Usd.Stage.CreateInMemory()
        self.shader = UsdShade.Shader.Define(self.stage, '/Material/Shader')
        self.material, self.node = material_utils.create_default_blender_material('TestPrincipled')

    def tearDown(self):
        bpy.data.materials.remove(self.material, do_unlink=True)

    def input(self, name, value_type, value):
        self.shader.CreateInput(name, value_type).Set(value)

    def test_import_defaults_to_full_roughness(self):
        material_utils.process_pbr(self.shader, self.material, self.node, '')
        self.assertAlmostEqual(self.node.inputs['Roughness'].default_value, 1.0)

    def test_imports_constants_and_gates_opacity(self):
        self.input('diffuse_color_constant', Sdf.ValueTypeNames.Color3f, Gf.Vec3f(.2, .3, .4))
        self.input('metallic_constant', Sdf.ValueTypeNames.Float, .7)
        self.input('reflection_roughness_constant', Sdf.ValueTypeNames.Float, .25)
        self.input('opacity_constant', Sdf.ValueTypeNames.Float, .3)
        self.input('enable_opacity', Sdf.ValueTypeNames.Bool, False)
        material_utils.process_pbr(self.shader, self.material, self.node, '')
        self.assertAlmostEqual(self.node.inputs['Base Color'].default_value[0], .2)
        self.assertAlmostEqual(self.node.inputs['Metallic'].default_value, .7)
        self.assertAlmostEqual(self.node.inputs['Roughness'].default_value, .25)
        self.assertAlmostEqual(self.node.inputs['Alpha'].default_value, 1.0)
        self.input('enable_opacity', Sdf.ValueTypeNames.Bool, True)
        material_utils.process_pbr(self.shader, self.material, self.node, '')
        self.assertAlmostEqual(self.node.inputs['Alpha'].default_value, .3)

    def test_transmission_threshold(self):
        transmission = material_utils.get_principled_transmission(self.node)
        transmission.default_value = .49
        self.assertEqual(ShaderSetup.detect_material_type(self.material), 'AperturePBR_Opacity')
        transmission.default_value = .5
        self.assertEqual(ShaderSetup.detect_material_type(self.material), 'AperturePBR_Translucent')

    def test_translucent_override_detection(self):
        self.input('transmittance_texture', Sdf.ValueTypeNames.Asset, Sdf.AssetPath('glass.dds'))
        self.assertTrue(material_utils._is_translucent_shader(self.shader))
        value, _ = mod_apply_utils.get_mod_input_value_util(self.shader, 'inputs:transmittance_texture')
        self.assertEqual(value.path, 'glass.dds')

    def test_mod_opacity_disable_resets_alpha(self):
        self.node.inputs['Alpha'].default_value = .2
        self.input('enable_opacity', Sdf.ValueTypeNames.Bool, False)
        mod_apply_utils.process_mod_pbr_util(self.shader, self.material, self.node, '', '', lambda *args: None)
        self.assertAlmostEqual(self.node.inputs['Alpha'].default_value, 1.0)

    def test_partial_mod_keeps_existing_translucent_material(self):
        material_utils.get_principled_transmission(self.node).default_value = 1.0
        self.node.inputs['Base Color'].default_value = (.2, .3, .4, 1)
        self.input('reflection_roughness_constant', Sdf.ValueTypeNames.Float, .3)
        result, node = mod_apply_utils.create_mod_material_nodes_util(
            '/Material', self.stage, '', '', lambda *args: None, self.material)
        try:
            self.assertAlmostEqual(material_utils.get_principled_transmission(node).default_value, 1.0)
            self.assertAlmostEqual(node.inputs['Base Color'].default_value[0], .2)
            self.assertAlmostEqual(node.inputs['Roughness'].default_value, .3)
        finally:
            bpy.data.materials.remove(result, do_unlink=True)

    def test_export_constants_and_opacity(self):
        self.node.inputs['Base Color'].default_value = (.2, .3, .4, 1)
        self.node.inputs['Alpha'].default_value = .35
        exporter = MaterialExporter('', '')
        asyncio.run(exporter._export_material_textures(self.node, self.shader.GetPrim(), ProgressTracker(6)))
        self.assertAlmostEqual(self.shader.GetInput('diffuse_color_constant').Get()[0], .2)
        self.assertTrue(self.shader.GetInput('enable_opacity').Get())
        self.assertAlmostEqual(self.shader.GetInput('opacity_constant').Get(), .35)

    def test_export_omits_mdl_defaults_but_keeps_changed_blender_values(self):
        self.node.inputs['Base Color'].default_value = (.2, .2, .2, 1)
        self.node.inputs['Metallic'].default_value = 0.0
        self.node.inputs['Roughness'].default_value = .5
        exporter = MaterialExporter('', '')
        asyncio.run(exporter._export_material_textures(self.node, self.shader.GetPrim(), ProgressTracker(6)))
        self.assertFalse(self.shader.GetInput('diffuse_color_constant'))
        self.assertFalse(self.shader.GetInput('metallic_constant'))
        self.assertFalse(self.shader.GetInput('reflection_roughness_constant'))
        self.node.inputs['Base Color'].default_value = (.8, .8, .8, 1)
        asyncio.run(exporter._export_material_textures(self.node, self.shader.GetPrim(), ProgressTracker(6)))
        self.assertAlmostEqual(self.shader.GetInput('diffuse_color_constant').Get()[0], .8)

    def test_export_translucent_custom_properties(self):
        self.material['rtx_thin_walled'] = True
        self.material['rtx_thin_wall_thickness'] = .25
        exporter = MaterialExporter('', '')
        asyncio.run(exporter._export_material_textures(self.node, self.shader.GetPrim(), ProgressTracker(6), 'AperturePBR_Translucent'))
        self.assertTrue(self.shader.GetInput('thin_walled').Get())
        self.assertAlmostEqual(self.shader.GetInput('thin_wall_thickness').Get(), .25)


if __name__ == '__main__':
    unittest.main(argv=['test_principled_materials'])
