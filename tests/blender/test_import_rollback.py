"""Failed direct imports must not leak Blender data into the legacy fallback."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import bpy
from rtx_remix_importer import import_core, material_utils
from rtx_remix_importer.blender_bridge import scene_builder

CAPTURE = Path(__file__).resolve().parents[1] / 'data' / 'full_project' / 'capture.usda'


class TestDirectImportRollback(unittest.TestCase):
    def setUp(self):
        self.context = bpy.context
        self.scene = self.context.scene
        self.old_active_collection = self.context.view_layer.active_layer_collection
        self.collection = bpy.data.collections.new('RollbackExistingCollection')
        self.scene.collection.children.link(self.collection)
        self.mesh = bpy.data.meshes.new('RollbackExistingMesh')
        self.obj = bpy.data.objects.new('RollbackExistingObject', self.mesh)
        self.collection.objects.link(self.obj)
        self.material = bpy.data.materials.new('RollbackExistingMaterial')
        self.mesh.materials.append(self.material)
        self.scene['source_capture_path'] = 'previous capture'
        self.cache_before = dict(material_utils._global_material_cache)
        material_utils._global_material_cache['rollback_test_existing'] = self.material

    def tearDown(self):
        material_utils._global_material_cache.clear()
        material_utils._global_material_cache.update(self.cache_before)
        self.context.view_layer.active_layer_collection = self.old_active_collection
        bpy.data.objects.remove(self.obj, do_unlink=True)
        bpy.data.meshes.remove(self.mesh)
        bpy.data.materials.remove(self.material)
        bpy.data.collections.remove(self.collection)
        if 'source_capture_path' in self.scene:
            del self.scene['source_capture_path']

    def test_failure_after_objects_rolls_back_before_legacy(self):
        before = {name: {x.as_pointer() for x in getattr(bpy.data, name)} for name in (
            'objects', 'collections', 'meshes', 'materials', 'images', 'lights', 'cameras', 'node_groups')}

        def fail_after_objects(*args, **kwargs):
            self.assertGreater(len(bpy.data.objects), len(before['objects']))
            group = bpy.data.node_groups.new('RollbackCreatedGroup', 'ShaderNodeTree')
            group.nodes.new('ShaderNodeTexImage').image = bpy.data.images.new('RollbackCreatedImage', 4, 4)
            raise RuntimeError('injected late direct import failure')

        def legacy(*args):
            after = {name: {x.as_pointer() for x in getattr(bpy.data, name)} for name in before}
            self.assertEqual(after, before)
            self.assertEqual(self.scene['source_capture_path'], 'previous capture')
            self.assertEqual(self.mesh.materials[0], self.material)
            self.assertEqual(material_utils._global_material_cache['rollback_test_existing'], self.material)
            self.assertEqual(self.context.view_layer.active_layer_collection, self.old_active_collection)
            return 'legacy-result'

        with patch.dict(os.environ, {import_core.BACKEND_ENV_VAR: 'direct'}), \
             patch.object(scene_builder, 'import_lights_from_usd', side_effect=fail_after_objects), \
             patch.object(import_core, '_legacy_import', side_effect=legacy) as legacy_call:
            self.assertEqual(import_core.import_rtx_remix_usd_with_materials(self.context, str(CAPTURE), True, True, .01), 'legacy-result')
            legacy_call.assert_called_once()

    def test_direct_only_rolls_back_without_fallback(self):
        before = {obj.as_pointer() for obj in bpy.data.objects}
        with patch.dict(os.environ, {import_core.BACKEND_ENV_VAR: 'direct-only'}), \
             patch.object(scene_builder, 'import_lights_from_usd', side_effect=RuntimeError('late failure')), \
             patch.object(import_core, '_legacy_import') as legacy_call:
            with self.assertRaises(import_core.USDImportError):
                import_core.import_rtx_remix_usd_with_materials(self.context, str(CAPTURE), False, True, .01)
            legacy_call.assert_not_called()
        self.assertEqual({obj.as_pointer() for obj in bpy.data.objects}, before)
