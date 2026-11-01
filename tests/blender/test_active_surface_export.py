"""The exported shader must be the one connected to the active output."""
import os
import tempfile
import unittest

import bpy
from pxr import Usd
from rtx_remix_importer.export_utils import NodeAnalyzer, ShaderSetup
from rtx_remix_importer.operators.export_operator import export_material


class TestActiveSurfaceExport(unittest.TestCase):
    def test_connected_shader_drives_material_and_type(self):
        material = bpy.data.materials.new('ActiveSurface')
        material.use_nodes = True
        nodes, links = material.node_tree.nodes, material.node_tree.links
        old = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
        old.inputs['Roughness'].default_value = .1
        active = nodes.new('ShaderNodeBsdfPrincipled')
        active.inputs['Roughness'].default_value = .9
        output = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL')
        reroute = nodes.new('NodeReroute')
        links.new(active.outputs['BSDF'], reroute.inputs[0])
        links.new(reroute.outputs[0], output.inputs['Surface'])
        stage = Usd.Stage.CreateInMemory()
        had_property = hasattr(bpy.types.Scene, 'remix_custom_texture_dir')
        if not had_property:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(default='textures')
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = '/RootNode/Looks/mat_1111111111111111'
                export_material(material, stage, tmp, os.path.join(tmp, 'mod.usda'), override_material_path=path)
                value = stage.GetPrimAtPath(path + '/Shader').GetAttribute('inputs:reflection_roughness_constant').Get()
                self.assertAlmostEqual(value, .9, places=5)
            active.inputs.get('Transmission Weight', active.inputs.get('Transmission')).default_value = 1
            self.assertEqual(ShaderSetup.detect_material_type(material), 'AperturePBR_Translucent')
        finally:
            if not had_property:
                del bpy.types.Scene.remix_custom_texture_dir
            bpy.data.materials.remove(material, do_unlink=True)

    def test_unsupported_surface_rejected(self):
        material = bpy.data.materials.new('MixedSurface')
        material.use_nodes = True
        nodes = material.node_tree.nodes
        output = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL')
        mix = nodes.new('ShaderNodeMixShader')
        material.node_tree.links.new(mix.outputs[0], output.inputs['Surface'])
        try:
            with self.assertRaisesRegex(ValueError, 'Unsupported material Surface'):
                NodeAnalyzer.find_surface_shader(material)
        finally:
            bpy.data.materials.remove(material, do_unlink=True)
