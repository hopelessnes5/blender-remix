"""Material export reports failed texture conversion instead of success."""
import os
import tempfile
import unittest
from unittest.mock import patch

import bpy
from pxr import Usd
from rtx_remix_importer.core_utils import TextureProcessor
from rtx_remix_importer.operators.export_operator import export_material


class TestExportTextureFailures(unittest.TestCase):
    def setUp(self):
        self.had_property = hasattr(bpy.types.Scene, 'remix_custom_texture_dir')
        if not self.had_property:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(default='textures')
        self.material = bpy.data.materials.new('ExportFailure')
        self.material.use_nodes = True
        self.image = bpy.data.images.new('ExportFailureImage', width=4, height=4)
        self.nodes = self.material.node_tree.nodes
        self.links = self.material.node_tree.links
        self.bsdf = next(n for n in self.nodes if n.type == 'BSDF_PRINCIPLED')
        texture = self.nodes.new('ShaderNodeTexImage')
        texture.image = self.image
        self.texture = texture

    def tearDown(self):
        bpy.data.materials.remove(self.material, do_unlink=True)
        bpy.data.images.remove(self.image)
        if not self.had_property:
            del bpy.types.Scene.remix_custom_texture_dir

    def export(self):
        with tempfile.TemporaryDirectory() as tmp:
            export_material(self.material, Usd.Stage.CreateInMemory(), tmp,
                            os.path.join(tmp, 'mod.usda'),
                            override_material_path='/RootNode/Looks/mat_1111111111111111')

    def test_normal_conversion_failure_raises(self):
        normal = self.nodes.new('ShaderNodeNormalMap')
        self.links.new(self.texture.outputs['Color'], normal.inputs['Color'])
        self.links.new(normal.outputs['Normal'], self.bsdf.inputs['Normal'])
        with patch.object(TextureProcessor, 'export_normal_map', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'normal map'):
                self.export()

    def test_queued_texture_failure_raises(self):
        self.links.new(self.texture.outputs['Color'], self.bsdf.inputs['Base Color'])
        with patch.object(TextureProcessor, '_convert_texture_batch_sync', return_value=[False]):
            with self.assertRaisesRegex(RuntimeError, 'textures to DDS'):
                self.export()
