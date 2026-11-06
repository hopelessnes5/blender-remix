"""Apply Mod follows official Toolkit mod semantics for scene-related content.

The mod holds replacement content on ``mesh_<HASH>`` prototype records
(``references = None`` resets plus ``ref_*`` asset children, the layout
NVIDIA's Toolkit authors); placements stay on ``inst_<HASH>[_<N>]`` prims
owned by the capture. Changes unrelated to the current scene and capture
(16-character hash relation) must never reach scene creation.
"""
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import bpy
from pxr import Sdf, Vt, Usd, UsdGeom, UsdLux

from rtx_remix_importer.ui.operators.mod_loader import ModFileLoader


MESH_HASH = 'ABCDEF0123456789'
LOOSE_HASH = '773BEAE9989DD021'
FOREIGN_HASH = 'DEF1234567890ABC'
SKY_HASH = 'A1B2C3D401234567'
MATERIAL_HASH = 'B7C8D9E0F1A23456'

CAPTURE_MESH_PATH = '/RootNode/meshes/mesh_' + MESH_HASH
CAPTURE_MESH_PRIM_PATH = CAPTURE_MESH_PATH + '/mesh_' + MESH_HASH
CAPTURE_INSTANCE_PATH = '/RootNode/instances/inst_' + MESH_HASH + '_0'
LOOSE_INSTANCE_PATH = '/RootNode/instances/inst_' + LOOSE_HASH + '_0'
LOOSE_PROTOTYPE_PATH = '/RootNode/meshes/mesh_' + LOOSE_HASH
REPLACED_PROTOTYPE_PATH = CAPTURE_MESH_PATH
REPLACEMENT_REF_PATH = CAPTURE_MESH_PATH + '/ref_x'
ADDED_INSTANCE_PATH = '/RootNode/instances/inst_' + MESH_HASH + '_9'
ADDED_LIGHT_PATH = CAPTURE_MESH_PATH + '/light_' + MESH_HASH
SKY_MESH_PATH = '/RootNode/meshes/mesh_' + SKY_HASH
SKY_MESH_PRIM_PATH = SKY_MESH_PATH + '/mesh'
SKY_INSTANCE_PATH = '/RootNode/instances/inst_' + SKY_HASH + '_0'
MATERIAL_PATH = '/RootNode/Looks/mat_' + MATERIAL_HASH
MATERIAL_SHADER_PATH = MATERIAL_PATH + '/Shader'
ADDED_LIGHT_WARM_PATH = CAPTURE_MESH_PATH + '/light_7DDC3F02BAA1C501'
FOREIGN_INSTANCE_PATH = '/RootNode/instances/inst_' + FOREIGN_HASH + '_0'
FOREIGN_MESH_PATH = '/RootNode/meshes/mesh_' + FOREIGN_HASH + '_0'
FOREIGN_LIGHT_PATH = '/RootNode/lights/light_' + FOREIGN_HASH


class TestModApplyChangedPrims(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.added_scene_props = []
        if not hasattr(bpy.types.Scene, 'remix_export_scale'):
            bpy.types.Scene.remix_export_scale = bpy.props.FloatProperty(default=1.0)
            cls.added_scene_props.append('remix_export_scale')

    @classmethod
    def tearDownClass(cls):
        for name in cls.added_scene_props:
            delattr(bpy.types.Scene, name)

    def setUp(self):
        self.root = Sdf.Layer.CreateAnonymous('mod_modtest.usda')
        self.repl = Sdf.Layer.CreateAnonymous('mod_replacement.usda')
        self.capture = Sdf.Layer.CreateAnonymous('mod_capture.usda')
        self.proto = Sdf.Layer.CreateAnonymous('modtest_proto.usda')
        self.asset = Sdf.Layer.CreateAnonymous('modtest_asset.usda')
        self.repl.customLayerData = {'lightspeed_layer_type': 'replacement'}
        self.capture.customLayerData = {'lightspeed_layer_type': 'capture'}
        self.root.subLayerPaths = [self.repl.identifier, self.capture.identifier]
        self.stage = Usd.Stage.Open(self.root.identifier, Usd.Stage.LoadAll)

        self._author_capture()
        self._author_mod_changed_prims()

        self.objects_before = set(bpy.data.objects.keys())

    def tearDown(self):
        for name in list(bpy.data.objects.keys()):
            if name not in self.objects_before:
                bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
        for collection in (bpy.data.meshes, bpy.data.lights, bpy.data.materials):
            for block in list(collection):
                if block.users == 0:
                    collection.remove(block)
        scene_collection = bpy.context.scene.collection
        for child in list(scene_collection.children):
            if child.name.startswith('mod '):
                scene_collection.children.unlink(child)
        for collection in list(bpy.data.collections):
            if collection.name.startswith('mod ') and collection.users == 0:
                bpy.data.collections.remove(collection)

    def _spec_mesh(self, layer: Sdf.Layer, prim_path: str, points):
        """Author a triangle ``Mesh`` prim spec (3 vertices, one face)."""
        spec = Sdf.CreatePrimInLayer(layer, Sdf.Path(prim_path))
        spec.specifier = Sdf.SpecifierDef
        spec.typeName = 'Mesh'

        def _add_attr(name: str, type_name, default_value):
            # Schema attributes must not be custom; the keyword and its
            # default differ across USD builds.
            try:
                attr = Sdf.AttributeSpec(spec, name, type_name, custom=False)
            except Exception:
                attr = Sdf.AttributeSpec(spec, name, type_name)
            attr.default = default_value
            return attr

        _add_attr('points', Sdf.ValueTypeNames.Float3Array, Vt.Vec3fArray(points))
        _add_attr('faceVertexCounts', Sdf.ValueTypeNames.IntArray, Vt.IntArray((len(points),)))
        _add_attr('faceVertexIndices', Sdf.ValueTypeNames.IntArray, Vt.IntArray(tuple(range(len(points)))))

    def _author_capture(self):
        """Record the pre-mod state: capture content reached through references."""
        # The game asset itself lives on a data layer referenced by the
        # capture's mesh container, like real captures ingest prototypes.
        container_path = '/RootNode/meshes/mesh_' + MESH_HASH
        self._spec_mesh(
            self.proto, container_path + '/mesh_' + MESH_HASH,
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))

        modtest_asset = self.asset
        self._spec_mesh(
            modtest_asset, '/RootNode/part_one',
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))
        self._spec_mesh(
            modtest_asset, '/RootNode/part_two',
            ((10.0, 0.0, 0.0), (11.0, 0.0, 0.0), (10.0, 1.0, 0.0)))

        self.stage.SetEditTarget(Usd.EditTarget(self.capture))
        self.stage.OverridePrim(Sdf.Path('/RootNode'))

        container = self.stage.DefinePrim(Sdf.Path(CAPTURE_MESH_PATH), 'Xform')
        self.assertTrue(container)
        container.GetReferences().AddReference(
            assetPath=self.proto.identifier, primPath=Sdf.Path(container_path))
        mesh_prim = self.stage.GetPrimAtPath(Sdf.Path(CAPTURE_MESH_PRIM_PATH))
        self.assertTrue(mesh_prim)
        self.assertEqual(len(UsdGeom.Mesh(mesh_prim).GetPointsAttr().Get()), 3)

        capture_inst = self.stage.DefinePrim(Sdf.Path(CAPTURE_INSTANCE_PATH), 'Xform')
        self.assertTrue(capture_inst)
        capture_inst.GetReferences().AddInternalReference(Sdf.Path(CAPTURE_MESH_PATH))
        UsdGeom.XformCommonAPI(capture_inst).SetTranslate((2.0, -3.0, 0.0))

        # A second captured instance of an unrelated prototype: its hash stays
        # capture-known, so a mod reset of that prototype stays related.
        loose_inst = self.stage.DefinePrim(Sdf.Path(LOOSE_INSTANCE_PATH), 'Xform')
        self.assertTrue(loose_inst)
        UsdGeom.XformCommonAPI(loose_inst).SetTranslate((1.0, 2.0, 3.0))

        # Another scene-related prototype, whose mesh child is named ``mesh``
        # (the layout real captures use). The mod edits it with over-only
        # flags; the record must not swap or duplicate scene geometry.
        sky_container = self.stage.DefinePrim(Sdf.Path(SKY_MESH_PATH), 'Xform')
        self.assertTrue(sky_container)
        sky_container.GetReferences().AddReference(
            assetPath=self.proto.identifier,
            primPath=Sdf.Path('/RootNode/meshes/mesh_' + SKY_HASH))
        sky_inst = self.stage.DefinePrim(Sdf.Path(SKY_INSTANCE_PATH), 'Xform')
        self.assertTrue(sky_inst)
        sky_inst.GetReferences().AddInternalReference(Sdf.Path(SKY_MESH_PATH))

    def _author_mod_changed_prims(self):
        """The replacement layer: update, reset, replacement, additions."""
        self.stage.SetEditTarget(Usd.EditTarget(self.repl))
        self.stage.OverridePrim(Sdf.Path('/RootNode'))

        # Existing placement: moved and hidden.
        moved = self.stage.OverridePrim(Sdf.Path(CAPTURE_INSTANCE_PATH))
        self.assertTrue(moved)
        UsdGeom.XformCommonAPI(moved).SetTranslate((5.0, 6.0, 0.0))
        UsdGeom.Imageable(moved).GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)

        # Prototype replacement: references reset, asset parts added in.
        # ``SetReferences([])`` is the empty list op the Toolkit authoring
        # uses (``references = None``); ClearReferences authors nothing when
        # the edit target has no previous opinions.
        replaced = self.stage.OverridePrim(Sdf.Path(REPLACED_PROTOTYPE_PATH))
        self.assertTrue(replaced)
        replaced.GetReferences().SetReferences([])
        ref_child = self.stage.DefinePrim(Sdf.Path(REPLACEMENT_REF_PATH), 'Xform')
        self.assertTrue(ref_child)
        ref_child.GetReferences().AddReference(
            assetPath=self.asset.identifier, primPath=Sdf.Path('/RootNode'))
        UsdGeom.XformCommonAPI(ref_child).SetTranslate((0.0, 0.0, 0.0))

        # An empty reset: the prototype is gone (capture-locally known only).
        gone = self.stage.OverridePrim(Sdf.Path(LOOSE_PROTOTYPE_PATH))
        self.assertTrue(gone)
        gone.GetReferences().SetReferences([])

        # An added placement of the still-original prototype.
        added = self.stage.DefinePrim(Sdf.Path(ADDED_INSTANCE_PATH), 'Xform')
        self.assertTrue(added)
        added.GetReferences().AddInternalReference(Sdf.Path(CAPTURE_MESH_PATH))
        UsdGeom.XformCommonAPI(added).SetTranslate((7.0, 0.0, -2.0))

        # A scene light of the replaced prototype, Toolkit-style: nested
        # under the mesh path and defined once, not per placement.
        light = UsdLux.SphereLight.Define(self.stage, Sdf.Path(ADDED_LIGHT_PATH))
        self.assertTrue(light)
        light.GetIntensityAttr().Set(50.0)

        # A color-temperature light (Toolkit attribute names): the Kelvin
        # value rides on Blender's native light temperature, not on an
        # authored color.
        warm = UsdLux.SphereLight.Define(self.stage, Sdf.Path(ADDED_LIGHT_WARM_PATH))
        self.assertTrue(warm)
        warm_prim = self.stage.GetPrimAtPath(Sdf.Path(ADDED_LIGHT_WARM_PATH))
        warm_prim.CreateAttribute(
            'inputs:enableColorTemperature', Sdf.ValueTypeNames.Bool).Set(True)
        warm_prim.CreateAttribute(
            'inputs:colorTemperature', Sdf.ValueTypeNames.Float).Set(6500.0)
        warm_prim.CreateAttribute('inputs:intensity', Sdf.ValueTypeNames.Float).Set(10.0)

        # Over-only prototype flag edits (real mods set remix_category:* this
        # way) live on capture-owned records: the placement keeps its
        # transform and no scene object may be created from the record.
        sky_over = self.stage.OverridePrim(Sdf.Path(SKY_MESH_PATH))
        self.assertTrue(sky_over)
        sky_flag = self.stage.OverridePrim(Sdf.Path(SKY_MESH_PRIM_PATH))
        self.assertTrue(sky_flag)
        sky_flag.CreateAttribute(
            'remix_category:sky', Sdf.ValueTypeNames.Bool, custom=True
        ).Set(True)

        # Unrelated to the current scene and capture: must not be applied.
        foreign_inst = self.stage.DefinePrim(Sdf.Path(FOREIGN_INSTANCE_PATH), 'Xform')
        self.assertTrue(foreign_inst)
        foreign_mesh = self.stage.DefinePrim(Sdf.Path(FOREIGN_MESH_PATH), 'Xform')
        self.assertTrue(foreign_mesh)
        foreign_mesh.GetReferences().AddReference(
            assetPath=self.asset.identifier, primPath=Sdf.Path('/RootNode'))
        foreign_light = UsdLux.SphereLight.Define(
            self.stage, Sdf.Path(FOREIGN_LIGHT_PATH))
        self.assertTrue(foreign_light)
        foreign_light.GetIntensityAttr().Set(5.0)

    def _wire_loader(self):
        collection = getattr(bpy.context, 'collection', None) or bpy.context.scene.collection
        context = SimpleNamespace(scene=bpy.context.scene, collection=collection)
        loader = ModFileLoader(context, Mock())
        loader.stage = self.stage
        loader.xform_cache = UsdGeom.XformCache(loader.time_code)
        loader.up_axis_is_y = False

        mesh = bpy.data.meshes.new('ExistingCaptureInstanceMesh')
        existing = bpy.data.objects.new('ExistingCaptureInstance', mesh)
        # Capture-imported objects carry BOTH provenance tags: the instance
        # placement and the prototype container the instance references.
        existing['usd_instance_path'] = CAPTURE_INSTANCE_PATH
        existing['usd_prim_path'] = CAPTURE_MESH_PATH
        collection.objects.link(existing)
        self.existing = existing

        # Second capture object for the flag-edited prototype: same pair of
        # tags, and the mod only sets over-only flags on its record.
        sky_mesh = bpy.data.meshes.new('ExistingSkyInstanceMesh')
        sky_mesh.from_pydata(((0, 0, 0), (1, 0, 0), (0, 1, 0)), [], [(0, 1, 2)])
        sky_obj = bpy.data.objects.new('ExistingSkyInstance', sky_mesh)
        sky_obj.location = (3.0, 3.0, 3.0)
        sky_obj['usd_instance_path'] = SKY_INSTANCE_PATH
        sky_obj['usd_prim_path'] = SKY_MESH_PATH
        collection.objects.link(sky_obj)
        self.sky_obj = sky_obj

        loader._build_object_map()
        self.assertIn(CAPTURE_INSTANCE_PATH, loader.blender_object_map)
        # One key per object: the map's elif chain keys only the instance tag,
        # so container paths never shadow the placements (the old belief that
        # both tags keyed the same object is what this file used to assert).
        self.assertNotIn(CAPTURE_MESH_PATH, loader.blender_object_map)
        self.assertIn(SKY_INSTANCE_PATH, loader.blender_object_map)
        self.assertNotIn(SKY_MESH_PATH, loader.blender_object_map)
        return loader

    def test_changed_prims_cover_mod_edits_only(self):
        loader = self._wire_loader()

        layers = loader._get_layers_with_changes()
        self.assertEqual([layer.identifier for layer in layers], [self.repl.identifier])

        changed_prims, changed_materials = loader._get_changed_prims(layers)
        self.assertEqual(changed_materials, set())
        self.assertEqual(changed_prims, {
            CAPTURE_INSTANCE_PATH,
            REPLACED_PROTOTYPE_PATH,
            REPLACEMENT_REF_PATH,
            LOOSE_PROTOTYPE_PATH,
            ADDED_INSTANCE_PATH,
            ADDED_LIGHT_PATH,
            ADDED_LIGHT_WARM_PATH,
            SKY_MESH_PRIM_PATH,
        })

    def test_over_record_material_reaches_hash_objects(self):
        loader = self._wire_loader()

        # The mod authors an over-only rebinding on the capture-owned sky
        # record: the Look changes, not the placement transforms. The record
        # is neither defined nor reference-authored, so no geometry may swap.
        self.stage.SetEditTarget(Usd.EditTarget(self.repl))
        sky_over = self.stage.GetPrimAtPath(Sdf.Path(SKY_MESH_PATH))
        self.assertTrue(sky_over and sky_over.IsValid())
        sky_over.CreateRelationship('material:binding').AddTarget(
            Sdf.Path(MATERIAL_PATH))
        # An attribute-only shader under that Look is how changed_materials
        # learns the Look changed (real mods override shader inputs). No
        # binding is needed for the material rescope itself.
        self.stage.OverridePrim(Sdf.Path(MATERIAL_SHADER_PATH))
        shader_over = self.stage.GetPrimAtPath(Sdf.Path(MATERIAL_SHADER_PATH))
        self.assertTrue(shader_over)
        shader_over.CreateAttribute(
            'inputs:roughness', Sdf.ValueTypeNames.Float).Set(0.5)

        layers = loader._get_layers_with_changes()
        changed_prims, changed_materials = loader._get_changed_prims(layers)
        self.assertEqual(changed_materials, {MATERIAL_PATH})
        self.assertIn(SKY_MESH_PATH, changed_prims)
        self.assertIn(MATERIAL_SHADER_PATH, changed_prims)

        rebuild = bpy.data.materials.new('mat_over_rebind')
        with patch(
            'rtx_remix_importer.mod_apply_utils.get_or_create_mod_instance_material_util',
            return_value=rebuild,
        ) as util:
            for prim_path in sorted(changed_prims):
                prim = self.stage.GetPrimAtPath(Sdf.Path(prim_path))
                self.assertTrue(prim and prim.IsValid(), prim_path)
                loader._process_prim(prim)

        # The rebinding rebuilt one material and reskinned exactly the
        # objects of the record's hash, linked per object so other users of
        # the mesh datablock stay untouched.
        self.assertEqual(util.call_count, 1)
        self.assertEqual(util.call_args.kwargs['base_material_usd_path'], MATERIAL_PATH)
        self.assertIsNone(util.call_args.kwargs['existing_bl_material_param'])
        self.assertEqual(self.sky_obj.material_slots[0].link, 'OBJECT')
        self.assertEqual(self.sky_obj.material_slots[0].material, rebuild)
        self.assertEqual(self.sky_obj.data.materials[0], None)
        self.assertEqual(loader.stats['materials_applied'], 1)

        # Nothing but materials: placement transform, visibility, geometry
        # and the other capture object of a different hash stay as they were.
        for axis, expected in zip('xyz', (3.0, 3.0, 3.0)):
            self.assertAlmostEqual(getattr(self.sky_obj.location, axis), expected, places=4)
        self.assertFalse(self.sky_obj.hide_viewport)
        self.assertFalse(self.sky_obj.hide_render)
        self.assertEqual(len(self.sky_obj.data.vertices), 3)
        self.assertEqual(len(self.existing.material_slots), 0)
        self.assertEqual(
            [o for o in bpy.data.objects
             if o.get('usd_instance_path') == SKY_INSTANCE_PATH],
            [self.sky_obj])

    def test_apply_patches_scene_from_changed_prims(self):
        loader = self._wire_loader()
        layers = loader._get_layers_with_changes()
        changed_prims, _ = loader._get_changed_prims(layers)

        for prim_path in sorted(changed_prims):
            prim = self.stage.GetPrimAtPath(Sdf.Path(prim_path))
            self.assertTrue(prim and prim.IsValid(), prim_path)
            loader._process_prim(prim)

        # The mod collection got created from the anonymous file's stem and
        # is linked into the scene.
        mod_coll = bpy.data.collections.get('mod mod_modtest')
        self.assertIsNotNone(mod_coll)
        self.assertIn(
            mod_coll.name,
            [child.name for child in bpy.context.scene.collection.children])

        # Replaced instance: same object, swapped prototype data, untouched
        # matrix, visibility and provenance tags.
        self.assertEqual(len(self.existing.data.vertices), 6)
        self.assertEqual(len(self.existing.data.polygons), 2)
        self.assertEqual(max(vert.co.x for vert in self.existing.data.vertices), 11.0)
        self.assertEqual(len(self.existing.material_slots), 0)
        for slot in self.existing.material_slots:
            self.assertEqual(slot.link, 'DATA')
        for axis, expected in zip('xyz', (5.0, 6.0, 0.0)):
            self.assertAlmostEqual(getattr(self.existing.location, axis), expected, places=4)
        self.assertTrue(self.existing.hide_viewport)
        self.assertTrue(self.existing.hide_render)
        self.assertEqual(self.existing['usd_instance_path'], CAPTURE_INSTANCE_PATH)
        self.assertEqual(self.existing['usd_prim_path'], CAPTURE_MESH_PATH)
        capture_instances = [o for o in bpy.data.objects
                             if o.get('usd_instance_path') == CAPTURE_INSTANCE_PATH]
        self.assertEqual(capture_instances, [self.existing])

        # Over-only flag edits on a capture-owned prototype record keep the
        # paired scene object intact: no transform reset to the container's
        # identity L2W, no visibility change, and no duplicate creation.
        sky_objects = [o for o in bpy.data.objects
                       if o.get('usd_instance_path') == SKY_INSTANCE_PATH]
        self.assertEqual(sky_objects, [self.sky_obj])
        for axis, expected in zip('xyz', (3.0, 3.0, 3.0)):
            self.assertAlmostEqual(getattr(self.sky_obj.location, axis), expected, places=4)
        self.assertFalse(self.sky_obj.hide_viewport)
        self.assertFalse(self.sky_obj.hide_render)
        self.assertEqual(len(self.sky_obj.data.vertices), 3)
        self.assertNotIn(mod_coll.name, [c.name for c in self.sky_obj.users_collection])

        # Added placement of the same hash: the replacement composes through
        # the referenced prototype, so one object per asset part is created.
        added = [o for o in bpy.data.objects
                 if o.get('usd_instance_path') == ADDED_INSTANCE_PATH]
        self.assertEqual(len(added), 2)
        for added_obj in added:
            self.assertEqual(len(added_obj.data.vertices), 3)
            self.assertEqual(len(added_obj.data.polygons), 1)
        self.assertEqual(
            {obj.name for obj in added},
            {'inst_ABCDEF0123456789_9_mod', 'inst_ABCDEF0123456789_9_mod.001'})
        for added_obj in added:
            for axis, expected in zip('xyz', (7.0, 0.0, -2.0)):
                self.assertAlmostEqual(getattr(added_obj.location, axis), expected, places=4)
            self.assertTrue(
                str(added_obj.get('usd_prim_path', '')).endswith(
                    ('/part_one', '/part_two')))
        # Part data carries the asset-local offsets; matrices stay equal.
        self.assertEqual(
            {max(vert.co.x for vert in obj.data.vertices) for obj in added},
            {1.0, 11.0})

        # One light per captured instance, parented to its own placement,
        # never one prototype-space light at the origin.
        lights = [o for o in bpy.data.objects
                  if o.get('usd_light_path') == ADDED_LIGHT_PATH]
        self.assertEqual(len(lights), 2)
        self.assertEqual({o.get('usd_light_instance_path') for o in lights},
                         {CAPTURE_INSTANCE_PATH, ADDED_INSTANCE_PATH})
        for light_obj in lights:
            self.assertEqual(light_obj.data.type, 'POINT')
            self.assertAlmostEqual(light_obj.data.energy, 50.0, places=4)
            anchor_path = light_obj.get('usd_light_instance_path')
            self.assertEqual(light_obj.parent.get('usd_instance_path'), anchor_path)
            expect_x = 5.0 if anchor_path == CAPTURE_INSTANCE_PATH else 7.0
            self.assertAlmostEqual(light_obj.matrix_world.translation.x, expect_x, places=4)

        # The color-temperature light: energy from intensity; the Kelvin
        # value goes to Blender's native temperature properties and the
        # light keeps the temperature-free USD color as its tint.
        warm_lights = [o for o in bpy.data.objects
                       if o.get('usd_light_path') == ADDED_LIGHT_WARM_PATH]
        self.assertEqual(len(warm_lights), 2)
        self.assertEqual({o.get('usd_light_instance_path') for o in warm_lights},
                         {CAPTURE_INSTANCE_PATH, ADDED_INSTANCE_PATH})
        for light_obj in warm_lights:
            self.assertEqual(light_obj.data.type, 'POINT')
            self.assertAlmostEqual(light_obj.data.energy, 10.0, places=4)
        warm_data = warm_lights[0].data
        self.assertTrue(warm_data.use_temperature)
        self.assertAlmostEqual(warm_data.temperature, 6500.0, places=4)
        self.assertEqual(tuple(warm_data.color), (1.0, 1.0, 1.0))
        # Blender's blackbody, not the tint, carries the temperature.
        self.assertNotEqual(tuple(warm_data.temperature_color), (1.0, 1.0, 1.0))
        self.assertAlmostEqual(
            warm_lights[0].data['usd_color_temperature'], 6500.0, places=4)

        # Created content lives only in the mod collection; the replaced
        # capture object stays where the capture import put it.
        for obj in added + lights + warm_lights:
            self.assertEqual([c.name for c in obj.users_collection], [mod_coll.name])
        self.assertNotIn(mod_coll.name, [c.name for c in self.existing.users_collection])
        self.assertNotIn(mod_coll.name, [c.name for c in self.sky_obj.users_collection])
        self.assertEqual(loader.stats['objects_created'], 6)

        # Nothing from unrelated parts of the mod, no capture prototype
        # duplicates, and nothing from the emptied reset either.
        tag_paths = {o.get('usd_instance_path') for o in bpy.data.objects
                     if 'usd_instance_path' in o}
        self.assertEqual(
            tag_paths, {CAPTURE_INSTANCE_PATH, ADDED_INSTANCE_PATH,
                        SKY_INSTANCE_PATH})
        for obj in bpy.data.objects:
            self.assertNotIn(
                obj.get('usd_instance_path', ''),
                (CAPTURE_MESH_PATH, CAPTURE_MESH_PRIM_PATH, LOOSE_PROTOTYPE_PATH))
        foreign = [o for o in bpy.data.objects
                   if FOREIGN_HASH in str(o.get('usd_instance_path', ''))
                   or FOREIGN_HASH in str(o.get('usd_light_path', ''))]
        self.assertFalse(foreign)


    def test_referenced_asset_nested_light_is_materialized_per_instance(self):
        """References aren't layer stack entries; scan composed child lights."""
        asset_stage = Usd.Stage.Open(self.asset.identifier, Usd.Stage.LoadAll)
        nested_path = '/RootNode/Klab_A/PrimaryLights/TankA'
        nested = UsdLux.SphereLight.Define(asset_stage, Sdf.Path(nested_path))
        self.assertTrue(nested)
        nested.GetIntensityAttr().Set(12.0)
        UsdGeom.XformCommonAPI(nested.GetPrim()).SetTranslate((1.0, 2.0, 3.0))
        composed_light_path = REPLACEMENT_REF_PATH + '/Klab_A/PrimaryLights/TankA'
        loader = self._wire_loader()
        layers = loader._get_layers_with_changes()
        changed_prims, _ = loader._get_changed_prims(layers)
        # This light is only in an asset referenced by the mod's ref_x child.
        self.assertNotIn(composed_light_path, changed_prims)
        for path in sorted(changed_prims):
            prim = self.stage.GetPrimAtPath(Sdf.Path(path))
            if prim and prim.IsValid():
                loader._process_prim(prim)
        lights = [obj for obj in bpy.data.objects if obj.get('usd_light_path') == composed_light_path]
        self.assertEqual(len(lights), 2)
        by_instance = {obj['usd_light_instance_path']: obj for obj in lights}
        self.assertEqual(set(by_instance), {CAPTURE_INSTANCE_PATH, ADDED_INSTANCE_PATH})
        self.assertAlmostEqual(by_instance[CAPTURE_INSTANCE_PATH].matrix_world.translation.x, 6.0, places=4)
        self.assertAlmostEqual(by_instance[ADDED_INSTANCE_PATH].matrix_world.translation.x, 8.0, places=4)
        for obj in lights:
            self.assertAlmostEqual(obj.matrix_world.translation.y, 2.0 if obj['usd_light_instance_path'] == ADDED_INSTANCE_PATH else 8.0, places=4)
            self.assertAlmostEqual(obj.data.energy, 12.0, places=4)

    def test_legacy_unparented_prototype_light_is_replaced(self):
        """An old mod-created light at the prototype origin must not survive."""
        loader = self._wire_loader()
        old_data = bpy.data.lights.new('OldPrototypeLightData', type='POINT')
        old_light = bpy.data.objects.new('OldPrototypeLight', old_data)
        old_light['usd_light_path'] = ADDED_LIGHT_PATH
        old_light['usd_instance_path'] = ADDED_LIGHT_PATH
        loader._target_collection().objects.link(old_light)
        old_name = old_light.name

        layers = loader._get_layers_with_changes()
        changed, _ = loader._get_changed_prims(layers)
        for path in sorted(changed):
            prim = self.stage.GetPrimAtPath(Sdf.Path(path))
            if prim and prim.IsValid():
                loader._process_prim(prim)
        self.assertNotIn(old_name, bpy.data.objects)
        self.assertEqual(len([o for o in bpy.data.objects
                              if o.get('usd_light_path') == ADDED_LIGHT_PATH]), 2)

    def test_repeated_apply_keeps_one_light_per_instance(self):
        loader = self._wire_loader()
        for _ in range(2):
            loader._build_object_map()
            layers = loader._get_layers_with_changes()
            changed, _ = loader._get_changed_prims(layers)
            for path in sorted(changed):
                prim = self.stage.GetPrimAtPath(Sdf.Path(path))
                if prim and prim.IsValid():
                    loader._process_prim(prim)
        for source in (ADDED_LIGHT_PATH, ADDED_LIGHT_WARM_PATH):
            lights = [obj for obj in bpy.data.objects if obj.get('usd_light_path') == source]
            self.assertEqual(len(lights), 2)
            self.assertEqual(len({obj['usd_light_instance_path'] for obj in lights}), 2)



    @staticmethod
    def _give_uv(mesh):
        uv_layer = mesh.uv_layers.new(name='st')
        uv_layer.data.foreach_set('uv', (0.0, 0.0, 1.0, 0.0, 0.0, 1.0))
        return uv_layer

    def test_light_only_prototype_does_not_hide_mesh_or_drop_uv(self):
        """A ref reset with only added lights isn't a request to erase Mesh."""
        loader = self._wire_loader()
        self._give_uv(self.sky_obj.data)
        original_data = self.sky_obj.data
        self.stage.SetEditTarget(Usd.EditTarget(self.repl))
        proto = self.stage.OverridePrim(Sdf.Path(SKY_MESH_PATH))
        proto.GetReferences().SetReferences([])
        light = UsdLux.SphereLight.Define(self.stage, Sdf.Path(SKY_MESH_PATH + '/Lamp'))
        light.GetIntensityAttr().Set(24.0)
        layers = loader._get_layers_with_changes()
        changed, _ = loader._get_changed_prims(layers)
        for path in sorted(changed):
            prim = self.stage.GetPrimAtPath(Sdf.Path(path))
            if prim and prim.IsValid():
                loader._process_prim(prim)
        self.assertIs(self.sky_obj.data, original_data)
        self.assertFalse(self.sky_obj.hide_viewport)
        self.assertFalse(self.sky_obj.hide_render)
        self.assertEqual(len(self.sky_obj.data.uv_layers), 1)
        self.assertEqual(tuple(self.sky_obj.data.uv_layers[0].data[1].uv), (1.0, 0.0))

    def _author_sky_replacement_without_uv(self, points, faces):
        self.stage.SetEditTarget(Usd.EditTarget(self.repl))
        proto = self.stage.OverridePrim(Sdf.Path(SKY_MESH_PATH))
        proto.GetReferences().SetReferences([])
        mesh = UsdGeom.Mesh.Define(self.stage, Sdf.Path(SKY_MESH_PATH + '/part'))
        mesh.GetPointsAttr().Set(points)
        mesh.GetFaceVertexCountsAttr().Set([len(face) for face in faces])
        mesh.GetFaceVertexIndicesAttr().Set([i for face in faces for i in face])
        return proto

    def test_replacement_preserves_original_uv_on_matching_topology(self):
        loader = self._wire_loader()
        self._give_uv(self.sky_obj.data)
        original = self.sky_obj.data
        proto = self._author_sky_replacement_without_uv(
            [(0, 0, 0), (2, 0, 0), (0, 2, 0)], [(0, 1, 2)])
        loader._get_changed_prims(loader._get_layers_with_changes())
        loader._replace_prototype_hash({SKY_HASH}, proto)
        self.assertIsNot(self.sky_obj.data, original)
        self.assertEqual(len(self.sky_obj.data.uv_layers), 1)
        self.assertEqual(tuple(self.sky_obj.data.uv_layers[0].data[1].uv), (1.0, 0.0))
        self.assertFalse(self.sky_obj.hide_viewport)

    def test_replacement_with_different_topology_and_no_uv_is_skipped(self):
        loader = self._wire_loader()
        self._give_uv(self.sky_obj.data)
        original = self.sky_obj.data
        proto = self._author_sky_replacement_without_uv(
            [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], [(0, 1, 2, 3)])
        loader._get_changed_prims(loader._get_layers_with_changes())
        loader._replace_prototype_hash({SKY_HASH}, proto)
        self.assertIs(self.sky_obj.data, original)
        self.assertEqual(len(self.sky_obj.data.uv_layers), 1)
        self.assertFalse(self.sky_obj.hide_viewport)

    def test_mod_ngon_keeps_corner_indices_for_uv(self):
        """The generic mod mesh extractor must not fan-triangulate a pentagon."""
        from rtx_remix_importer import mod_apply_utils
        self.stage.SetEditTarget(Usd.EditTarget(self.repl))
        mesh = UsdGeom.Mesh.Define(self.stage, Sdf.Path('/Ngon'))
        mesh.GetPointsAttr().Set([
            (0, 0, 0), (1, 0, 0), (2, 1, 0), (1, 2, 0), (0, 1, 0)])
        mesh.GetFaceVertexCountsAttr().Set([5])
        mesh.GetFaceVertexIndicesAttr().Set([0, 1, 2, 3, 4])
        uv = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
            'st', Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
        uv.Set([(0, 0), (1, 0), (1, 1), (0.5, 1), (0, 1)])
        extracted = mod_apply_utils.get_mesh_data_from_mod(
            mesh.GetPrim(), Usd.TimeCode.Default(), False, lambda *_: None)
        self.assertEqual(len(extracted[1]), 1)
        self.assertEqual(len(extracted[1][0]), 5)
        self.assertEqual(len(extracted[2][0]), 5)


if __name__ == '__main__':
    unittest.main()
