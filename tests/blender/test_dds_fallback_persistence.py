"""The DDS fallback must not leave Blender referring to a deleted PNG."""
import os
import tempfile
import unittest
import bpy
from rtx_remix_importer.core_utils import TextureProcessor
from rtx_remix_importer.texture_loader import _convert_dds_to_png


class TestDDSFallbackPersistence(unittest.TestCase):
    def test_fallback_is_packed_before_temporary_file_is_deleted(self):
        processor = TextureProcessor()
        if not processor.is_available():
            self.skipTest('texconv unavailable')
        source = bpy.data.images.new('PersistenceTest', width=4, height=4, float_buffer=True)
        source.colorspace_settings.name = 'Non-Color'
        source.pixels.foreach_set([.8, .5, .9, 1] * 16)
        converted = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dds = os.path.join(tmp, 'source.dds')
                self.assertTrue(processor.export_normal_map(source, dds, 'OPENGL'))
                converted = _convert_dds_to_png(dds, False, True)
                self.assertIsNotNone(converted.packed_file)
                self.assertEqual(os.path.normcase(converted.filepath), os.path.normcase(dds))
                self.assertGreater(len(converted.packed_file.data), 0)
        finally:
            if converted is not None:
                bpy.data.images.remove(converted)
            bpy.data.images.remove(source)
