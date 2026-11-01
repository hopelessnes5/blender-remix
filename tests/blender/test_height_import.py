"""Import and round-trip Remix displacement through real Blender nodes."""
import os
import tempfile
import unittest
from pathlib import Path

import bpy
from pxr import Sdf, Usd, UsdShade
from rtx_remix_importer import material_utils, mod_apply_utils
from rtx_remix_importer.core_utils import TextureProcessor
from rtx_remix_importer.displacement import apply_imported_displacement, read_displacement


class TestHeightImport(unittest.TestCase):
    def setUp(self):
        self.mat, self.bsdf = material_utils.create_default_blender_material('ImportHeight')
        self.stage = Usd.Stage.CreateInMemory()
        self.shader = UsdShade.Shader.Define(self.stage, '/Shader')

    def tearDown(self):
        bpy.data.materials.remove(self.mat, do_unlink=True)

    def input(self, name, value_type, value):
        self.shader.CreateInput(name, value_type).Set(value)

    def node(self):
        output = next(n for n in self.mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output)
        return output.inputs['Displacement'].links[0].from_node

    def test_import_mapping_and_zero_scale(self):
        self.input('displace_in', Sdf.ValueTypeNames.Float, .03)
        self.input('displace_out', Sdf.ValueTypeNames.Float, .07)
        material_utils.process_pbr(self.shader, self.mat, self.bsdf, '')
        node = self.node()
        self.assertAlmostEqual(node.inputs['Scale'].default_value, .1)
        self.assertAlmostEqual(node.inputs['Midlevel'].default_value, .3)
        self.input('displace_in', Sdf.ValueTypeNames.Float, 0)
        self.input('displace_out', Sdf.ValueTypeNames.Float, 0)
        material_utils.process_pbr(self.shader, self.mat, self.bsdf, '')
        self.assertEqual(self.node(), node)
        self.assertEqual(node.inputs['Scale'].default_value, 0)
        self.assertEqual(node.inputs['Midlevel'].default_value, .5)

    def test_partial_mod_override_keeps_other_component(self):
        apply_imported_displacement(self.mat, .03, .07)
        self.input('displace_in', Sdf.ValueTypeNames.Float, .08)
        mod_apply_utils.process_mod_pbr_util(self.shader, self.mat, self.bsdf, '', '', lambda *args: None)
        node = self.node()
        self.assertAlmostEqual(node.inputs['Scale'].default_value, .15)
        self.assertAlmostEqual(node.inputs['Midlevel'].default_value, .08 / .15)

    def test_missing_component_uses_mdl_default(self):
        self.input('displace_out', Sdf.ValueTypeNames.Float, .15)
        material_utils.process_pbr(self.shader, self.mat, self.bsdf, '')
        self.assertAlmostEqual(self.node().inputs['Scale'].default_value, .2)
        self.assertAlmostEqual(self.node().inputs['Midlevel'].default_value, .25)

    def test_import_bc4_height_and_round_trip(self):
        processor = TextureProcessor()
        if not processor.is_available():
            self.skipTest('texconv unavailable')
        source = bpy.data.images.new('SourceHeight', width=4, height=4, float_buffer=True)
        source.colorspace_settings.name = 'Non-Color'
        source.pixels.foreach_set([.6, .6, .6, 1] * 16)
        loaded = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'height.dds'
                self.assertTrue(processor.export_height_texture(source, str(path)))
                self.input('height_texture', Sdf.ValueTypeNames.Asset, Sdf.AssetPath(str(path)))
                self.input('displace_in', Sdf.ValueTypeNames.Float, .03)
                self.input('displace_out', Sdf.ValueTypeNames.Float, .07)
                material_utils.process_pbr(self.shader, self.mat, self.bsdf, os.path.join(tmp, 'capture.usda'))
                node = self.node()
                tex = node.inputs['Height'].links[0].from_node
                loaded = tex.image
                self.assertEqual(node.inputs['Height'].links[0].from_socket.name, 'Color')
                self.assertIsNotNone(loaded.packed_file)
                self.assertAlmostEqual(loaded.pixels[0], .6, delta=.03)
                self.assertAlmostEqual(loaded.pixels[1], .6, delta=.03)
                self.assertAlmostEqual(loaded.pixels[2], .6, delta=.03)
                _, _, inward, outward = read_displacement(self.mat)
                self.assertAlmostEqual(inward, .03)
                self.assertAlmostEqual(outward, .07)
                library = str(Path(tmp) / 'roundtrip.blend')
                bpy.data.libraries.write(library, {self.mat})
                with bpy.data.libraries.load(library) as (available, requested):
                    requested.materials = [self.mat.name]
                restored = requested.materials[0]
                restored_image = None
                try:
                    restored_image, _, restored_in, restored_out = read_displacement(restored)
                    self.assertAlmostEqual(restored_in, .03)
                    self.assertAlmostEqual(restored_out, .07)
                    self.assertAlmostEqual(restored_image.pixels[0], .6, delta=.03)
                    self.assertAlmostEqual(restored_image.pixels[1], .6, delta=.03)
                finally:
                    bpy.data.materials.remove(restored, do_unlink=True)
                    if restored_image is not None and restored_image is not loaded:
                        bpy.data.images.remove(restored_image, do_unlink=True)
        finally:
            bpy.data.images.remove(source, do_unlink=True)
            if loaded is not None:
                bpy.data.images.remove(loaded, do_unlink=True)

    def test_mod_imports_bc4_height(self):
        processor = TextureProcessor()
        if not processor.is_available():
            self.skipTest('texconv unavailable')
        source = bpy.data.images.new('ModHeight', width=4, height=4, float_buffer=True)
        source.colorspace_settings.name = 'Non-Color'
        source.pixels.foreach_set([.4, .4, .4, 1] * 16)
        loaded = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'height.dds'
                self.assertTrue(processor.export_height_texture(source, str(path)))
                self.input('height_texture', Sdf.ValueTypeNames.Asset, Sdf.AssetPath(str(path)))
                self.input('displace_in', Sdf.ValueTypeNames.Float, .02)
                self.input('displace_out', Sdf.ValueTypeNames.Float, .08)
                mod_apply_utils.process_mod_pbr_util(self.shader, self.mat, self.bsdf, tmp, os.path.join(tmp, 'mod.usda'), lambda *args: None)
                loaded = self.node().inputs['Height'].links[0].from_node.image
                self.assertAlmostEqual(self.node().inputs['Scale'].default_value, .1)
                self.assertAlmostEqual(self.node().inputs['Midlevel'].default_value, .2)
                self.assertAlmostEqual(loaded.pixels[0], .4, delta=.03)
        finally:
            bpy.data.images.remove(source, do_unlink=True)
            if loaded is not None:
                bpy.data.images.remove(loaded, do_unlink=True)

    def test_relative_height_path_resolves_against_authoring_sublayer(self):
        processor = TextureProcessor()
        if not processor.is_available():
            self.skipTest('texconv unavailable')
        source = bpy.data.images.new('LayerHeight', width=4, height=4, float_buffer=True)
        source.colorspace_settings.name = 'Non-Color'
        loaded = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                subdir = Path(tmp) / 'layer'
                subdir.mkdir()
                source.pixels.foreach_set([.7, .7, .7, 1] * 16)
                self.assertTrue(processor.export_height_texture(source, str(subdir / 'height.dds')))
                source.pixels.foreach_set([.2, .2, .2, 1] * 16)
                self.assertTrue(processor.export_height_texture(source, str(Path(tmp) / 'height.dds')))
                sublayer = Sdf.Layer.CreateNew(str(subdir / 'materials.usda'))
                root = Sdf.Layer.CreateNew(str(Path(tmp) / 'root.usda'))
                root.subLayerPaths.append('layer/materials.usda')
                stage = Usd.Stage.Open(root)
                stage.SetEditTarget(sublayer)
                shader = UsdShade.Shader.Define(stage, '/Material/Shader')
                shader.CreateInput('height_texture', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('./height.dds'))
                material_utils.process_pbr(shader, self.mat, self.bsdf, str(Path(tmp) / 'root.usda'))
                loaded = self.node().inputs['Height'].links[0].from_node.image
                self.assertAlmostEqual(loaded.pixels[0], .7, delta=.03)
                self.assertAlmostEqual(self.node().inputs['Scale'].default_value, .05)
                self.assertAlmostEqual(self.node().inputs['Midlevel'].default_value, 1.0)
        finally:
            bpy.data.images.remove(source, do_unlink=True)
            if loaded is not None:
                bpy.data.images.remove(loaded, do_unlink=True)
