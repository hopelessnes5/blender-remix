"""Verify the normal encoding setting and actual DDS exports."""
import asyncio
import os
import subprocess
import tempfile
import unittest

import bpy
from pxr import Usd
from rtx_remix_importer.core_utils import TextureProcessor
from rtx_remix_importer.export_utils import MaterialExporter, ProgressTracker


class TestNormalEncodingModes(unittest.TestCase):
    def test_normal_formats_preserve_selected_channels(self):
        processor = TextureProcessor()
        if not processor.is_available():
            self.skipTest('texconv unavailable')
        source = bpy.data.images.new('EncodingTest', width=4, height=4, float_buffer=True)
        source.colorspace_settings.name = 'Non-Color'
        source.pixels.foreach_set([.72, .36, .83, 1] * 16)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                for mode in ('OCT_EXISTING', 'OPENGL', 'DIRECTX'):
                    with self.subTest(mode=mode):
                        dds = os.path.join(tmp, mode + '.dds')
                        self.assertTrue(processor.export_normal_map(source, dds, mode))
                        raw = open(dds, 'rb').read(148)
                        self.assertEqual(raw[:4], b'DDS ')
                        # DXGI_FORMAT_BC7_UNORM is 98; BC5 may use legacy ATI2.
                        if mode != 'OCT_EXISTING':
                            self.assertEqual(raw[84:88], b'DX10')
                            self.assertEqual(int.from_bytes(raw[128:132], 'little'), 98)
                        decoded_dir = os.path.join(tmp, mode)
                        os.mkdir(decoded_dir)
                        result = subprocess.run([processor.texconv_path, dds, '-o', decoded_dir, '-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-y', '-nologo'], capture_output=True, timeout=60)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        decoded = bpy.data.images.load(os.path.join(decoded_dir, mode + '.png'), check_existing=False)
                        try:
                            decoded.colorspace_settings.name = 'Non-Color'
                            for index, expected in enumerate((.72, .36, .83)[:2 if mode == 'OCT_EXISTING' else 3]):
                                self.assertAlmostEqual(decoded.pixels[index], expected, delta=.03)
                        finally:
                            bpy.data.images.remove(decoded)
        finally:
            bpy.data.images.remove(source)

    def test_material_encoding_matches_selection(self):
        from rtx_remix_importer import normal_export
        from unittest.mock import patch
        material = bpy.data.materials.new('EncodingMaterial')
        material.use_nodes = True
        image = bpy.data.images.new('EncodingSource', width=4, height=4)
        nodes, links = material.node_tree.nodes, material.node_tree.links
        bsdf = next(node for node in nodes if node.type == 'BSDF_PRINCIPLED')
        texture = nodes.new('ShaderNodeTexImage')
        texture.image = image
        normal = nodes.new('ShaderNodeNormalMap')
        links.new(texture.outputs['Color'], normal.inputs['Color'])
        links.new(normal.outputs['Normal'], bsdf.inputs['Normal'])
        try:
            for mode, expected in [('OCT_CONVERT', 0), ('OCT_EXISTING', 0), ('OPENGL', 1), ('DIRECTX', 2)]:
                with self.subTest(mode=mode), patch.object(normal_export, 'current_mode', return_value=mode):
                    stage = Usd.Stage.CreateInMemory()
                    shader = stage.DefinePrim('/Shader', 'Shader')
                    exporter = MaterialExporter('', '')
                    async def export_texture(*args):
                        return 'normal.dds'
                    exporter.texture_exporter.export_texture_async = export_texture
                    asyncio.run(exporter._export_material_textures(bsdf, shader, ProgressTracker(6)))
                    self.assertEqual(shader.GetAttribute('inputs:encoding').Get(), expected)
        finally:
            bpy.data.materials.remove(material, do_unlink=True)
            bpy.data.images.remove(image)
