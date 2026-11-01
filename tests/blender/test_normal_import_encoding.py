"""Verify Remix normal-map decoding and export roundtrip."""
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import bpy
import numpy as np
from pxr import Sdf, Usd, UsdShade
from rtx_remix_importer.core_utils import TextureProcessor
from rtx_remix_importer.normal_import import decode_to_opengl, effective_encoding
from rtx_remix_importer.nvidia_core.octahedral import convert_float_to_octahedral_in_place


class TestNormalImportEncoding(unittest.TestCase):
    def test_inverse_matches_nvidia_encode(self):
        vectors = np.array([[[.8, .5, .9, 1.], [.3, .7, .95, 1.], [.5, .5, 1., 1.], [.5, 1., .5, 1.]]], dtype=np.float32)
        encoded = vectors.copy()
        convert_float_to_octahedral_in_place(encoded, opengl=True)
        source = bpy.data.images.new('OctInput', width=4, height=1, float_buffer=True)
        source.colorspace_settings.name = 'Non-Color'
        source.pixels.foreach_set(encoded.ravel())
        decoded = None
        try:
            decoded = decode_to_opengl(source, 0)
            self.assertIsNotNone(decoded.packed_file)
            actual = np.array(decoded.pixels[:]).reshape(1, 4, 4)
            expected = vectors[:, :, :3] * 2 - 1
            expected /= np.linalg.norm(expected, axis=2, keepdims=True)
            np.testing.assert_allclose(actual[:, :, :3], (expected + 1) / 2, atol=1e-5)
            self.assertTrue(decoded['remix_normal_decoded_opengl'])
            self.assertEqual(decoded['remix_normal_source_name'], source.name)
            self.assertEqual(TextureProcessor().texture_filename(decoded), 'OctInput.dds')
        finally:
            if decoded: bpy.data.images.remove(decoded)
            bpy.data.images.remove(source)

    def test_dx_green_inversion_and_no_guesses(self):
        image = bpy.data.images.new('DXInput', width=1, height=1, float_buffer=True)
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set([.6, .2, .9, 1])
        decoded = None
        try:
            self.assertIs(decode_to_opengl(image, None), image)
            self.assertIs(decode_to_opengl(image, 1), image)
            decoded = decode_to_opengl(image, 2)
            self.assertAlmostEqual(decoded.pixels[1], .8, places=5)
            self.assertAlmostEqual(image.pixels[1], .2, places=5)
        finally:
            if decoded: bpy.data.images.remove(decoded)
            bpy.data.images.remove(image)

    def test_decoded_texture_exports_original_filename(self):
        image = bpy.data.images.new('wall_normal.png', width=1, height=1, float_buffer=True)
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set([.5, .5, 1, 1])
        decoded = None
        try:
            decoded = decode_to_opengl(image, 2)
            self.assertEqual(TextureProcessor().texture_filename(decoded), 'wall_normal.dds')
            self.assertEqual(TextureProcessor().texture_filename(image), 'wall_normal.dds')
            decoded.name = 'custom_normal.png'
            self.assertEqual(TextureProcessor().texture_filename(decoded), 'custom_normal.dds')
        finally:
            if decoded: bpy.data.images.remove(decoded)
            bpy.data.images.remove(image)

    def test_fallback_name_provenance_and_source_protection(self):
        from rtx_remix_importer.texture_loader import _convert_dds_to_png
        processor = TextureProcessor()
        if not processor.is_available(): self.skipTest('texconv unavailable')
        image = bpy.data.images.new('InitialNormal', width=4, height=4, float_buffer=True)
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set([.6, .2, .9, 1] * 16)
        fallback = decoded = renamed = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = os.path.join(tmp, 'original.dds')
                self.assertTrue(processor.export_normal_map(image, source, 'OPENGL'))
                with open(source, 'rb') as stream: original = stream.read()
                fallback = _convert_dds_to_png(source, True, True)
                self.assertTrue(fallback.name.startswith('original_converted'))
                self.assertEqual(processor.texture_filename(fallback), 'original.dds')
                decoded = decode_to_opengl(fallback, 2)
                self.assertEqual(processor.texture_filename(decoded), 'original.dds')
                self.assertEqual(decoded['remix_normal_source_path'], source)
                with self.assertRaisesRegex(ValueError, 'overwrite'):
                    processor.export_normal_map(decoded, source, 'OPENGL')
                with open(source, 'rb') as stream: self.assertEqual(stream.read(), original)
                fallback.name = 'custom_normal.png'
                self.assertEqual(processor.texture_filename(fallback), 'custom_normal.dds')
                renamed = decode_to_opengl(fallback, 2)
                self.assertEqual(processor.texture_filename(renamed), 'custom_normal.dds')
        finally:
            for obj in (renamed, decoded, fallback, image):
                if obj: bpy.data.images.remove(obj)

    def test_usd_encoding_and_mdl_default(self):
        stage = Usd.Stage.CreateInMemory()
        shader = UsdShade.Shader.Define(stage, '/Shader')
        self.assertIsNone(effective_encoding(shader))
        shader.GetPrim().CreateAttribute('info:mdl:sourceAsset', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('AperturePBR_Opacity.mdl'))
        self.assertEqual(effective_encoding(shader), 0)
        shader.CreateInput('encoding', Sdf.ValueTypeNames.Int).Set(2)
        self.assertEqual(effective_encoding(shader), 2)
        self.assertEqual(effective_encoding(shader.GetPrim()), 2)

    def test_decoded_image_export_modes(self):
        processor = TextureProcessor()
        if not processor.is_available(): self.skipTest('texconv unavailable')
        image = bpy.data.images.new('DecodedNormal', width=4, height=4, float_buffer=True)
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set([.8, .4, .9, 1] * 16)
        image['remix_normal_decoded_opengl'] = True
        try:
            with tempfile.TemporaryDirectory() as tmp:
                for mode in ('OCT_EXISTING', 'DIRECTX'):
                    with self.subTest(mode=mode):
                        path = os.path.join(tmp, mode + '.dds')
                        self.assertTrue(processor.export_normal_map(image, path, mode))
                        folder = os.path.join(tmp, mode)
                        os.mkdir(folder)
                        result = subprocess.run([processor.texconv_path, path, '-o', folder, '-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-y', '-nologo'], capture_output=True, timeout=60)
                        self.assertEqual(result.returncode, 0)
                        decoded = bpy.data.images.load(os.path.join(folder, mode + '.png'), check_existing=False)
                        try:
                            decoded.colorspace_settings.name = 'Non-Color'
                            if mode == 'DIRECTX':
                                self.assertAlmostEqual(decoded.pixels[1], .6, delta=.03)
                            else:
                                reference = np.array([[[.8, .4, .9, 1]]], dtype=np.float32)
                                convert_float_to_octahedral_in_place(reference, opengl=True)
                                self.assertAlmostEqual(decoded.pixels[0], reference[0, 0, 0], delta=.03)
                                self.assertAlmostEqual(decoded.pixels[1], reference[0, 0, 1], delta=.03)
                        finally:
                            bpy.data.images.remove(decoded)
        finally:
            bpy.data.images.remove(image)

    def test_capture_and_mod_material_read_usd_encoding(self):
        from rtx_remix_importer.material_utils import process_pbr
        from rtx_remix_importer.mod_apply_utils import process_mod_pbr_util
        stage = Usd.Stage.CreateInMemory()
        shader = UsdShade.Shader.Define(stage, '/Shader')
        shader.CreateInput('encoding', Sdf.ValueTypeNames.Int).Set(2)
        source = bpy.data.images.new('DXInput2', width=4, height=4, float_buffer=True)
        source.colorspace_settings.name = 'Non-Color'
        source.pixels.foreach_set([.6, .2, .9, 1] * 16)
        materials = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, 'input.png')
                with open(path, 'wb') as stream: stream.write(b'placeholder')
                shader.CreateInput('normalmap_texture', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(path))
                for is_mod in (False, True):
                    material = bpy.data.materials.new('EncodingImport')
                    materials.append(material)
                    material.use_nodes = True
                    bsdf = next(node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
                    if is_mod:
                        with patch('rtx_remix_importer.mod_apply_utils.load_mod_texture_util', return_value=source):
                            process_mod_pbr_util(shader, material, bsdf, tmp, os.path.join(tmp, 'mod.usda'), lambda *args: None)
                    else:
                        with patch('rtx_remix_importer.material_utils.load_texture', return_value=source):
                            process_pbr(shader, material, bsdf, os.path.join(tmp, 'capture.usda'))
                    normal = bsdf.inputs['Normal'].links[0].from_node
                    imported = normal.inputs['Color'].links[0].from_node.image
                    self.assertAlmostEqual(imported.pixels[1], .8, places=5)
                    self.assertEqual(imported['remix_normal_original_encoding'], 2)
        finally:
            for material in materials:
                for node in material.node_tree.nodes:
                    if node.type == 'TEX_IMAGE' and node.image and node.image != source:
                        bpy.data.images.remove(node.image, do_unlink=True)
                bpy.data.materials.remove(material, do_unlink=True)
            bpy.data.images.remove(source)

    def test_decoded_normal_survives_blend_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            blend = tmp_path / 'normal.blend'
            writer = tmp_path / 'writer.py'
            repo = str(Path(__file__).resolve().parents[2])
            writer.write_text(
                'import bpy, sys\n'
                f'sys.path.insert(0, {repo!r})\n'
                'from rtx_remix_importer.normal_import import decode_to_opengl\n'
                "image=bpy.data.images.new('PackedSource',width=4,height=4,float_buffer=True)\n"
                "image.colorspace_settings.name='Non-Color'\n"
                'image.pixels.foreach_set([.6,.2,.9,1]*16)\n'
                'decoded=decode_to_opengl(image,2)\n'
                "decoded.use_fake_user=True\n"
                f'bpy.ops.wm.save_as_mainfile(filepath={str(blend)!r})\n', encoding='utf-8')
            reader = tmp_path / 'reader.py'
            reader.write_text(
                'import bpy\n'
                "image=bpy.data.images['PackedSource_decoded']\n"
                "assert image.packed_file is not None\n"
                "assert image['remix_normal_decoded_opengl']\n"
                "assert image['remix_normal_source_name'] == 'PackedSource'\n"
                "assert image['remix_normal_decoded_name'] == image.name\n"
                'assert abs(image.pixels[1] - .8) < .02\n', encoding='utf-8')
            env = dict(os.environ, BLENDER_USER_CONFIG=str(tmp_path / 'config'))
            for args in (['--python', str(writer)], [str(blend), '--python', str(reader)]):
                result = subprocess.run([bpy.app.binary_path, '--background', '--factory-startup', '--python-exit-code', '1', *args], env=env, capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
