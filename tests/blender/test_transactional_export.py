"""Audit A03-A05: conversions run first, on the main thread, and clearly.

A USD material may only be published after every referenced DDS exists, a
failed conversion must fail the whole operation instead of producing a
half-authored material (A04/A05), and Blender image saving must stay on
Blender's main thread, never in a worker (A03).
"""

import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import bpy
from pxr import Usd, UsdShade

from rtx_remix_importer import core_utils, material_utils
from rtx_remix_importer.core_utils import TextureProcessor, get_background_processor
from rtx_remix_importer.export_utils import MaterialExporter, ProgressTracker, TextureExporter
from rtx_remix_importer.operators.export_operator import export_material


class _ShimOperator:
    def __init__(self):
        self.messages = []

    def report(self, level, message):
        self.messages.append((level, message))


class TestTransactionalExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not hasattr(bpy.types.Scene, 'remix_custom_texture_dir'):
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(
                default='rtx-remix/textures')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)

    def make_material_with_image(self, name):
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        tree = material.node_tree
        principled = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
        image = bpy.data.images.new(name + '_img', 4, 4)
        self.addCleanup(bpy.data.images.remove, image)
        tex = tree.nodes.new('ShaderNodeTexImage')
        tex.image = image
        tree.links.new(tex.outputs['Color'], principled.inputs['Base Color'])
        return material, image

    def save_png(self, name):
        image = bpy.data.images.new(name, 4, 4)
        self.addCleanup(bpy.data.images.remove, image)
        path = os.path.join(self.root, f'{name}.png')
        image.filepath = path
        image.file_format = 'PNG'
        image.save()
        return path

    def wait_for_job(self, job_id, timeout=60):
        processor = get_background_processor()
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = processor.get_job_status(job_id)
            if info and info['status'] in ('completed', 'failed', 'cancelled'):
                return info
            time.sleep(0.05)
        return None

    def test_failed_batch_conversion_leaves_stage_untouched(self):
        stage_path = os.path.join(self.root, 'mod.usda')
        stage = Usd.Stage.CreateNew(stage_path)
        stage.Save()
        before = open(stage_path, 'rb').read()

        material, _ = self.make_material_with_image('TxZeroUsd')
        self.addCleanup(bpy.data.materials.remove, material, do_unlink=True)

        processor = TextureProcessor()
        processor.texconv_path = None
        with mock.patch('rtx_remix_importer.core_utils.get_texture_processor',
                        return_value=processor):
            with self.assertRaisesRegex(RuntimeError, 'Could not export textures to DDS'):
                export_material(material, stage, self.root, stage_path)

        # Nothing may have been authored before the failed conversion.
        self.assertEqual(open(stage_path, 'rb').read(), before)
        # And no partially converted output may sit at its destination.
        textures_dir = os.path.join(self.root, 'rtx-remix', 'textures')
        if os.path.isdir(textures_dir):
            self.assertEqual([f for f in os.listdir(textures_dir) if f.endswith('.dds')], [])

    def test_successful_export_publishes_only_verified_files(self):
        if not TextureProcessor().is_available():
            self.skipTest('texconv.exe not available')

        stage_path = os.path.join(self.root, 'mod_success.usda')
        stage = Usd.Stage.CreateNew(stage_path)
        stage.Save()
        before = open(stage_path, 'rb').read()

        material, image = self.make_material_with_image('TxSuccess')
        self.addCleanup(bpy.data.materials.remove, material, do_unlink=True)

        mat_path = export_material(material, stage, self.root, stage_path)
        self.assertTrue(mat_path)
        stage.GetRootLayer().Save()
        self.assertNotEqual(open(stage_path, 'rb').read(), before)

        shader_path = str(mat_path) + '/Shader'
        attribute = stage.GetPrimAtPath(shader_path).GetAttribute('inputs:diffuse_texture')
        self.assertTrue(attribute.Get())
        self.assertTrue(os.path.exists(os.path.join(
            self.root, 'rtx-remix', 'textures', TextureProcessor().texture_filename(image))))

    def test_legacy_texture_exporter_raises_on_none(self):
        material, node = material_utils.create_default_blender_material('TxLegacyNone')
        self.addCleanup(bpy.data.materials.remove, material, do_unlink=True)
        image = bpy.data.images.new('legacy_none_img', 4, 4)
        self.addCleanup(bpy.data.images.remove, image)
        tex = material.node_tree.nodes.new('ShaderNodeTexImage')
        tex.image = image
        material.node_tree.links.new(tex.outputs['Color'], node.inputs['Base Color'])

        stage = Usd.Stage.CreateInMemory()
        shader = UsdShade.Shader.Define(stage, '/Material/Shader')

        exporter = MaterialExporter('', '')

        async def return_none(*args, **kwargs):
            return None

        with mock.patch.object(TextureExporter, 'export_texture_async', return_none):
            with self.assertRaisesRegex(RuntimeError, 'to DDS'):
                asyncio.run(exporter._export_material_textures(
                    node, shader.GetPrim(), ProgressTracker(6)))

    def test_background_prepares_images_on_main_thread(self):
        if not TextureProcessor().is_available():
            self.skipTest('texconv.exe not available')

        saved_threads = []
        original_save = TextureProcessor._save_blender_image_to_file

        def sentinel(processor, bl_image, save_path):
            saved_threads.append(threading.current_thread() is threading.main_thread())
            return original_save(processor, bl_image, save_path)

        image = bpy.data.images.new('background_bg_img', 4, 4)
        self.addCleanup(bpy.data.images.remove, image)
        output = os.path.join(self.root, 'background_bg.dds')

        with mock.patch.object(TextureProcessor, '_save_blender_image_to_file', sentinel):
            job_id = get_background_processor().start_background_job(
                [(image, output, 'base color', 'BC7_UNORM_SRGB')])
            status = self.wait_for_job(job_id)

        self.assertIsNotNone(status, 'background job did not finish in time')
        self.assertEqual(status['status'], 'completed')
        # Every Blender image save ran on the main thread (audit A03).
        self.assertTrue(saved_threads and all(saved_threads))
        self.assertTrue(os.path.exists(output))

    def test_preload_keeps_image_loading_on_main_thread(self):
        png = self.save_png('preload_src')
        operator = _ShimOperator()
        loader = _make_loader(operator)
        loader._preload_textures_parallel([png])
        self.assertIn('preload_src', bpy.data.images)
        self.assertTrue(any('Pre-loaded 1/1' in message for _, message in operator.messages))


def _make_loader(operator):
    from rtx_remix_importer.ui.operators.mod_loader import ModFileLoader
    return ModFileLoader(None, operator)


if __name__ == '__main__':
    unittest.main()
