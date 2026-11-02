"""Texture queue completion means success, not merely termination."""
import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from rtx_remix_importer.core_utils import BackgroundTextureProcessor, TextureProcessingQueue


class TestTextureQueueStatus(unittest.TestCase):
    def test_wait_results_include_failed_and_unknown_tasks(self):
        async def check():
            queue = TextureProcessingQueue(max_concurrent_tasks=1)
            queue.completed_tasks['good'] = SimpleNamespace(status='completed', result=True)
            queue.completed_tasks['bad'] = SimpleNamespace(status='failed', result=False)
            queue.completed_tasks['cancelled'] = SimpleNamespace(status='cancelled', result=None)
            self.assertTrue(await queue.wait_for_task('good'))
            self.assertFalse(await queue.wait_for_task('bad'))
            self.assertFalse(await queue.wait_for_task('cancelled'))
            self.assertFalse(await queue.wait_for_task('unknown'))
            self.assertTrue(await queue.wait_for_all(['good']))
            self.assertFalse(await queue.wait_for_all(['good', 'bad']))
            self.assertFalse(await queue.wait_for_all(['good', 'unknown']))
            messages = []
            self.assertFalse(await queue.wait_for_all(['bad'], progress_callback=messages.append))
            self.assertNotIn('successfully', messages[-1])
        asyncio.run(check())

    def test_failed_background_job_reports_actual_progress_and_error(self):
        processor = BackgroundTextureProcessor()
        processor.completed_jobs['job'] = {
            'status': 'failed', 'progress': 1, 'total': 3,
            'start_time': time.time(), 'results': [True, False, False],
            'error': '2 conversions failed',
        }
        status = processor.get_job_status('job')
        self.assertEqual(status['progress'], 1)
        self.assertEqual(status['total'], 3)
        self.assertEqual(status['error'], '2 conversions failed')
        self.assertEqual(status['results'], [True, False, False])

    def test_raising_progress_callback_cannot_strand_batch(self):
        async def check():
            fake = SimpleNamespace(convert_texture_batch_async=AsyncMock(return_value=[True, False]))
            with patch('rtx_remix_importer.core_utils.get_texture_processor', return_value=fake):
                queue = TextureProcessingQueue(max_concurrent_tasks=1, batch_size=2)
                ids = await queue.add_batch([
                    (SimpleNamespace(name='good'), 'good.dds', 'base color', None),
                    (SimpleNamespace(name='bad'), 'bad.dds', 'base color', None),
                ])
                def broken_callback(_message):
                    raise RuntimeError('UI closed')
                queue.active_tasks[ids[0]].progress_callback = broken_callback
                try:
                    self.assertFalse(await queue.wait_for_all(ids, timeout=2))
                    self.assertTrue(await queue.wait_for_task(ids[0]))
                    self.assertFalse(await queue.wait_for_task(ids[1]))
                    self.assertFalse(queue.active_tasks)
                finally:
                    await queue.stop()
        asyncio.run(check())

    def test_stop_completes_running_and_pending_task_statuses(self):
        async def check():
            started = asyncio.Event()
            async def conversion(*_args, **_kwargs):
                started.set()
                await asyncio.Event().wait()
            fake = SimpleNamespace(convert_texture_batch_async=conversion)
            with patch('rtx_remix_importer.core_utils.get_texture_processor', return_value=fake):
                queue = TextureProcessingQueue(max_concurrent_tasks=1, batch_size=1)
                ids = await queue.add_batch([
                    (SimpleNamespace(name='running'), 'one.dds', 'base color', None),
                    (SimpleNamespace(name='queued'), 'two.dds', 'base color', None),
                ])
                await asyncio.wait_for(started.wait(), 2)
                await queue.stop()
                self.assertEqual(queue.get_queue_status()['queue_size'], 0)
                self.assertFalse(queue.active_tasks)
                for task_id in ids:
                    self.assertEqual(queue.get_task_status(task_id)['status'], 'cancelled')
                    self.assertFalse(await queue.wait_for_task(task_id))
                self.assertIn('publication blocked', queue.get_task_status(ids[0])['error'])
        asyncio.run(check())
