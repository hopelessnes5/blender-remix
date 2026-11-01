"""Capture previews use DDS files directly and never create converted files."""
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rtx_remix_importer import core_utils, thumbnail_dds
from rtx_remix_importer.ui import capture_panel


def write_half_dds(path, rgba):
    header = bytearray(128)
    header[:4] = b'DDS '
    struct.pack_into('<I', header, 4, 124)
    struct.pack_into('<II', header, 12, 1, 1)
    struct.pack_into('<II', header, 76, 32, 4)
    header[84:88] = b'\x71\0\0\0'
    path.write_bytes(header + struct.pack('<4e', *rgba))


class TestCaptureThumbnails(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        capture_panel.register_previews()

    def tearDown(self):
        capture_panel.unregister_previews()
        self.tmp.cleanup()

    def test_toolkit_names_and_precedence(self):
        capture = self.root / 'shot.usda'
        legacy = self.root / 'thumbs'
        hidden = self.root / '.thumbs'
        legacy.mkdir()
        hidden.mkdir()
        short = legacy / 'shot.dds'
        full = hidden / 'shot.usda.dds'
        write_half_dds(short, (1, 0, 0, 1))
        write_half_dds(full, (0, 1, 0, 1))
        self.assertEqual(core_utils.get_thumbnail_preview(str(capture)), str(full))
        full.unlink()
        self.assertEqual(core_utils.get_thumbnail_preview(str(capture)), str(short))
        short.unlink()
        self.assertIsNone(core_utils.get_thumbnail_preview(str(capture)))

    def test_load_refresh_and_remove_without_filesystem_cache(self):
        capture = self.root / 'shot.usd'
        folder = self.root / 'thumbs'
        folder.mkdir()
        image = folder / 'shot.usd.dds'
        write_half_dds(image, (1, 0, 0, 1))
        before = set(self.root.rglob('*'))
        with patch.object(core_utils.constants, 'ADDON_DIR', str(self.root)):
            capture_panel.load_thumbnail(str(capture))
        pcoll = capture_panel.preview_collections['main']
        first = pcoll[str(capture)]
        # Background Blender has no UI icon IDs, but still stores the pixels.
        self.assertEqual(tuple(first.icon_size), (256, 256))
        self.assertAlmostEqual(first.icon_pixels_float[0], 1.0)
        self.assertEqual(tuple(first.image_size), (256, 256))
        self.assertAlmostEqual(first.image_pixels_float[0], 1.0)
        self.assertEqual(set(self.root.rglob('*')), before)
        self.assertFalse((self.root / 'temp_thumbnails').exists())
        write_half_dds(image, (0, 1, 0, 1))
        stat = image.stat()
        os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
        capture_panel.load_thumbnail(str(capture))
        self.assertAlmostEqual(pcoll[str(capture)].icon_pixels_float[1], 1.0)
        self.assertAlmostEqual(pcoll[str(capture)].image_pixels_float[1], 1.0)
        image.unlink()
        capture_panel.load_thumbnail(str(capture))
        self.assertNotIn(str(capture), pcoll)

    def test_bc1_decode(self):
        header = bytearray(128)
        header[:4] = b'DDS '
        struct.pack_into('<I', header, 4, 124)
        struct.pack_into('<II', header, 12, 4, 4)
        struct.pack_into('<II', header, 76, 32, 4)
        header[84:88] = b'DXT1'
        file = self.root / 'block.dds'
        file.write_bytes(header + struct.pack('<HHI', 0xf800, 0x001f, 0))
        pixel = thumbnail_dds.read_dds(file)[0, 0]
        self.assertEqual(tuple(pixel), (1, 0, 0, 1))

    def test_bc2_and_bc3_alpha(self):
        for fourcc, alpha in ((b'DXT3', b'\x77' * 8),
                              (b'DXT5', bytes((119, 0)) + b'\0' * 6)):
            with self.subTest(format=fourcc):
                header = bytearray(128)
                header[:4] = b'DDS '
                struct.pack_into('<I', header, 4, 124)
                struct.pack_into('<II', header, 12, 4, 4)
                struct.pack_into('<II', header, 76, 32, 4)
                header[84:88] = fourcc
                file = self.root / 'alpha.dds'
                file.write_bytes(header + alpha + struct.pack('<HHI', 0xf800, 0x001f, 0))
                pixel = thumbnail_dds.read_dds(file)[0, 0]
                self.assertAlmostEqual(float(pixel[0]), 1.0)
                self.assertAlmostEqual(float(pixel[3]), 119 / 255, places=6)

    def test_bad_thumbnail_does_not_create_preview(self):
        folder = self.root / 'thumbs'
        folder.mkdir()
        (folder / 'bad.dds').write_bytes(b'not a DDS')
        capture = str(self.root / 'bad.usd')
        capture_panel.load_thumbnail(capture)
        self.assertNotIn(capture, capture_panel.preview_collections['main'])


if __name__ == '__main__':
    unittest.main(argv=['test_capture_thumbnails'])
