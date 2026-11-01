"""Material overrides must not mutate other objects sharing a mesh."""
import unittest
from unittest.mock import Mock, patch

import bpy
from pxr import Usd
from rtx_remix_importer.ui.operators.mod_loader import ModFileLoader


class TestInstanceMaterialUpdate(unittest.TestCase):
    def test_material_only_update_uses_object_linked_material(self):
        mesh = bpy.data.meshes.new('SharedAuditMeshOnly')
        base = bpy.data.materials.new('OnlySharedBase')
        current = bpy.data.materials.new('mat_2222222222222222_variant')
        updated = bpy.data.materials.new('OnlyUpdatedOverride')
        other = bpy.data.materials.new('OnlyOtherOverride')
        mesh.materials.append(base)
        first = bpy.data.objects.new('FirstOnlyInstance', mesh)
        second = bpy.data.objects.new('SecondOnlyInstance', mesh)
        first.material_slots[0].link = second.material_slots[0].link = 'OBJECT'
        first.material_slots[0].material = current
        second.material_slots[0].material = other
        loader = ModFileLoader(bpy.context, Mock())
        loader.stage = Usd.Stage.CreateInMemory()
        try:
            with patch('rtx_remix_importer.mod_apply_utils.get_or_create_mod_instance_material_util', return_value=updated) as convert:
                loader._update_material_only('/Mesh', first, {'/RootNode/Looks/mat_2222222222222222'})
            self.assertIs(convert.call_args.kwargs['existing_bl_material_param'], current)
            self.assertEqual(first.material_slots[0].material, updated)
            self.assertEqual(second.material_slots[0].material, other)
            self.assertEqual(mesh.materials[0], base)
        finally:
            bpy.data.objects.remove(first, do_unlink=True)
            bpy.data.objects.remove(second, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            for material in (base, current, updated, other):
                bpy.data.materials.remove(material, do_unlink=True)

    def test_binding_updates_object_slot_only(self):
        mesh = bpy.data.meshes.new('SharedAuditMesh')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        base = bpy.data.materials.new('SharedBase')
        original = bpy.data.materials.new('OriginalOverride')
        other = bpy.data.materials.new('OtherOverride')
        updated = bpy.data.materials.new('UpdatedOverride')
        mesh.materials.append(base)
        first = bpy.data.objects.new('FirstInstance', mesh)
        second = bpy.data.objects.new('SecondInstance', mesh)
        first.material_slots[0].link = second.material_slots[0].link = 'OBJECT'
        first.material_slots[0].material = original
        second.material_slots[0].material = other
        stage = Usd.Stage.CreateInMemory()
        prim = stage.DefinePrim('/Mesh', 'Mesh')
        prim.CreateRelationship('material:binding').SetTargets(['/Material'])
        loader = ModFileLoader(bpy.context, Mock())
        loader.stage = stage
        try:
            with patch('rtx_remix_importer.mod_apply_utils.get_or_create_mod_instance_material_util', return_value=updated) as convert:
                loader._apply_material(prim, first)
            self.assertIs(convert.call_args.kwargs['existing_bl_material_param'], original)
            self.assertEqual(first.material_slots[0].material, updated)
            self.assertEqual(second.material_slots[0].material, other)
            self.assertEqual(mesh.materials[0], base)
        finally:
            bpy.data.objects.remove(first, do_unlink=True)
            bpy.data.objects.remove(second, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            for material in (base, original, other, updated):
                bpy.data.materials.remove(material, do_unlink=True)
