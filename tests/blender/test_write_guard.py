"""The shared layer-write guard used by every write entry point (audit A01).

A capture layer and a permission-locked layer are never written, and the check
re-runs immediately before each save instead of only when the layer was first
opened.  These tests drive the real guard, the project operator helper and the
export/composition paths that now route through it.
"""
import os
import tempfile
import unittest

from pxr import Sdf, Usd

from rtx_remix_importer import write_guard
from rtx_remix_importer.nvidia_core import composition
from rtx_remix_importer.operators.export_operator import ExportRemixAsset
from rtx_remix_importer.ui.operators.project_ops import _require_writable_layer


class _ReportingOperator:
    def __init__(self):
        self.messages = []

    def report(self, level, message):
        self.messages.append((level, message))


class TestSharedGuard(unittest.TestCase):
    def test_capture_and_locked_layers_are_rejected(self):
        capture = Sdf.Layer.CreateAnonymous("capture.usda")
        capture.customLayerData = {'lightspeed_layer_type': 'capture'}
        with self.assertRaisesRegex(ValueError, 'capture'):
            write_guard.ensure_writable_layer(capture)
        other = Sdf.Layer.CreateAnonymous("locked.usda")
        other.SetPermissionToEdit(False)
        with self.assertRaisesRegex(ValueError, 'locked'):
            write_guard.ensure_writable_layer(other)

    def test_existing_layer_types_pass(self):
        for layer_type in ('replacement', 'workfile', None):
            layer = Sdf.Layer.CreateAnonymous(f"{layer_type or 'blank'}.usda")
            if layer_type:
                layer.customLayerData = {'lightspeed_layer_type': layer_type}
            write_guard.ensure_writable_layer(layer)

    def test_missing_file_and_missing_layer_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'Could not open layer'):
                write_guard.open_writable_layer(os.path.join(tmp, 'missing.usda'))
        with self.assertRaisesRegex(ValueError, 'No layer to author into'):
            write_guard.ensure_writable_layer(None)

    def test_locked_stage_reused_from_memory_is_rejected(self):
        # permissionToEdit is an in-memory lock: opening the file again keeps
        # the lock, so an export that only checked file contents would slip in.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'target.usda')
            stage = Usd.Stage.CreateNew(path)
            stage.GetRootLayer().customLayerData = {'lightspeed_layer_type': 'replacement'}
            stage.GetRootLayer().Save()
            with open(path, 'rb') as stream:
                before = stream.read()
            stage.GetRootLayer().SetPermissionToEdit(False)
            op = _ReportingOperator()
            self.assertIsNone(ExportRemixAsset.get_or_create_stage(op, os.path.abspath(path)))
            self.assertTrue(any('locked' in message for _, message in op.messages))
            with open(path, 'rb') as stream:
                self.assertEqual(stream.read(), before)


class TestCompositionSaveGuard(unittest.TestCase):
    def test_unlocked_capture_refuses_to_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Sdf.Layer.CreateNew(os.path.join(tmp, 'root.usda'))
            capture = Sdf.Layer.CreateNew(os.path.join(tmp, 'capture.usda'))
            capture.customLayerData = {'lightspeed_layer_type': 'capture'}
            capture.Save()
            root.subLayerPaths.append('./capture.usda')
            root.Save()
            with open(capture.realPath, 'rb') as stream:
                before = stream.read()
            project = composition.ProjectComposition.open(os.path.join(tmp, 'root.usda'))
            try:
                self.assertTrue((not project.capture_layer.permissionToEdit))
                # Simulate a race unlocking the capture after open().
                project.capture_layer.SetPermissionToEdit(True)
                with self.assertRaisesRegex(ValueError, 'unlocked'):
                    project.save()
            finally:
                project.close()
            with open(capture.realPath, 'rb') as stream:
                self.assertEqual(stream.read(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
