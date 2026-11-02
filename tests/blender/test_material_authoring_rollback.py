"""Rollback target layer when material authoring fails after prim creation."""
import os
import tempfile
import unittest
from unittest.mock import patch

import bpy
import mathutils
from pxr import Sdf, Usd, UsdGeom
from rtx_remix_importer.operators.export_operator import export_material, write_ingested_mesh_asset, _MaterialTextureTransaction
from rtx_remix_importer.core_utils import TextureProcessor


class TestMaterialAuthoringRollback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.material = bpy.data.materials.new('RollbackMaterial')
        self.material.use_nodes = True
        self.addCleanup(bpy.data.materials.remove, self.material, do_unlink=True)
        self.scene_property_created = not hasattr(bpy.types.Scene, 'remix_custom_texture_dir')
        if self.scene_property_created:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(default='textures')
            self.addCleanup(delattr, bpy.types.Scene, 'remix_custom_texture_dir')

    def test_existing_dirty_edits_are_preserved_after_partial_authoring(self):
        stage_path = os.path.join(self.tmp.name, 'mod.usda')
        stage = Usd.Stage.CreateNew(stage_path)
        stage.GetRootLayer().Save()
        prim = stage.OverridePrim('/RootNode/Looks/mat_0123456789ABCDEF/Shader')
        prim.CreateAttribute('inputs:metallic_constant', Sdf.ValueTypeNames.Float).Set(.75)
        stage.OverridePrim('/Unrelated').CreateAttribute('pending', Sdf.ValueTypeNames.Int).Set(42)
        before = stage.GetRootLayer().ExportToString()
        with open(stage_path, 'rb') as stream:
            before_file = stream.read()
        with patch('rtx_remix_importer.material_defaults.remove_default_inputs', side_effect=RuntimeError('injected authoring error')):
            with self.assertRaisesRegex(RuntimeError, 'injected authoring error'):
                export_material(self.material, stage, self.tmp.name, stage_path,
                                override_material_path='/RootNode/Looks/mat_0123456789ABCDEF')
        self.assertEqual(stage.GetRootLayer().ExportToString(), before)
        self.assertTrue(stage.GetRootLayer().dirty)
        stage.GetRootLayer().Save()
        self.assertEqual(stage.GetPrimAtPath('/Unrelated').GetAttribute('pending').Get(), 42)
        self.assertAlmostEqual(stage.GetPrimAtPath('/RootNode/Looks/mat_0123456789ABCDEF/Shader').GetAttribute('inputs:metallic_constant').Get(), .75)
        with open(stage_path, 'rb') as stream:
            self.assertNotEqual(stream.read(), before_file)  # Only the pre-existing unsaved edits were saved.

    def test_failed_binding_and_new_material_leave_no_specs(self):
        stage_path = os.path.join(self.tmp.name, 'asset.usda')
        stage = Usd.Stage.CreateNew(stage_path)
        stage.DefinePrim('/ReferenceTarget/XForms/Mesh', 'Mesh')
        before = stage.GetRootLayer().ExportToString()
        with patch('rtx_remix_importer.material_defaults.remove_default_inputs', side_effect=RuntimeError('injected authoring error')):
            with self.assertRaisesRegex(RuntimeError, 'injected authoring error'):
                export_material(self.material, stage, self.tmp.name, stage_path,
                                material_scope_path='/ReferenceTarget/Looks',
                                bind_prim_path='/ReferenceTarget/XForms/Mesh')
        self.assertEqual(stage.GetRootLayer().ExportToString(), before)

    def setup_normal_and_scalar(self):
        processor = TextureProcessor()
        if not processor.is_available():
            self.skipTest('texconv unavailable')
        nodes, links = self.material.node_tree.nodes, self.material.node_tree.links
        bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
        normal_image = bpy.data.images.new('normal_existing', width=4, height=4, float_buffer=True)
        self.addCleanup(bpy.data.images.remove, normal_image)
        normal_image.colorspace_settings.name = 'Non-Color'
        normal_image.pixels.foreach_set([.8, .5, .9, 1] * 16)
        normal_tex = nodes.new('ShaderNodeTexImage')
        normal_tex.image = normal_image
        normal_map = nodes.new('ShaderNodeNormalMap')
        links.new(normal_tex.outputs['Color'], normal_map.inputs['Color'])
        links.new(normal_map.outputs['Normal'], bsdf.inputs['Normal'])
        scalar = bpy.data.images.new('scalar_existing', width=4, height=4, float_buffer=True)
        self.addCleanup(bpy.data.images.remove, scalar)
        scalar_tex = nodes.new('ShaderNodeTexImage')
        scalar_tex.image = scalar
        links.new(scalar_tex.outputs['Alpha'], bsdf.inputs['Roughness'])
        texture_dir = os.path.join(self.tmp.name, bpy.context.scene.remix_custom_texture_dir or 'rtx-remix/textures')
        os.makedirs(texture_dir, exist_ok=True)
        normal_path = os.path.join(texture_dir, 'normal_existing.dds')
        with open(normal_path, 'wb') as stream:
            stream.write(b'existing normal file')
        return normal_path, os.path.join(texture_dir, 'scalar_existing.dds')

    def test_scalar_conversion_failure_keeps_existing_normal_and_usd(self):
        normal_path, _ = self.setup_normal_and_scalar()
        stage = Usd.Stage.CreateInMemory()
        before = stage.GetRootLayer().ExportToString()
        with patch.object(TextureProcessor, 'export_height_texture', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'scalar texture'):
                export_material(self.material, stage, self.tmp.name, os.path.join(self.tmp.name, 'mod.usda'),
                                override_material_path='/RootNode/Looks/mat_0123456789ABCDEF')
        with open(normal_path, 'rb') as stream:
            self.assertEqual(stream.read(), b'existing normal file')
        self.assertEqual(stage.GetRootLayer().ExportToString(), before)

    def test_usd_authoring_failure_does_not_publish_normal_or_scalar(self):
        normal_path, scalar_path = self.setup_normal_and_scalar()
        stage = Usd.Stage.CreateInMemory()
        before = stage.GetRootLayer().ExportToString()
        with patch('rtx_remix_importer.material_defaults.remove_default_inputs', side_effect=RuntimeError('injected authoring error')):
            with self.assertRaisesRegex(RuntimeError, 'injected authoring error'):
                export_material(self.material, stage, self.tmp.name, os.path.join(self.tmp.name, 'mod.usda'),
                                override_material_path='/RootNode/Looks/mat_0123456789ABCDEF')
        with open(normal_path, 'rb') as stream:
            self.assertEqual(stream.read(), b'existing normal file')
        self.assertFalse(os.path.exists(scalar_path))
        self.assertEqual(stage.GetRootLayer().ExportToString(), before)

    def test_second_publish_failure_restores_first_texture(self):
        normal_path, scalar_path = self.setup_normal_and_scalar()
        stage = Usd.Stage.CreateInMemory()
        before = stage.GetRootLayer().ExportToString()
        replace = os.replace
        def fail_second(src, dst):
            if os.path.normcase(os.path.abspath(dst)) == os.path.normcase(os.path.abspath(scalar_path)):
                raise OSError('injected publish failure')
            return replace(src, dst)
        with patch('rtx_remix_importer.operators.export_operator.os.replace', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'injected publish failure'):
                export_material(self.material, stage, self.tmp.name, os.path.join(self.tmp.name, 'mod.usda'),
                                override_material_path='/RootNode/Looks/mat_0123456789ABCDEF')
        with open(normal_path, 'rb') as stream:
            self.assertEqual(stream.read(), b'existing normal file')
        self.assertFalse(os.path.exists(scalar_path))
        self.assertEqual(stage.GetRootLayer().ExportToString(), before)

    def test_failed_rollback_keeps_recovery_backup(self):
        processor = TextureProcessor()
        final_path = os.path.join(self.tmp.name, 'existing.dds')
        with open(final_path, 'wb') as stream:
            stream.write(b'original')
        image = bpy.data.images.new('RecoveryImage', width=4, height=4)
        self.addCleanup(bpy.data.images.remove, image)
        transaction = _MaterialTextureTransaction()
        staged = transaction.stage_path(processor, image, final_path)
        with open(staged, 'wb') as stream:
            stream.write(b'new')
        transaction.publish()
        backup = transaction.published[0][1]
        with patch('rtx_remix_importer.operators.export_operator.os.replace', side_effect=OSError('locked')):
            with self.assertRaisesRegex(RuntimeError, 'backups remain'):
                transaction.rollback()
        transaction.close()
        with open(backup, 'rb') as stream:
            self.assertEqual(stream.read(), b'original')

    def test_failed_mesh_material_restores_existing_layer_and_disk(self):
        mesh = bpy.data.meshes.new('RollbackMesh')
        self.addCleanup(bpy.data.meshes.remove, mesh)
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new('RollbackObject', mesh)
        self.addCleanup(bpy.data.objects.remove, obj, do_unlink=True)
        bpy.context.scene.collection.objects.link(obj)
        path = os.path.join(self.tmp.name, 'mesh.usda')
        stage = Usd.Stage.CreateNew(path)
        stage.DefinePrim('/ExistingMesh', 'Mesh')
        stage.GetRootLayer().Save()
        stage.OverridePrim('/Unsaved').CreateAttribute('keep', Sdf.ValueTypeNames.Int).Set(8)
        before = stage.GetRootLayer().ExportToString()
        with open(path, 'rb') as stream:
            disk_before = stream.read()
        messages = []
        with patch('rtx_remix_importer.operators.export_operator.export_material', side_effect=RuntimeError('material failure')):
            result = write_ingested_mesh_asset(stage, obj, bpy.context, 'NewMesh', mathutils.Matrix.Identity(4),
                                               self.material, False, self.tmp.name, path, UsdGeom.Tokens.z,
                                               lambda level, message: messages.append(message))
        self.assertIsNone(result)
        self.assertTrue(any('material failure' in message for message in messages))
        self.assertEqual(stage.GetRootLayer().ExportToString(), before)
        with open(path, 'rb') as stream:
            self.assertEqual(stream.read(), disk_before)

    def test_mesh_save_failure_restores_published_textures_and_layer(self):
        normal_path, scalar_path = self.setup_normal_and_scalar()
        mesh = bpy.data.meshes.new('SaveFailureMesh')
        self.addCleanup(bpy.data.meshes.remove, mesh)
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new('SaveFailureObject', mesh)
        self.addCleanup(bpy.data.objects.remove, obj, do_unlink=True)
        bpy.context.scene.collection.objects.link(obj)
        path = os.path.join(self.tmp.name, 'mesh_save.usda')
        stage = Usd.Stage.CreateNew(path)
        stage.DefinePrim('/ExistingMesh', 'Mesh')
        stage.GetRootLayer().Save()
        before = stage.GetRootLayer().ExportToString()
        with open(path, 'rb') as stream:
            disk_before = stream.read()
        for outcome in ('raises', 'returns_false'):
            with self.subTest(outcome=outcome):
                messages = []
                options = {'side_effect': RuntimeError('save failed')} if outcome == 'raises' else {'return_value': False}
                with patch.object(Sdf.Layer, 'Save', **options):
                    result = write_ingested_mesh_asset(stage, obj, bpy.context, 'NewMesh', mathutils.Matrix.Identity(4),
                                                       self.material, False, self.tmp.name, path, UsdGeom.Tokens.z,
                                                       lambda level, message: messages.append(message))
                self.assertIsNone(result)
                self.assertTrue(messages)
                self.assertEqual(stage.GetRootLayer().ExportToString(), before)
                with open(path, 'rb') as stream:
                    self.assertEqual(stream.read(), disk_before)
                with open(normal_path, 'rb') as stream:
                    self.assertEqual(stream.read(), b'existing normal file')
                self.assertFalse(os.path.exists(scalar_path))

