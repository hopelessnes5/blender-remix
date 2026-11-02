"""Mesh export must not silently discard per-face materials."""
import os
import tempfile
import unittest
from unittest.mock import patch

import bpy
import mathutils
from pxr import Usd, UsdGeom
from rtx_remix_importer.operators.export_operator import (
    _used_mesh_materials, write_ingested_mesh_asset,
)


class Operator:
    def __init__(self):
        self.messages = []
    def report(self, level, message):
        self.messages.append(message)


class TestMeshMaterialGuard(unittest.TestCase):
    def setUp(self):
        self.mesh = bpy.data.meshes.new('guard_mesh')
        self.mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0),(1,1,0)], [], [(0,1,2),(1,3,2)])
        self.obj = bpy.data.objects.new('guard_object', self.mesh)
        bpy.context.scene.collection.objects.link(self.obj)
        self.addCleanup(bpy.data.meshes.remove, self.mesh)
        self.addCleanup(bpy.data.objects.remove, self.obj, do_unlink=True)
        self.a = bpy.data.materials.new('guard_a')
        self.b = bpy.data.materials.new('guard_b')
        self.addCleanup(bpy.data.materials.remove, self.a, do_unlink=True)
        self.addCleanup(bpy.data.materials.remove, self.b, do_unlink=True)
        self.mesh.materials.append(self.a)
        self.mesh.materials.append(self.b)

    def check(self):
        bpy.context.view_layer.update()
        return _used_mesh_materials(self.obj, bpy.context)

    def test_different_used_materials_and_only_second(self):
        self.mesh.polygons[1].material_index = 1
        self.assertEqual(self.check(), [self.a, self.b])
        self.mesh.polygons[0].material_index = 1
        self.assertEqual(self.check(), [self.b])

    def test_unused_slot_and_duplicate_material_are_allowed(self):
        self.assertEqual(self.check(), [self.a])
        self.mesh.materials[1] = self.a
        self.mesh.polygons[1].material_index = 1
        self.assertEqual(self.check(), [self.a])

    def test_object_linked_slot_is_effective_and_per_instance(self):
        second = bpy.data.objects.new('second_instance', self.mesh)
        bpy.context.scene.collection.objects.link(second)
        self.addCleanup(bpy.data.objects.remove, second, do_unlink=True)
        self.mesh.polygons[1].material_index = 1
        self.obj.material_slots[1].link = 'OBJECT'
        self.obj.material_slots[1].material = self.a
        self.assertEqual(self.check(), [self.a])
        self.assertEqual(_used_mesh_materials(second, bpy.context), [self.a, self.b])

    def test_evaluated_modifier_material_is_checked(self):
        modifier = self.obj.modifiers.new('solidify', 'SOLIDIFY')
        modifier.thickness = .1
        modifier.material_offset = 1
        self.assertEqual(self.check(), [self.a, self.b])

    def test_inherit_does_not_apply_guard(self):
        self.mesh.polygons[1].material_index = 1
        # Geometry inheritance intentionally writes no material. Check the
        # direct function passes the guard without publishing an actual file.
        stage = Usd.Stage.CreateInMemory()
        with patch('rtx_remix_importer.operators.export_operator._write_ingested_mesh_asset_impl', return_value=None):
            self.assertIsNone(write_ingested_mesh_asset(stage, self.obj, bpy.context, 'mesh', mathutils.Matrix.Identity(4),
                                                        self.a, True, '', '', UsdGeom.Tokens.z))
