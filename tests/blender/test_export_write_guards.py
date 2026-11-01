"""Protect source images and capture layers during export."""
import os
import tempfile
import unittest

import bpy
from pxr import Usd
from rtx_remix_importer.core_utils import TextureProcessor
from rtx_remix_importer.operators.export_operator import ExportRemixAsset


class TestExportWriteGuards(unittest.TestCase):
    def test_normal_source_is_never_replaced(self):
        image = bpy.data.images.new('source', width=4, height=4)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, 'source.dds')
                with open(path, 'wb') as stream:
                    stream.write(b'original')
                image.filepath = path
                with self.assertRaisesRegex(ValueError, 'overwrite'):
                    TextureProcessor().export_normal_map(image, path, 'OPENGL')
                with open(path, 'rb') as stream:
                    self.assertEqual(stream.read(), b'original')
        finally:
            bpy.data.images.remove(image)

    def test_capture_is_rejected_as_export_stage(self):
        class Operator:
            def report(self, level, message):
                pass
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'capture.usda')
            stage = Usd.Stage.CreateNew(path)
            stage.GetRootLayer().customLayerData = {'lightspeed_layer_type': 'capture'}
            stage.GetRootLayer().Save()
            with open(path, 'rb') as stream:
                before = stream.read()
            self.assertIsNone(ExportRemixAsset.get_or_create_stage(Operator(), path))
            with open(path, 'rb') as stream:
                self.assertEqual(stream.read(), before)
