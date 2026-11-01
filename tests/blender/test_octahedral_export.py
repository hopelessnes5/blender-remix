"""Exercise the real Blender-image to BC5 normal export pipeline."""
import os
import subprocess
import tempfile
import unittest
import asyncio
from pathlib import Path

import bpy
from pxr import Usd
from rtx_remix_importer.core_utils import TextureProcessor
from rtx_remix_importer.export_utils import MaterialExporter, ProgressTracker, TextureExporter


class TestOctahedralExport(unittest.TestCase):
    def test_normal_export_replaces_old_dds_with_octahedral(self):
        image = bpy.data.images.new('plain.png', width=4, height=4, float_buffer=True)
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set([.8, .5, .9, 1] * 16)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                exporter = TextureExporter(tmp, os.path.join(tmp, 'mod.usda'))
                if not exporter.texture_processor.is_available():
                    self.skipTest('texconv unavailable')
                os.makedirs(exporter.textures_dir)
                path = Path(exporter.textures_dir) / 'plain.dds'
                path.write_bytes(b'old non-octahedral data')
                result = asyncio.run(exporter.export_texture_async(image, 'normal'))
                self.assertEqual(Path(result).name, 'plain.dds')
                self.assertEqual(path.read_bytes()[:4], b'DDS ')
        finally:
            bpy.data.images.remove(image)
    def test_exported_normal_is_marked_octahedral(self):
        material = bpy.data.materials.new('OctahedralTest')
        material.use_nodes = True
        image = bpy.data.images.new('NormalSource', width=4, height=4)
        nodes, links = material.node_tree.nodes, material.node_tree.links
        bsdf = next(node for node in nodes if node.type == 'BSDF_PRINCIPLED')
        texture = nodes.new('ShaderNodeTexImage')
        texture.image = image
        normal = nodes.new('ShaderNodeNormalMap')
        links.new(texture.outputs['Color'], normal.inputs['Color'])
        links.new(normal.outputs['Normal'], bsdf.inputs['Normal'])
        stage = Usd.Stage.CreateInMemory()
        shader = stage.DefinePrim('/Shader', 'Shader')
        exporter = MaterialExporter('', '')
        async def export_texture(_image, _kind, _callback):
            return 'normal.dds'
        exporter.texture_exporter.export_texture_async = export_texture
        try:
            asyncio.run(exporter._export_material_textures(bsdf, shader, ProgressTracker(6)))
            self.assertEqual(shader.GetAttribute('inputs:encoding').Get(), 0)
            self.assertEqual(shader.GetAttribute('inputs:normalmap_texture').Get().path, 'normal.dds')
        finally:
            bpy.data.materials.remove(material, do_unlink=True)
            bpy.data.images.remove(image)

    def test_bc5_contains_octahedral_coordinates(self):
        processor = TextureProcessor()
        if not processor.is_available():
            self.skipTest('texconv unavailable')
        image = bpy.data.images.new('TestNormal', width=4, height=4, float_buffer=True)
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set([.8, .5, .9, 1.0] * 16)
        try:
            self.assertEqual(processor.texture_filename(image), 'TestNormal.dds')
            image.name = 'Normal.001'
            self.assertEqual(processor.texture_filename(image), 'Normal.001.dds')
            image.name = 'Normal.png'
            self.assertEqual(processor.texture_filename(image), 'Normal.dds')
            with tempfile.TemporaryDirectory() as tmp:
                dds = os.path.join(tmp, 'normal.dds')
                self.assertTrue(processor.export_octahedral_normal(image, dds))
                header = Path(dds).read_bytes()
                self.assertEqual(header[:4], b'DDS ')
                png_dir = os.path.join(tmp, 'decoded')
                os.mkdir(png_dir)
                result = subprocess.run([processor.texconv_path, dds, '-o', png_dir, '-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-y', '-nologo'], capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                decoded = bpy.data.images.load(os.path.join(png_dir, 'normal.png'), check_existing=False)
                try:
                    decoded.colorspace_settings.name = 'Non-Color'
                    self.assertAlmostEqual(decoded.pixels[0], 5/7, delta=.03)
                    self.assertAlmostEqual(decoded.pixels[1], 5/7, delta=.03)
                finally:
                    bpy.data.images.remove(decoded)
        finally:
            bpy.data.images.remove(image)
