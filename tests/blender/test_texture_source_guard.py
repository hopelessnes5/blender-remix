"""Texture export must refuse targets that alias their own source (audit A02).

A hard link or a symlink can name the export target differently while pointing
at the very bytes the source image was loaded from, so the guard compares
resolved paths and falls back to ``os.path.samefile`` when both files exist.
The guard runs before texconv availability, which keeps rejection testable in
any environment.
"""
import os
import tempfile
import unittest

import bpy

from rtx_remix_importer.core_utils import TextureProcessor


def _write(path, payload=b'original'):
    with open(path, 'wb') as stream:
        stream.write(payload)
    return path


class TestTextureSourceGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)

    def image(self, name, filepath):
        image = bpy.data.images.new(name, width=4, height=4)
        self.addCleanup(bpy.data.images.remove, image)
        image.filepath = filepath
        return image

    def test_flag_matches_sources_and_leaves_others(self):
        source = _write(os.path.join(self.root, 'source.png'))
        normal_source = _write(os.path.join(self.root, 'decoded.png'))
        image = self.image('guarded', source)
        image['remix_normal_source_path'] = normal_source
        processor = TextureProcessor()
        self.assertTrue(processor.is_source_target(image, source))
        self.assertTrue(processor.is_source_target(image, normal_source))
        self.assertFalse(processor.is_source_target(image, _write(os.path.join(self.root, 'other.png'), b'other')))
        self.assertFalse(processor.is_source_target(image, os.path.join(self.root, 'missing.png')))

    def test_height_rejects_hardlink_alias(self):
        source = _write(os.path.join(self.root, 'source.png'))
        alias = os.path.join(self.root, 'alias.png')
        try:
            os.link(source, alias)
        except OSError as error:
            self.skipTest(f'hard links unavailable: {error}')
        processor = TextureProcessor()
        image = self.image('height', alias)
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            processor.export_height_texture(image, source)
        with open(source, 'rb') as stream:
            self.assertEqual(stream.read(), b'original')

    def test_normal_rejects_hardlink_alias(self):
        source = _write(os.path.join(self.root, 'source.dds'))
        alias = os.path.join(self.root, 'alias.dds')
        try:
            os.link(source, alias)
        except OSError as error:
            self.skipTest(f'hard links unavailable: {error}')
        processor = TextureProcessor()
        image = self.image('normal', alias)
        image['remix_normal_source_path'] = source
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            processor.export_normal_map(image, source, 'OPENGL')
        with open(source, 'rb') as stream:
            self.assertEqual(stream.read(), b'original')

    def test_distinct_target_is_not_flagged(self):
        source = _write(os.path.join(self.root, 'source.png'))
        target = os.path.join(self.root, 'height_out.dds')
        processor = TextureProcessor()
        image = self.image('height', source)
        try:
            processor.export_height_texture(image, target)
        except ValueError as error:
            self.fail(f'a different file was treated as the source: {error}')
        with open(source, 'rb') as stream:
            self.assertEqual(stream.read(), b'original')

    def test_symlink_alias_is_detected(self):
        source = _write(os.path.join(self.root, 'source.png'))
        link = os.path.join(self.root, 'link.png')
        try:
            os.symlink(source, link)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f'symlinks unavailable: {error}')
        processor = TextureProcessor()
        image = self.image('alias', link)
        self.assertTrue(processor.is_source_target(image, source))


if __name__ == '__main__':
    unittest.main(verbosity=2)
