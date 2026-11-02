"""Selection-wide texture conflict checks must run before any publication."""
import os
import tempfile
import types
import unittest
from unittest import mock

import bpy
from rtx_remix_importer.operators import export_operator as export


class TexturePreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.added_property = not hasattr(bpy.types.Scene, 'remix_custom_texture_dir')
        if cls.added_property:
            bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(default='rtx-remix/textures')

    @classmethod
    def tearDownClass(cls):
        if cls.added_property:
            del bpy.types.Scene.remix_custom_texture_dir

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.materials = []
        self.images = []
        self.addCleanup(self.cleanup_data)
        original = export._used_mesh_materials
        def used(obj, context):
            if isinstance(obj, types.SimpleNamespace):
                return [obj.material_slots[0].material]
            return original(obj, bpy.context)
        patcher = mock.patch.object(export, '_used_mesh_materials', side_effect=used)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cleanup_data(self):
        for material in self.materials:
            bpy.data.materials.remove(material, do_unlink=True)
        for image in self.images:
            bpy.data.images.remove(image)

    def material(self, image_name, image=None):
        mat = bpy.data.materials.new('PreflightMaterial')
        self.materials.append(mat)
        mat.use_nodes = True
        if image is None:
            image = bpy.data.images.new(image_name, 2, 2)
            self.images.append(image)
        tree = mat.node_tree
        node = tree.nodes.new('ShaderNodeTexImage')
        node.image = image
        shader = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
        tree.links.new(node.outputs['Color'], shader.inputs['Base Color'])
        return mat, image

    def obj(self, mat, kind='MESH'):
        return types.SimpleNamespace(type=kind, material_slots=[types.SimpleNamespace(material=mat)])

    def test_collision_aborts_before_any_disk_or_stage_work(self):
        a, _ = self.material('wall.png')
        b, _ = self.material('wall.jpg')
        scene = types.SimpleNamespace(remix_mod_file_path=os.path.join(self.temp.name, 'mod.usda'),
                                      remix_inherit_parent_material=False)
        context = types.SimpleNamespace(scene=scene)
        operator = mock.Mock()
        target = os.path.join(self.temp.name, 'target.usda')
        with mock.patch.object(export, 'get_mesh_name_from_object', side_effect=lambda obj: obj.material_slots[0].material.name), \
             mock.patch.object(export, 'ensure_mdl_files') as mdl:
            result = export._export_remix_objects(operator, context, target, [self.obj(a), self.obj(b)])
        self.assertEqual(result, (False, 0, 0))
        self.assertIn('same export path', str(operator.report.call_args))
        mdl.assert_not_called()
        operator.get_or_create_stage.assert_not_called()
        self.assertEqual(os.listdir(self.temp.name), [])

    def test_shared_image_allowed_and_inherit_skips_mesh_material(self):
        a, image = self.material('shared.png')
        b, _ = self.material('unused', image)
        scene = types.SimpleNamespace(remix_inherit_parent_material=False)
        context = types.SimpleNamespace(scene=scene)
        plan = export._preflight_texture_destinations(context, [self.obj(a), self.obj(b)],
                                                     self.temp.name, os.path.join(self.temp.name, 'x.usda'))
        self.assertEqual(len(plan), 1)
        other, _ = self.material('shared.jpg')
        scene.remix_inherit_parent_material = True
        plan = export._preflight_texture_destinations(context, [self.obj(a), self.obj(other)],
                                                     self.temp.name, os.path.join(self.temp.name, 'x.usda'))
        self.assertEqual(plan, {})

    def test_material_replacement_checks_mesh_even_when_inheriting(self):
        a, _ = self.material('replace.png')
        b, _ = self.material('replace.jpg')
        context = types.SimpleNamespace(scene=types.SimpleNamespace(remix_inherit_parent_material=True))
        with self.assertRaisesRegex(ValueError, 'same export path'):
            export._preflight_texture_destinations(context, [self.obj(a), self.obj(b)],
                                                   self.temp.name, os.path.join(self.temp.name, 'x.usda'), True)

    def test_height_and_color_with_same_name_conflict(self):
        color, _ = self.material('brick.png')
        height, height_image = self.material('other.png')
        height_image.name = 'brick.jpg'
        tree = height.node_tree
        tex = next(node for node in tree.nodes if node.type == 'TEX_IMAGE')
        for link in list(tex.outputs['Color'].links):
            tree.links.remove(link)
        disp = tree.nodes.new('ShaderNodeDisplacement')
        output = next(node for node in tree.nodes if node.type == 'OUTPUT_MATERIAL')
        tree.links.new(tex.outputs['Color'], disp.inputs['Height'])
        tree.links.new(disp.outputs['Displacement'], output.inputs['Displacement'])
        context = types.SimpleNamespace(scene=types.SimpleNamespace(remix_inherit_parent_material=False))
        with self.assertRaisesRegex(ValueError, 'same export path|Height map conflicts'):
            export._preflight_texture_destinations(context, [self.obj(color), self.obj(height)],
                                                   self.temp.name, os.path.join(self.temp.name, 'x.usda'))

    def test_shared_mesh_objects_with_distinct_object_materials_conflict(self):
        a, _ = self.material('instance.png')
        b, _ = self.material('instance.jpg')
        mesh = bpy.data.meshes.new('PreflightSharedMesh')
        mesh.from_pydata([(0,0,0), (1,0,0), (0,1,0)], [], [(0,1,2)])
        self.addCleanup(bpy.data.meshes.remove, mesh, do_unlink=True)
        mesh.materials.append(a)
        objects = [bpy.data.objects.new('PreflightInstance', mesh) for _ in range(2)]
        for obj in objects:
            self.addCleanup(bpy.data.objects.remove, obj, do_unlink=True)
            obj.material_slots[0].link = 'OBJECT'
        objects[0].material_slots[0].material = a
        objects[1].material_slots[0].material = b
        context = types.SimpleNamespace(scene=types.SimpleNamespace(remix_inherit_parent_material=False))
        with self.assertRaisesRegex(ValueError, 'same export path'):
            export._preflight_texture_destinations(context, objects, self.temp.name,
                                                   os.path.join(self.temp.name, 'x.usda'))
