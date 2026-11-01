"""Scalar channels survive import and actual BC4 texture export."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import asyncio

import bpy
from pxr import Usd
from rtx_remix_importer.material_utils import process_input
from rtx_remix_importer.mod_apply_utils import process_mod_input_util
from rtx_remix_importer.operators.export_operator import export_material
from rtx_remix_importer.core_utils import TextureProcessor
from rtx_remix_importer.export_utils import MaterialExporter, ProgressTracker


class TestScalarChannels(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.material = bpy.data.materials.new('ScalarRegression')
        self.material.use_nodes = True
        self.nodes = self.material.node_tree.nodes
        self.links = self.material.node_tree.links
        self.bsdf = next(n for n in self.nodes if n.type == 'BSDF_PRINCIPLED')
        self.image = bpy.data.images.new('scalar', width=4, height=4, float_buffer=True)
        self.image.colorspace_settings.name = 'Non-Color'
        self.image.alpha_mode = 'CHANNEL_PACKED'
        self.image.pixels.foreach_set([.2, .4, .6, .8] * 16)
        self.had_property = hasattr(bpy.types.Scene, 'remix_custom_texture_dir')
        if not self.had_property:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(default='textures')

    def tearDown(self):
        bpy.data.materials.remove(self.material, do_unlink=True)
        bpy.data.images.remove(self.image)
        if not self.had_property:
            del bpy.types.Scene.remix_custom_texture_dir
        self.tmp.cleanup()

    def test_import_and_mod_apply_read_red(self):
        path = os.path.join(self.tmp.name, 'rough.png')
        self.image.filepath_raw = path
        self.image.file_format = 'PNG'
        self.image.save()
        for name in ('Roughness', 'Metallic'):
            process_input(path, name, self.nodes, self.links, self.bsdf, name, os.path.join(self.tmp.name, 'capture.usda'), is_non_color=True)
            link = self.bsdf.inputs[name].links[0]
            self.assertEqual(link.from_node.type, 'SEPARATE_COLOR')
            self.assertEqual(link.from_socket.name, 'Red')
        # Updating a legacy alpha link must replace it with the red channel.
        texture = self.nodes.new('ShaderNodeTexImage')
        texture.image = self.image
        self.links.new(texture.outputs['Alpha'], self.bsdf.inputs['Roughness'])
        process_mod_input_util(path, 'roughness', self.nodes, self.links, self.bsdf, 'Roughness', self.tmp.name, os.path.join(self.tmp.name, 'mod.usda'), is_non_color=True)
        self.assertEqual(self.bsdf.inputs['Roughness'].links[0].from_socket.name, 'Red')

    def test_alpha_and_separate_green_export_as_bc4(self):
        processor = TextureProcessor()
        if not processor.is_available():
            self.skipTest('texconv unavailable')
        for channel, expected in [('Alpha', .8), ('Red', .2), ('Green', .4), ('Blue', .6)]:
            with self.subTest(channel=channel):
                texture = self.nodes.new('ShaderNodeTexImage')
                texture.image = self.image
                if channel == 'Alpha':
                    self.links.new(texture.outputs['Alpha'], self.bsdf.inputs['Alpha'])
                else:
                    separate = self.nodes.new('ShaderNodeSeparateColor')
                    self.links.new(texture.outputs['Color'], separate.inputs['Color'])
                    self.links.new(separate.outputs[channel], self.bsdf.inputs['Roughness'])
                stage = Usd.Stage.CreateInMemory()
                path = '/RootNode/Looks/mat_1111111111111111'
                export_material(self.material, stage, self.tmp.name, os.path.join(self.tmp.name, 'mod.usda'), override_material_path=path)
                dds = os.path.join(self.tmp.name, 'textures', 'scalar.dds')
                decoded_dir = os.path.join(self.tmp.name, 'decoded')
                os.makedirs(decoded_dir, exist_ok=True)
                result = subprocess.run([processor.texconv_path, dds, '-o', decoded_dir, '-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-y', '-nologo'], capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                decoded = bpy.data.images.load(os.path.join(decoded_dir, 'scalar.png'), check_existing=False)
                try:
                    decoded.colorspace_settings.name = 'Non-Color'
                    self.assertAlmostEqual(decoded.pixels[0], expected, delta=.035)
                finally:
                    bpy.data.images.remove(decoded)
                # Remove previous links so this iteration exports one scalar texture.
                for socket in (self.bsdf.inputs['Alpha'], self.bsdf.inputs['Roughness']):
                    for link in list(socket.links):
                        self.links.remove(link)

    def test_async_material_export_preserves_alpha(self):
        processor = TextureProcessor()
        if not processor.is_available():
            self.skipTest('texconv unavailable')
        texture = self.nodes.new('ShaderNodeTexImage')
        texture.image = self.image
        self.links.new(texture.outputs['Alpha'], self.bsdf.inputs['Alpha'])
        stage = Usd.Stage.CreateInMemory()
        shader = stage.DefinePrim('/Shader', 'Shader')
        exporter = MaterialExporter(self.tmp.name, os.path.join(self.tmp.name, 'mod.usda'))
        asyncio.run(exporter._export_material_textures(self.bsdf, shader, ProgressTracker(6)))
        dds = os.path.join(exporter.texture_exporter.textures_dir, 'scalar.dds')
        result = subprocess.run([processor.texconv_path, dds, '-o', self.tmp.name, '-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-y', '-nologo'], capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        decoded = bpy.data.images.load(os.path.join(self.tmp.name, 'scalar.png'), check_existing=False)
        try:
            decoded.colorspace_settings.name = 'Non-Color'
            self.assertAlmostEqual(decoded.pixels[0], .8, delta=.035)
        finally:
            bpy.data.images.remove(decoded)

    def test_unsupported_scalar_processing_is_rejected(self):
        texture = self.nodes.new('ShaderNodeTexImage')
        texture.image = self.image
        math = self.nodes.new('ShaderNodeMath')
        self.links.new(texture.outputs['Alpha'], math.inputs[0])
        self.links.new(math.outputs[0], self.bsdf.inputs['Roughness'])
        with self.assertRaisesRegex(ValueError, 'Unsupported scalar texture node'):
            export_material(self.material, Usd.Stage.CreateInMemory(), self.tmp.name, os.path.join(self.tmp.name, 'mod.usda'), override_material_path='/RootNode/Looks/mat_1111111111111111')
