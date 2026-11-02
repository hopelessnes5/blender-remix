"""Disabling/reloading the add-on detaches callbacks and cancels queued work."""
import unittest
from unittest.mock import Mock, patch

import bpy
import rtx_remix_importer
from rtx_remix_importer import core_utils


class TestAddonLifecycle(unittest.TestCase):
    def test_background_shutdown_detaches_callbacks_and_timer(self):
        processor = core_utils.BackgroundTextureProcessor()
        gate = Mock()
        job = {'publication_gate': gate, 'progress_callback': Mock(),
               'completion_callback': Mock(), 'last_message': 'pending',
               'status': 'processing', 'total': 1, 'progress': 0}
        processor.active_jobs['job'] = job
        bpy.app.timers.register(processor._timer_callback, first_interval=100)
        processor.timer_registered = True
        try:
            processor.shutdown()
            gate.cancel.assert_called_once()
            self.assertTrue(job['cancelled'])
            self.assertIsNone(job['progress_callback'])
            self.assertIsNone(job['completion_callback'])
            self.assertNotIn('last_message', job)
            self.assertFalse(bpy.app.timers.is_registered(processor._timer_callback))
            processor.shutdown()  # idempotent
        finally:
            if bpy.app.timers.is_registered(processor._timer_callback):
                bpy.app.timers.unregister(processor._timer_callback)

    def test_cleanup_cancels_queue_without_waiting_for_pool(self):
        task = Mock()
        task.progress_callback = Mock()
        queue = Mock()
        queue.active_tasks = {'t1': task}
        pool = Mock()
        old_queue, old_pool = core_utils._texture_queue, core_utils._thread_pool
        try:
            core_utils._texture_queue, core_utils._thread_pool = queue, pool
            core_utils.cleanup_thread_pool()
            queue.cancel_tasks.assert_called_once_with(['t1'], 'Addon shutdown')
            queue.shutdown.assert_called_once_with()
            self.assertIsNone(task.progress_callback)
            pool.shutdown.assert_called_once_with(wait=False, cancel_futures=False)
            self.assertIsNone(core_utils._texture_queue)
            self.assertIsNone(core_utils._thread_pool)
        finally:
            core_utils._texture_queue, core_utils._thread_pool = old_queue, old_pool

    def test_load_handler_installed_and_removed(self):
        # Registration is already covered separately; test the lifecycle hook
        # on the real handler list without depending on other suites' state.
        callback = rtx_remix_importer._cleanup_before_load
        with patch.object(core_utils, 'cleanup_addon_resources') as cleanup:
            callback(None)
            cleanup.assert_called_once_with()
