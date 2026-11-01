"""USD/Blender light and camera unit and basis conversion regressions."""
import math
import unittest
from unittest.mock import patch

import bpy
import mathutils
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux
from rtx_remix_importer.light_utils import create_light_from_usd
from rtx_remix_importer.blender_bridge.scene_builder import _create_camera
from rtx_remix_importer.operators.export_operator import export_light


class TestLightCameraRoundtrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.props = []
        for name, prop in [('remix_export_scale', bpy.props.FloatProperty(default=.01)),
                           ('remix_anchor_object_target', bpy.props.PointerProperty(type=bpy.types.Object))]:
            if not hasattr(bpy.types.Scene, name):
                setattr(bpy.types.Scene, name, prop)
                cls.props.append(name)

    @classmethod
    def tearDownClass(cls):
        for name in cls.props:
            delattr(bpy.types.Scene, name)

    def test_camera_clipping_scaled(self):
        stage = Usd.Stage.CreateInMemory()
        camera = UsdGeom.Camera.Define(stage, '/Camera')
        camera.CreateClippingRangeAttr(Gf.Vec2f(1, 1000))
        composition = type('Composition', (), {'up_axis_is_y': False, 'path': 'capture.usda'})()
        obj = _create_camera(bpy.context, composition, camera.GetPrim(), .01)
        try:
            self.assertAlmostEqual(obj.data.clip_start, .01, places=5)
            self.assertAlmostEqual(obj.data.clip_end, 10, places=5)
        finally:
            data = obj.data
            bpy.data.objects.remove(obj)
            bpy.data.cameras.remove(data)

    def test_imported_anchor_keeps_relative_light_units(self):
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        parent = UsdGeom.Xform.Define(stage, '/World/Parent')
        parent.AddTranslateOp().Set(Gf.Vec3d(40, 0, 0))
        prim = UsdLux.SphereLight.Define(stage, '/World/Parent/light_12345678').GetPrim()
        UsdGeom.Xformable(prim).AddTranslateOp().Set(Gf.Vec3d(20, 5, 0))
        anchor = bpy.data.objects.new('TestImportedAnchor', None)
        anchor['usd_instance_path'] = '/World/Parent'
        bpy.context.scene.collection.objects.link(anchor)
        anchor.matrix_world = mathutils.Matrix.Translation((.4, 0, 0)) @ mathutils.Matrix.Scale(.01, 4)
        bpy.context.view_layer.update()
        bpy.context.scene['remix_import_unit_scale'] = .01
        bpy.context.scene.remix_export_scale = .01
        bpy.context.scene.remix_anchor_object_target = anchor
        obj = create_light_from_usd(prim, stage, .01)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        out = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(out, UsdGeom.Tokens.z)
        reporter = type('Reporter', (), {'report': lambda *args: None})()
        try:
            with patch('rtx_remix_importer.operators.export_operator.get_game_stage', return_value=stage):
                self.assertTrue(export_light(reporter, bpy.context, obj, out, '', ''))
            exported = out.GetPrimAtPath(obj['usd_light_path'])
            matrix = UsdGeom.Xformable(exported).GetLocalTransformation()
            self.assertAlmostEqual(matrix[3][0], 20, delta=.001)
            self.assertAlmostEqual(matrix[3][1], 5, delta=.001)
            self.assertAlmostEqual(matrix[0][0], 1, delta=.001)
        finally:
            bpy.context.scene.remix_anchor_object_target = None
            del bpy.context.scene['remix_import_unit_scale']
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.lights.remove(data)
            bpy.data.objects.remove(anchor, do_unlink=True)

    def test_light_roundtrip(self):
        for axis in (UsdGeom.Tokens.z, UsdGeom.Tokens.y):
            for scale in (.01, 1.0):
                for kind in ('SphereLight', 'RectLight', 'DistantLight', 'Spot'):
                    with self.subTest(axis=axis, scale=scale, kind=kind):
                        stage = Usd.Stage.CreateInMemory()
                        UsdGeom.SetStageUpAxis(stage, axis)
                        name = 'SphereLight' if kind == 'Spot' else kind
                        prim = stage.DefinePrim('/World/light_12345678', name)
                        xform = UsdGeom.Xformable(prim)
                        xform.AddTranslateOp().Set(Gf.Vec3d(100, 50, 20))
                        xform.AddRotateXYZOp().Set(Gf.Vec3f(10, 20, 30))
                        prim.CreateAttribute('inputs:intensity', Sdf.ValueTypeNames.Float).Set(100)
                        if name == 'SphereLight':
                            prim.CreateAttribute('inputs:radius', Sdf.ValueTypeNames.Float).Set(1)
                        if kind == 'RectLight':
                            prim.CreateAttribute('inputs:width', Sdf.ValueTypeNames.Float).Set(2)
                            prim.CreateAttribute('inputs:height', Sdf.ValueTypeNames.Float).Set(3)
                        if kind == 'Spot':
                            UsdLux.ShapingAPI.Apply(prim)
                            prim.CreateAttribute('inputs:shaping:cone:angle', Sdf.ValueTypeNames.Float).Set(30)
                        if kind == 'DistantLight':
                            prim.CreateAttribute('inputs:angle', Sdf.ValueTypeNames.Float).Set(2)
                        obj = create_light_from_usd(prim, stage, scale)
                        self.assertIsNotNone(obj)
                        bpy.context.scene.collection.objects.link(obj)
                        bpy.context.view_layer.update()
                        bpy.context.scene.remix_export_scale = scale
                        out = Usd.Stage.CreateInMemory()
                        UsdGeom.SetStageUpAxis(out, axis)
                        reporter = type('Reporter', (), {'report': lambda *args: None})()
                        try:
                            with patch('rtx_remix_importer.operators.export_operator.get_game_stage', return_value=None):
                                self.assertTrue(export_light(reporter, bpy.context, obj, out, '', ''))
                            exported = out.GetPrimAtPath(obj['usd_light_path'])
                            transform = UsdGeom.Xformable(exported).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                            original = xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                            for i in range(4):
                                for j in range(4):
                                    self.assertAlmostEqual(transform[i][j], original[i][j], delta=.002)
                            self.assertAlmostEqual(exported.GetAttribute('inputs:intensity').Get(), 100, delta=.02)
                            attr = {'SphereLight': 'radius', 'Spot': 'radius', 'RectLight': 'width', 'DistantLight': 'angle'}[kind]
                            expected = {'SphereLight': 1, 'Spot': 1, 'RectLight': 2, 'DistantLight': 2}[kind]
                            self.assertAlmostEqual(exported.GetAttribute('inputs:' + attr).Get(), expected, delta=.001)
                            if kind == 'Spot':
                                self.assertAlmostEqual(exported.GetAttribute('inputs:shaping:cone:angle').Get(), 30, delta=.001)
                        finally:
                            data = obj.data
                            bpy.data.objects.remove(obj, do_unlink=True)
                            bpy.data.lights.remove(data)
