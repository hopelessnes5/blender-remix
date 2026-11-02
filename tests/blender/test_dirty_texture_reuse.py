"""Modified images must not silently reuse an existing ordinary DDS."""
import os
import tempfile
import unittest
from types import SimpleNamespace

import bpy
from rtx_remix_importer.core_utils import TextureProcessor
from rtx_remix_importer.operators.export_operator import _preflight_texture_destinations


class TestDirtyTextureReuse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.added = not hasattr(bpy.types.Scene, 'remix_custom_texture_dir')
        if cls.added:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(default='textures')

    @classmethod
    def tearDownClass(cls):
        if cls.added:
            del bpy.types.Scene.remix_custom_texture_dir

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = bpy.data.images.new('dirty_reuse.png', 2, 2)
        self.addCleanup(bpy.data.images.remove, self.image)
        self.source = os.path.join(self.tmp.name, 'source.png')
        self.image.filepath_raw = self.source
        self.image.file_format = 'PNG'
        self.image.save()
        self.assertFalse(self.image.is_dirty)
        self.source_before = open(self.source, 'rb').read()
        self.material = bpy.data.materials.new('reuse')
        self.material.use_nodes = True
        self.addCleanup(bpy.data.materials.remove, self.material, do_unlink=True)
        tree = self.material.node_tree
        texture = tree.nodes.new('ShaderNodeTexImage')
        texture.image = self.image
        shader = next(node for node in tree.nodes if node.type == 'BSDF_PRINCIPLED')
        tree.links.new(texture.outputs['Color'], shader.inputs['Base Color'])
        self.mesh = bpy.data.meshes.new('reuse_mesh')
        self.addCleanup(bpy.data.meshes.remove, self.mesh)
        self.obj = bpy.data.objects.new('reuse_object', self.mesh)
        self.addCleanup(bpy.data.objects.remove, self.obj, do_unlink=True)
        self.mesh.materials.append(self.material)
        self.texture_dir = os.path.join(self.tmp.name, bpy.context.scene.remix_custom_texture_dir or 'rtx-remix/textures')
        os.makedirs(self.texture_dir, exist_ok=True)
        self.dds = os.path.join(self.texture_dir, 'dirty_reuse.dds')
        self.capture = os.path.join(self.tmp.name, 'capture.usda')
        with open(self.capture, 'wb') as f:
            f.write(b'capture bytes')

    def dirty(self):
        self.image.pixels[0] = .75
        self.assertTrue(self.image.is_dirty)

    def preflight(self):
        return _preflight_texture_destinations(bpy.context, [self.obj], self.tmp.name,
                                                os.path.join(self.tmp.name, 'target.usda'), True)

    def test_dirty_existing_is_rejected_before_writes(self):
        with open(self.dds, 'wb') as f:
            f.write(b'original dds')
        self.dirty()
        with self.assertRaisesRegex(ValueError, 'Cannot reuse existing DDS for modified image'):
            self.preflight()
        with open(self.dds, 'rb') as f:
            self.assertEqual(f.read(), b'original dds')
        with open(self.source, 'rb') as f:
            self.assertEqual(f.read(), self.source_before)
        with open(self.capture, 'rb') as f:
            self.assertEqual(f.read(), b'capture bytes')
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, 'target.usda')))

    def test_clean_existing_and_dirty_new_are_allowed(self):
        with open(self.dds, 'wb') as f:
            f.write(b'original dds')
        self.assertTrue(self.preflight())
        os.remove(self.dds)
        self.dirty()
        self.assertTrue(self.preflight())
        self.assertFalse(os.path.exists(self.dds))

    def test_batch_rejects_before_preparing_or_writing_any_item(self):
        with open(self.dds, 'wb') as f:
            f.write(b'original dds')
        self.dirty()
        output = os.path.join(self.texture_dir, 'new.dds')
        messages = []
        processor = TextureProcessor()
        batch = [(self.image, output, 'base color', None), (self.image, self.dds, 'base color', None)]
        results, groups = processor._prepare_conversion_batch(batch, messages.append)
        self.assertEqual(results, [False, False])
        self.assertEqual(groups, {})
        self.assertTrue(any('Cannot reuse existing DDS' in message for message in messages))
        self.assertFalse(os.path.exists(output))
        with open(self.dds, 'rb') as f:
            self.assertEqual(f.read(), b'original dds')

    def test_batch_reuses_clean_existing(self):
        with open(self.dds, 'wb') as f:
            f.write(b'original dds')
        results, groups = TextureProcessor()._prepare_conversion_batch([(self.image, self.dds, 'base color', None)])
        self.assertEqual(results, [True])
        self.assertEqual(groups, {})
