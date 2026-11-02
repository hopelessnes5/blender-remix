"""Export edits under the original captured material hash."""
import tempfile
import unittest
from unittest.mock import patch
import bpy
from pxr import Sdf, Usd
from rtx_remix_importer.operators import export_operator


class TestMaterialOverrideExport(unittest.TestCase):
    def test_capture_layer_is_unchanged_and_stronger_default_is_preserved(self):
        mod = Sdf.Layer.CreateAnonymous('mod.usda')
        capture = Sdf.Layer.CreateAnonymous('capture.usda')
        mod.subLayerPaths.append(capture.identifier)
        stage = Usd.Stage.Open(mod)
        stage.SetEditTarget(capture)
        shader = stage.DefinePrim('/RootNode/Looks/mat_9BF7DE6807F288EC/Shader', 'Shader')
        shader.CreateAttribute('inputs:metallic_constant', Sdf.ValueTypeNames.Float).Set(.7)
        before = capture.ExportToString()
        stage.SetEditTarget(mod)
        mat = bpy.data.materials.new('mat_9BF7DE6807F288EC')
        mat.use_nodes = True
        prop_name = 'remix_custom_texture_dir'
        created_prop = not hasattr(bpy.types.Scene, prop_name)
        if created_prop:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(default='textures')
        try:
            with tempfile.TemporaryDirectory() as root:
                path = export_operator.export_material(mat, stage, root, root + '/mod.usda', override_material_path='/RootNode/Looks/mat_9BF7DE6807F288EC')
                self.assertEqual(str(path), '/RootNode/Looks/mat_9BF7DE6807F288EC')
                self.assertEqual(capture.ExportToString(), before)
                self.assertEqual(shader.GetAttribute('inputs:metallic_constant').Get(), 0.0)
                self.assertTrue(mod.GetAttributeAtPath('/RootNode/Looks/mat_9BF7DE6807F288EC/Shader.inputs:metallic_constant'))
        finally:
            if created_prop:
                del bpy.types.Scene.remix_custom_texture_dir
            bpy.data.materials.remove(mat)

    def test_uses_capture_hash_without_binding_or_mdl_replacement(self):
        stage = Usd.Stage.CreateInMemory()
        mesh = bpy.data.meshes.new('OverrideMesh')
        obj = bpy.data.objects.new('OverrideObject', mesh)
        mat = bpy.data.materials.new('mat_9BF7DE6807F288EC_abcdef12')
        mat.use_nodes = True
        mesh.materials.append(mat)
        obj['usd_prim_path'] = '/RootNode/meshes/mesh_123456789ABCDEF0'
        prop_name = 'remix_custom_texture_dir'
        created_prop = not hasattr(bpy.types.Scene, prop_name)
        if created_prop:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(default='textures')
        class Reporter:
            def report(self, kind, message):
                raise AssertionError(message)
        try:
            with tempfile.TemporaryDirectory() as root, patch.object(export_operator, 'get_game_stage', return_value=None):
                result = export_operator.export_material_replacement(Reporter(), bpy.context, obj, stage, root, root + '/mod.usda')
                self.assertTrue(result)
                prim = stage.GetPrimAtPath('/RootNode/Looks/mat_9BF7DE6807F288EC')
                self.assertTrue(prim)
                self.assertEqual(prim.GetSpecifier(), Sdf.SpecifierOver)
                shader = prim.GetChild('Shader')
                self.assertEqual(shader.GetSpecifier(), Sdf.SpecifierOver)
                self.assertFalse(shader.GetAttribute('info:mdl:sourceAsset'))
                self.assertEqual(shader.GetAttribute('inputs:metallic_constant').Get(), 0.0)
                self.assertAlmostEqual(shader.GetAttribute('inputs:reflection_roughness_constant').Get(), 0.5)
                self.assertFalse(stage.GetPrimAtPath(obj['usd_prim_path']))
                self.assertAlmostEqual(shader.GetAttribute('inputs:diffuse_color_constant').Get()[0], .8)
        finally:
            if created_prop:
                del bpy.types.Scene.remix_custom_texture_dir
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            bpy.data.materials.remove(mat)

    def test_no_hash_does_not_invent_random_identifier(self):
        mat = bpy.data.materials.new('NoHash')
        mesh = bpy.data.meshes.new('NoHashMesh')
        obj = bpy.data.objects.new('NoHashObject', mesh)
        try:
            with patch.object(export_operator, 'get_game_stage', return_value=None):
                self.assertIsNone(export_operator.captured_material_path(obj, mat, bpy.context))
                obj['usd_material_path'] = '/RootNode/Looks/mat_9bf7de6807f288ec'
                self.assertEqual(export_operator.captured_material_path(obj, mat, bpy.context), '/RootNode/Looks/mat_9BF7DE6807F288EC')
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            bpy.data.materials.remove(mat)
