"""Progress callbacks for asynchronous conversions run on the caller's loop."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import tempfile
import threading
import unittest

import bpy
from rtx_remix_importer.core_utils import TextureProcessor
from rtx_remix_importer.ui.operators.mod_loader import ModFileLoader


class TestAsyncProgressThreads(unittest.TestCase):
    def test_real_conversions_deliver_progress_on_main_thread(self):
        processor = TextureProcessor()
        if not processor.is_available():
            self.skipTest('texconv unavailable')
        image = bpy.data.images.new('ThreadProgressTest', width=4, height=4)
        seen = []

        def progress(message):
            # Actual callers may use bpy when displaying progress.
            seen.append((threading.current_thread() is threading.main_thread(), bpy.context.scene.name, message))

        async def run(tmp):
            dds = os.path.join(tmp, 'single.dds')
            self.assertTrue(await processor.convert_png_to_dds_async(image, dds, progress_callback=progress))
            png = os.path.join(tmp, 'decoded.png')
            self.assertTrue(await processor.convert_dds_to_png_async(dds, png, progress))
            second = os.path.join(tmp, 'batch.dds')
            self.assertEqual(await processor.convert_texture_batch_async([(image, second, 'base color', None)], progress), [True])
            # Let the loop process all queued progress messages.
            await asyncio.sleep(0)

        try:
            with tempfile.TemporaryDirectory() as tmp:
                asyncio.run(run(tmp))
            self.assertTrue(seen)
            self.assertTrue(all(main for main, _, _ in seen), seen)
            self.assertTrue(any('texconv' in message.lower() or 'converted:' in message.lower() for _, _, message in seen))
        finally:
            bpy.data.images.remove(image)

    def test_worker_cannot_start_image_preparation_or_preload(self):
        processor = TextureProcessor()
        loader = ModFileLoader(bpy.context, None)

        def prepare():
            return processor._prepare_conversion_batch([])

        def preload():
            return loader._preload_textures_parallel([])

        def normal():
            return processor.export_normal_map(None, 'unused.dds', 'OPENGL')

        def height():
            return processor.export_height_texture(None, 'unused.dds')

        with ThreadPoolExecutor(max_workers=1) as executor:
            for function in (prepare, preload, normal, height):
                with self.assertRaisesRegex(RuntimeError, 'main thread'):
                    executor.submit(function).result()
