"""Standalone mod cleanup: naming, references, capture safety and backup."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

MODULE = Path(__file__).resolve().parents[2] / 'tools' / 'clean_mod_texture_suffixes.py'
spec = importlib.util.spec_from_file_location('clean_mod_texture_suffixes', MODULE)
cleaner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleaner)


class TestModSuffixCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'textures').mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def layer(self, name, body, layer_type='replacement'):
        path = self.root / name
        path.write_text('#usda 1.0\n(\n customLayerData = { string lightspeed_layer_type = "' + layer_type + '" }\n)\n\ndef "Root" {\n' + body + '\n}\n', encoding='utf-8')
        return path

    def test_analysis_backup_apply_and_idempotence(self):
        texture = self.root / 'textures' / 'Stone_Normal.0123456789abcdef.oct.n.rtex.dds'
        texture.write_bytes(b'dds data')
        layer = self.layer('mod.usda', ' asset inputs:normalmap_texture = @./textures/Stone_Normal.0123456789abcdef.oct.n.rtex.dds@\n string custom:name = "./textures/Stone_Normal.0123456789abcdef.oct.n.rtex.dds"')
        original = layer.read_bytes()
        root, renames, edits = cleaner.build_plan(self.root)
        self.assertEqual(layer.read_bytes(), original)
        self.assertEqual(renames[texture].name, 'Stone_Normal.dds')
        backup = cleaner.apply_plan(root, renames, edits)
        self.assertTrue((texture.parent / 'Stone_Normal.dds').exists())
        self.assertNotIn('.rtex', layer.read_text(encoding='utf-8'))
        with ZipFile(backup) as archive:
            self.assertEqual(archive.read('mod.usda'), original)
            self.assertEqual(archive.read('textures/' + texture.name), b'dds data')
        self.assertEqual(cleaner.build_plan(self.root)[1:], ({}, {}))

    def test_collision_rejected_before_changes(self):
        old = self.root / 'textures' / 'wood.a.rtex.dds'
        old.write_bytes(b'old')
        (old.parent / 'wood.dds').write_bytes(b'existing')
        with self.assertRaisesRegex(ValueError, 'collision'):
            cleaner.build_plan(self.root)
        self.assertEqual(old.read_bytes(), b'old')

    def test_capture_reference_prevents_rename(self):
        texture = self.root / 'textures' / 'wood.a.rtex.dds'
        texture.write_bytes(b'wood')
        layer = self.layer('capture.usda', ' asset inputs:diffuse_texture = @./textures/wood.a.rtex.dds@', 'capture')
        before = layer.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Capture layer'):
            cleaner.build_plan(self.root)
        self.assertEqual(layer.read_bytes(), before)

    def test_unrelated_capture_is_not_edited(self):
        texture = self.root / 'textures' / 'wood.a.rtex.dds'
        texture.write_bytes(b'wood')
        capture = self.layer('capture.usda', ' string note = "wood.a.rtex.dds"', 'capture')
        before = capture.read_bytes()
        root, renames, edits = cleaner.build_plan(self.root)
        cleaner.apply_plan(root, renames, edits)
        self.assertEqual(capture.read_bytes(), before)

    def test_binary_usd_blocks_changes(self):
        (self.root / 'textures' / 'wood.a.rtex.dds').write_bytes(b'wood')
        (self.root / 'asset.usdc').write_bytes(b'PXR-USDC')
        with self.assertRaisesRegex(ValueError, 'Binary'):
            cleaner.build_plan(self.root)

    def test_external_reference_is_unchanged(self):
        (self.root / 'textures' / 'wood.a.rtex.dds').write_bytes(b'wood')
        layer = self.layer('mod.usda', ' asset external = @../other/wood.a.rtex.dds@')
        _, _, edits = cleaner.build_plan(self.root)
        self.assertNotIn(layer, edits)
