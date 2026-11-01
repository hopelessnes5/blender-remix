"""Exercise capture and locked-layer checks used by project operators."""
import os
import tempfile
import unittest

from pxr import Sdf
from rtx_remix_importer.ui.operators.project_ops import _require_writable_layer


class TestLayerWriteGuard(unittest.TestCase):
    def test_capture_rejected_and_replacement_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Sdf.Layer.CreateNew(os.path.join(tmp, 'capture.usda'))
            capture.customLayerData = {'lightspeed_layer_type': 'capture'}
            capture.Save()
            replacement = Sdf.Layer.CreateNew(os.path.join(tmp, 'mod.usda'))
            replacement.customLayerData = {'lightspeed_layer_type': 'replacement'}
            replacement.Save()
            with open(capture.realPath, 'rb') as stream:
                before = stream.read()
            with self.assertRaisesRegex(ValueError, 'capture'):
                _require_writable_layer(capture.realPath)
            self.assertEqual(_require_writable_layer(replacement.realPath), replacement)
            with open(capture.realPath, 'rb') as stream:
                self.assertEqual(stream.read(), before)

    def test_locked_layer_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            layer = Sdf.Layer.CreateNew(os.path.join(tmp, 'locked.usda'))
            layer.Save()
            layer.SetPermissionToEdit(False)
            try:
                with self.assertRaisesRegex(ValueError, 'locked'):
                    _require_writable_layer(layer.realPath)
            finally:
                layer.SetPermissionToEdit(True)
