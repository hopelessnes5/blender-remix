"""Duplicate cleanup must preserve different image contents."""
import os
import tempfile
import unittest
import bpy
from rtx_remix_importer.texture_loader import cleanup_duplicate_textures


class TestTextureDedupSafety(unittest.TestCase):
    def test_same_path_loaded_before_and_after_file_change_is_not_merged(self):
        generated = bpy.data.images.new('AuditDedupChangingSource', width=4, height=4, float_buffer=True)
        first = second = material = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, 'image.png')
                generated.filepath_raw = path
                generated.file_format = 'PNG'
                generated.pixels.foreach_set([.1, .1, .1, 1] * 16)
                generated.save()
                first = bpy.data.images.load(path, check_existing=False)
                first.name = 'AuditDedupChanged'
                first.pixels[0]  # force the first version into memory
                generated.pixels.foreach_set([.9, .9, .9, 1] * 16)
                generated.save()
                second = bpy.data.images.load(path, check_existing=False)
                second.name = 'AuditDedupChanged.001'
                material = bpy.data.materials.new('AuditDedupChangedMaterial')
                material.use_nodes = True
                node = material.node_tree.nodes.new('ShaderNodeTexImage')
                node.image = second
                cleanup_duplicate_textures()
                self.assertEqual(node.image, second)
        finally:
            if material is not None:
                bpy.data.materials.remove(material, do_unlink=True)
            if second is not None:
                bpy.data.images.remove(second)
            if first is not None:
                bpy.data.images.remove(first)
            bpy.data.images.remove(generated)

    def test_generated_images_with_matching_names_are_not_merged(self):
        first = bpy.data.images.new('AuditDedupGenerated', width=4, height=4)
        second = bpy.data.images.new('AuditDedupGenerated.001', width=4, height=4)
        material = bpy.data.materials.new('AuditDedupMaterial')
        material.use_nodes = True
        node = material.node_tree.nodes.new('ShaderNodeTexImage')
        node.image = second
        try:
            cleanup_duplicate_textures()
            self.assertEqual(node.image, second)
            self.assertIn(second.name, bpy.data.images)
        finally:
            bpy.data.materials.remove(material, do_unlink=True)
            bpy.data.images.remove(second)
            bpy.data.images.remove(first)

    def test_same_file_with_matching_settings_can_be_merged(self):
        generated = bpy.data.images.new('AuditDedupFileSource', width=4, height=4)
        first = second = material = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, 'image.png')
                generated.filepath_raw = path
                generated.file_format = 'PNG'
                generated.save()
                first = bpy.data.images.load(path, check_existing=False)
                second = bpy.data.images.load(path, check_existing=False)
                first.name, second.name = 'AuditDedupFile', 'AuditDedupFile.001'
                material = bpy.data.materials.new('AuditDedupFileMaterial')
                material.use_nodes = True
                node = material.node_tree.nodes.new('ShaderNodeTexImage')
                node.image = second
                cleanup_duplicate_textures()
                self.assertEqual(node.image, first)
                self.assertNotIn('AuditDedupFile.001', bpy.data.images)
                second = None
        finally:
            if material is not None:
                bpy.data.materials.remove(material, do_unlink=True)
            if second is not None:
                bpy.data.images.remove(second)
            if first is not None:
                bpy.data.images.remove(first)
            bpy.data.images.remove(generated)
