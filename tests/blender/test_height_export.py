"""Verify height texture and displacement export through Blender and USD."""
import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import bpy
from pxr import Sdf, Usd
from rtx_remix_importer.core_utils import ProgressTracker, TextureProcessor
from rtx_remix_importer.displacement import read_displacement
from rtx_remix_importer.export_utils import MaterialExporter
from rtx_remix_importer.operators.export_operator import export_material


class TestHeightExport(unittest.TestCase):
    def setUp(self):
        self.mat = bpy.data.materials.new('HeightMaterial')
        self.mat.use_nodes = True
        self.image = bpy.data.images.new('HeightTest.png', width=4, height=4, float_buffer=True)
        self.image.colorspace_settings.name = 'Non-Color'
        self.image.pixels.foreach_set([.25, .25, .25, .75] * 16)
        nodes = self.mat.node_tree.nodes
        self.bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
        self.output = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL')
        self.tex = nodes.new('ShaderNodeTexImage')
        self.tex.image = self.image
        self.disp = nodes.new('ShaderNodeDisplacement')
        self.disp.inputs['Midlevel'].default_value = .25
        self.disp.inputs['Scale'].default_value = .4
        links = self.mat.node_tree.links
        links.new(self.tex.outputs['Color'], self.disp.inputs['Height'])
        links.new(self.disp.outputs['Displacement'], self.output.inputs['Displacement'])
        self.added_property = not hasattr(bpy.types.Scene, 'remix_custom_texture_dir')
        if self.added_property:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(default='textures')

    def tearDown(self):
        if self.added_property:
            del bpy.types.Scene.remix_custom_texture_dir
        bpy.data.materials.remove(self.mat, do_unlink=True)
        bpy.data.images.remove(self.image, do_unlink=True)

    def test_reads_midlevel_and_scale(self):
        image, channel, inward, outward = read_displacement(self.mat)
        self.assertEqual(image, self.image)
        self.assertEqual(channel, 'Color')
        self.assertAlmostEqual(inward, .1)
        self.assertAlmostEqual(outward, .3)

    def test_rejects_unsupported_processing(self):
        math_node = self.mat.node_tree.nodes.new('ShaderNodeMath')
        self.mat.node_tree.links.new(math_node.outputs[0], self.disp.inputs['Height'])
        with self.assertRaisesRegex(ValueError, 'directly to an image'):
            read_displacement(self.mat)

    def test_alpha_socket_and_invalid_scale(self):
        self.mat.node_tree.links.new(self.tex.outputs['Alpha'], self.disp.inputs['Height'])
        self.assertEqual(read_displacement(self.mat)[1], 'Alpha')
        self.disp.inputs['Scale'].default_value = -.2
        with self.assertRaisesRegex(ValueError, 'nonnegative'):
            read_displacement(self.mat)

    def test_value_node_for_scale(self):
        value = self.mat.node_tree.nodes.new('ShaderNodeValue')
        value.outputs[0].default_value = .2
        self.mat.node_tree.links.new(value.outputs[0], self.disp.inputs['Scale'])
        self.assertAlmostEqual(read_displacement(self.mat)[2], .05)
        self.assertAlmostEqual(read_displacement(self.mat)[3], .15)

    def test_direct_export_contains_height_and_parameters(self):
        if not TextureProcessor().is_available():
            self.skipTest('texconv unavailable')
        with tempfile.TemporaryDirectory() as root:
            stage = Usd.Stage.CreateInMemory()
            path = export_material(self.mat, stage, root, os.path.join(root, 'mod.usda'),
                                   override_material_path='/RootNode/Looks/mat_0123456789ABCDEF')
            shader = stage.GetPrimAtPath(path.AppendChild('Shader'))
            asset = shader.GetAttribute('inputs:height_texture').Get().path
            self.assertEqual(Path(asset).name, 'HeightTest.dds')
            self.assertTrue((Path(root) / 'textures' / 'HeightTest.dds').is_file())
            self.assertAlmostEqual(shader.GetAttribute('inputs:displace_in').Get(), .1)
            self.assertAlmostEqual(shader.GetAttribute('inputs:displace_out').Get(), .3)

    def test_invalid_displacement_does_not_author_usd(self):
        math_node = self.mat.node_tree.nodes.new('ShaderNodeMath')
        self.mat.node_tree.links.new(math_node.outputs[0], self.disp.inputs['Height'])
        with tempfile.TemporaryDirectory() as root:
            stage = Usd.Stage.CreateInMemory()
            before = stage.GetRootLayer().ExportToString()
            with self.assertRaises(ValueError):
                export_material(self.mat, stage, root, os.path.join(root, 'mod.usda'),
                                override_material_path='/RootNode/Looks/mat_0123456789ABCDEF')
            self.assertEqual(stage.GetRootLayer().ExportToString(), before)

    def test_texture_name_collision_does_not_overwrite_other_texture(self):
        self.mat.node_tree.links.new(self.tex.outputs['Color'], self.bsdf.inputs['Base Color'])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'textures' / 'HeightTest.dds'
            path.parent.mkdir()
            path.write_bytes(b'existing texture')
            stage = Usd.Stage.CreateInMemory()
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                export_material(self.mat, stage, root, os.path.join(root, 'mod.usda'),
                                override_material_path='/RootNode/Looks/mat_0123456789ABCDEF')
            self.assertEqual(path.read_bytes(), b'existing texture')

    def test_export_does_not_change_capture_and_retains_required_default_override(self):
        if not TextureProcessor().is_available():
            self.skipTest('texconv unavailable')
        capture = Sdf.Layer.CreateAnonymous('capture.usda')
        capture.customLayerData = {'lightspeed_layer_type': 'capture'}
        mod = Sdf.Layer.CreateAnonymous('mod.usda')
        mod.subLayerPaths.append(capture.identifier)
        stage = Usd.Stage.Open(mod)
        stage.SetEditTarget(capture)
        shader = stage.DefinePrim('/RootNode/Looks/mat_0123456789ABCDEF/Shader', 'Shader')
        shader.CreateAttribute('inputs:displace_in', Sdf.ValueTypeNames.Float).Set(.1)
        before = capture.ExportToString()
        stage.SetEditTarget(mod)
        self.disp.inputs['Midlevel'].default_value = .5
        self.disp.inputs['Scale'].default_value = .1
        with tempfile.TemporaryDirectory() as root:
            path = export_material(self.mat, stage, root, os.path.join(root, 'mod.usda'),
                                   override_material_path='/RootNode/Looks/mat_0123456789ABCDEF')
            self.assertAlmostEqual(stage.GetPrimAtPath(path.AppendChild('Shader')).GetAttribute('inputs:displace_in').Get(), .05)
            self.assertEqual(capture.ExportToString(), before)

    def test_secondary_export_contains_height_and_parameters(self):
        if not TextureProcessor().is_available():
            self.skipTest('texconv unavailable')
        with tempfile.TemporaryDirectory() as root:
            stage = Usd.Stage.CreateInMemory()
            shader = stage.DefinePrim('/Shader', 'Shader')
            exporter = MaterialExporter(root, os.path.join(root, 'mod.usda'))
            asyncio.run(exporter._export_material_textures(self.bsdf, shader, ProgressTracker(6)))
            self.assertEqual(Path(shader.GetAttribute('inputs:height_texture').Get().path).name, 'HeightTest.dds')
            self.assertAlmostEqual(shader.GetAttribute('inputs:displace_in').Get(), .1)
            self.assertAlmostEqual(shader.GetAttribute('inputs:displace_out').Get(), .3)

    def test_bc4_preserves_color_and_alpha_channels(self):
        processor = TextureProcessor()
        if not processor.is_available():
            self.skipTest('texconv unavailable')
        with tempfile.TemporaryDirectory() as root:
            dds = os.path.join(root, 'height.dds')
            for channel, expected in [('Color', .25), ('Alpha', .75)]:
                with self.subTest(channel=channel):
                    self.assertTrue(processor.export_height_texture(self.image, dds, channel))
                    decoded_dir = os.path.join(root, channel)
                    os.mkdir(decoded_dir)
                    result = subprocess.run([processor.texconv_path, dds, '-o', decoded_dir, '-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-y', '-nologo'], capture_output=True, timeout=60)
                    self.assertEqual(result.returncode, 0)
                    decoded = bpy.data.images.load(os.path.join(decoded_dir, 'height.png'), check_existing=False)
                    try:
                        decoded.colorspace_settings.name = 'Non-Color'
                        self.assertAlmostEqual(decoded.pixels[0], expected, delta=.03)
                    finally:
                        bpy.data.images.remove(decoded)
