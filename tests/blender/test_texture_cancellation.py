"""Running texconv must never publish DDS after cancellation or timeout."""
import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy
from rtx_remix_importer.core_utils import (
    BackgroundTextureProcessor, TextureProcessingQueue, TextureProcessor,
    cancel_all_publications, _PublicationGate, _progress_on_loop,
)


class TestTextureCancellation(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.image = bpy.data.images.new('CancellationSource', width=4, height=4)
        self.started, self.release, self.finished = threading.Event(), threading.Event(), threading.Event()
        self.processor = TextureProcessor(texconv_path='test-texconv')

    def tearDown(self):
        self.release.set()
        bpy.data.images.remove(self.image)
        self.directory.cleanup()

    def texconv(self, cmd, **_kwargs):
        self.started.set()
        if not self.release.wait(10):
            raise RuntimeError('test conversion timed out')
        output_dir = Path(cmd[cmd.index('-o') + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        for input_path in cmd[1:cmd.index('-o')]:
            (output_dir / (Path(input_path).stem + '.dds')).write_bytes(b'test DDS')
        self.finished.set()
        return SimpleNamespace(returncode=0, stderr='')

    async def wait_started(self):
        for _ in range(200):
            if self.started.is_set():
                return
            await asyncio.sleep(.01)
        self.fail('texconv worker did not start')

    async def wait_cleanup(self):
        for _ in range(300):
            if self.finished.is_set() and not list(Path(self.directory.name).glob('*.dds')):
                return
            await asyncio.sleep(.01)
        self.fail('temporary DDS was not cleaned up')

    def test_queue_timeout_blocks_running_and_pending_publications(self):
        async def check():
            queue = TextureProcessingQueue(max_concurrent_tasks=1, batch_size=1)
            paths = [os.path.join(self.directory.name, f'{i}.dds') for i in range(2)]
            with patch('rtx_remix_importer.core_utils.get_texture_processor', return_value=self.processor), \
                 patch('rtx_remix_importer.core_utils.get_texture_queue', return_value=queue), \
                 patch('rtx_remix_importer.core_utils.subprocess.run', side_effect=self.texconv):
                operation = asyncio.create_task(self.processor.process_textures_parallel(
                    [(self.image, path, 'base color', None) for path in paths], timeout=.1))
                try:
                    await self.wait_started()
                    results = await asyncio.wait_for(operation, 3)
                    self.assertEqual(list(results.values()), [False, False])
                    self.release.set()
                    await asyncio.wait_for(queue.queue.join(), 5)
                    await self.wait_cleanup()
                    self.assertTrue(all(not os.path.exists(path) for path in paths))
                finally:
                    self.release.set()
                    await queue.stop()
        asyncio.run(check())

    def test_direct_async_cancellation_blocks_publication(self):
        async def check():
            path = os.path.join(self.directory.name, 'direct.dds')
            with patch('rtx_remix_importer.core_utils.subprocess.run', side_effect=self.texconv):
                operation = asyncio.create_task(self.processor.convert_png_to_dds_async(self.image, path))
                await self.wait_started()
                operation.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await operation
                self.release.set()
                await self.wait_cleanup()
                self.assertFalse(os.path.exists(path))
        asyncio.run(check())

    def test_single_task_timeout_blocks_publication(self):
        async def check():
            queue = TextureProcessingQueue(max_concurrent_tasks=1, batch_size=1)
            path = os.path.join(self.directory.name, 'single.dds')
            with patch('rtx_remix_importer.core_utils.get_texture_processor', return_value=self.processor), \
                 patch('rtx_remix_importer.core_utils.subprocess.run', side_effect=self.texconv):
                ids = await queue.add_batch([(self.image, path, 'base color', None)])
                try:
                    await self.wait_started()
                    self.assertFalse(await queue.wait_for_task(ids[0], timeout=.01))
                    self.release.set()
                    await asyncio.wait_for(queue.queue.join(), 5)
                    await self.wait_cleanup()
                    self.assertFalse(os.path.exists(path))
                finally:
                    self.release.set()
                    await queue.stop()
        asyncio.run(check())

    def test_cancelled_waiter_blocks_publication(self):
        async def check():
            queue = TextureProcessingQueue(max_concurrent_tasks=1, batch_size=1)
            path = os.path.join(self.directory.name, 'waiter.dds')
            with patch('rtx_remix_importer.core_utils.get_texture_processor', return_value=self.processor), \
                 patch('rtx_remix_importer.core_utils.subprocess.run', side_effect=self.texconv):
                ids = await queue.add_batch([(self.image, path, 'base color', None)])
                try:
                    await self.wait_started()
                    waiter = asyncio.create_task(queue.wait_for_all(ids))
                    await asyncio.sleep(.01)
                    waiter.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await waiter
                    self.release.set()
                    await asyncio.wait_for(queue.queue.join(), 5)
                    await self.wait_cleanup()
                    self.assertFalse(os.path.exists(path))
                finally:
                    self.release.set()
                    await queue.stop()
        asyncio.run(check())

    def test_cancelled_single_waiter_blocks_publication(self):
        async def check():
            queue = TextureProcessingQueue(max_concurrent_tasks=1, batch_size=1)
            path = os.path.join(self.directory.name, 'single-waiter.dds')
            with patch('rtx_remix_importer.core_utils.get_texture_processor', return_value=self.processor), \
                 patch('rtx_remix_importer.core_utils.subprocess.run', side_effect=self.texconv):
                ids = await queue.add_batch([(self.image, path, 'base color', None)])
                try:
                    await self.wait_started()
                    waiter = asyncio.create_task(queue.wait_for_task(ids[0]))
                    await asyncio.sleep(.01)
                    waiter.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await waiter
                    self.release.set()
                    await asyncio.wait_for(queue.queue.join(), 5)
                    await self.wait_cleanup()
                    self.assertFalse(os.path.exists(path))
                finally:
                    self.release.set()
                    await queue.stop()
        asyncio.run(check())

    def test_queued_worker_after_cancellation_cleans_prepared_png(self):
        from concurrent.futures import ThreadPoolExecutor
        async def check():
            executor = ThreadPoolExecutor(max_workers=1)
            blocker_started = threading.Event()
            blocker_release = threading.Event()
            def block():
                blocker_started.set()
                blocker_release.wait(10)
            blocker = executor.submit(block)
            self.assertTrue(blocker_started.wait(2))
            png = Path(self.directory.name) / 'queued.png'
            png.write_bytes(b'png')
            output = os.path.join(self.directory.name, 'queued.dds')
            try:
                with patch('rtx_remix_importer.core_utils.get_thread_pool', return_value=executor), \
                     patch.object(self.processor, '_prepare_png_source', return_value=str(png)), \
                     patch('rtx_remix_importer.core_utils.subprocess.run') as run:
                    operation = asyncio.create_task(self.processor.convert_png_to_dds_async(self.image, output))
                    await asyncio.sleep(.05)
                    operation.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await operation
                    blocker_release.set()
                    for _ in range(300):
                        if not png.exists():
                            break
                        await asyncio.sleep(.01)
                    self.assertFalse(png.exists())
                    self.assertFalse(os.path.exists(output))
                    run.assert_not_called()
            finally:
                blocker_release.set()
                executor.shutdown(wait=True)
        asyncio.run(check())

    def test_shutdown_gate_blocks_direct_async_publication(self):
        async def check():
            path = os.path.join(self.directory.name, 'shutdown.dds')
            with patch('rtx_remix_importer.core_utils.subprocess.run', side_effect=self.texconv):
                operation = asyncio.create_task(self.processor.convert_png_to_dds_async(self.image, path))
                await self.wait_started()
                cancel_all_publications()
                self.release.set()
                self.assertFalse(await asyncio.wait_for(operation, 5))
                self.assertFalse(os.path.exists(path))
                self.assertFalse(list(Path(self.directory.name).glob('*.dds')))
        asyncio.run(check())

    def test_background_cancel_blocks_last_group_publication(self):
        path = os.path.join(self.directory.name, 'background.dds')
        background = BackgroundTextureProcessor()
        with patch('rtx_remix_importer.core_utils.get_texture_processor', return_value=self.processor), \
             patch('rtx_remix_importer.core_utils.subprocess.run', side_effect=self.texconv):
            job = background.start_background_job([(self.image, path, 'base color', None)])
            try:
                self.assertTrue(self.started.wait(5))
                self.assertTrue(background.cancel_job(job))
                self.release.set()
                background.active_jobs[job]['thread'].join(5)
                self.assertFalse(background.active_jobs[job]['thread'].is_alive())
                self.assertEqual(background.active_jobs[job]['status'], 'cancelled')
                self.assertFalse(os.path.exists(path))
                self.assertFalse(list(Path(self.directory.name).glob('*.dds')))
            finally:
                self.release.set()
                if bpy.app.timers.is_registered(background._timer_callback):
                    bpy.app.timers.unregister(background._timer_callback)
                background.timer_registered = False

    def test_cancelled_dds_to_png_never_publishes(self):
        async def check():
            path = os.path.join(self.directory.name, 'output.png')
            def conversion(_source, staged, _callback):
                self.started.set()
                if not self.release.wait(10):
                    raise RuntimeError('test conversion timed out')
                Path(staged).write_bytes(b'test PNG')
                self.finished.set()
                return True
            with patch.object(self.processor, 'convert_dds_to_png_sync', side_effect=conversion):
                operation = asyncio.create_task(self.processor.convert_dds_to_png_async('input.dds', path))
                await self.wait_started()
                operation.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await operation
                self.release.set()
                for _ in range(300):
                    if self.finished.is_set() and not list(Path(self.directory.name).glob('remix_dds_*')):
                        break
                    await asyncio.sleep(.01)
                self.assertTrue(self.finished.is_set())
                self.assertFalse(os.path.exists(path))
                self.assertFalse(list(Path(self.directory.name).glob('remix_dds_*')))
        asyncio.run(check())

    def test_cancelled_gate_drops_already_scheduled_callback(self):
        async def check():
            messages = []
            gate = _PublicationGate()
            forward = _progress_on_loop(asyncio.get_running_loop(), messages.append, [gate])
            forward('stale')
            gate.cancel()
            await asyncio.sleep(.01)
            self.assertEqual(messages, [])
        asyncio.run(check())

    def test_previously_cancelled_batch_skips_texconv_and_cleans_inputs(self):
        png = Path(self.directory.name) / 'input.png'
        png.write_bytes(b'png')
        target = os.path.join(self.directory.name, 'output.dds')
        gate = _PublicationGate()
        gate.cancel()
        with patch('rtx_remix_importer.core_utils.subprocess.run') as run:
            result = self.processor._run_texconv_batch(
                [False], {(self.directory.name, 'BC7_UNORM'): [(0, str(png), target)]},
                publication_gates=[gate])
        run.assert_not_called()
        self.assertEqual(result, [False])
        self.assertFalse(png.exists())
        self.assertFalse(os.path.exists(target))

    def test_previously_cancelled_single_conversion_cleans_input(self):
        png = Path(self.directory.name) / 'input.png'
        png.write_bytes(b'png')
        target = os.path.join(self.directory.name, 'output.dds')
        gate = _PublicationGate()
        gate.cancel()
        with patch('rtx_remix_importer.core_utils.subprocess.run') as run:
            result = self.processor._run_png_to_dds(str(png), target, 'BC7_UNORM', publication_gate=gate)
        run.assert_not_called()
        self.assertFalse(result)
        self.assertFalse(png.exists())
