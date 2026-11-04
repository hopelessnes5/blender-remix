"""USD/Blender light and camera unit and basis conversion regressions."""
import contextlib
import io
import math
import unittest
from unittest.mock import patch

import bpy
import mathutils
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux
from rtx_remix_importer.light_utils import create_light_from_usd
from rtx_remix_importer.blender_bridge.scene_builder import _create_camera
from rtx_remix_importer.operators.export_operator import export_light


class RecordingReporter:
    def __init__(self):
        self.reports = []

    def report(self, level, message):
        self.reports.append((level, message))


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

    def test_parented_light_export_bakes_world_transform(self):
        """A rigid parent chain (translate, rotate, uniform scale) roundtrips."""
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        parent = stage.DefinePrim('/World/ScaledParent', 'Xform')
        xform = UsdGeom.Xformable(parent)
        xform.AddTranslateOp().Set(Gf.Vec3d(40, 0, 0))
        xform.AddRotateXYZOp().Set(Gf.Vec3f(0, 0, 90))
        xform.AddScaleOp().Set(Gf.Vec3f(0.5, 0.5, 0.5))
        prim = UsdLux.SphereLight.Define(stage, '/World/ScaledParent/light_12345678').GetPrim()
        prim_xform = UsdGeom.Xformable(prim)
        prim_xform.AddTranslateOp().Set(Gf.Vec3d(20, 5, 0))
        prim_xform.AddRotateXYZOp().Set(Gf.Vec3f(10, 20, 30))
        obj = create_light_from_usd(prim, stage, 1.0)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        bpy.context.scene.remix_export_scale = 1.0
        out = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(out, UsdGeom.Tokens.z)
        reporter = RecordingReporter()
        try:
            with patch('rtx_remix_importer.operators.export_operator.get_game_stage', return_value=None):
                self.assertTrue(export_light(reporter, bpy.context, obj, out, '', ''))
            original = prim_xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            exported = out.GetPrimAtPath(obj['usd_light_path'])
            transform = UsdGeom.Xformable(exported).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            for i in range(3):
                self.assertAlmostEqual(transform[3][i], original[3][i], delta=.002)

            def directions(matrix):
                dirs = []
                for j in range(3):
                    column = [matrix[i][j] for i in range(3)]
                    length = math.sqrt(sum(c * c for c in column))
                    dirs.append([c / length for c in column])
                return dirs

            got, want = directions(transform), directions(original)
            for columns in range(len(got)):
                for row in range(3):
                    self.assertAlmostEqual(got[columns][row], want[columns][row], delta=.002)
        finally:
            bpy.context.scene.remix_anchor_object_target = None
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.lights.remove(data)

    def test_export_light_fails_closed_on_shear(self):
        """A sheared parent chain is refused instead of silently misplaced."""
        parent = bpy.data.objects.new('ShearParent', None)
        parent.scale = (1.0, 2.0, 1.0)
        bpy.context.scene.collection.objects.link(parent)
        light_data = bpy.data.lights.new('ShearedLight', 'POINT')
        obj = bpy.data.objects.new('ShearedLight', light_data)
        obj.rotation_euler = (0, 0, math.radians(45))
        bpy.context.scene.collection.objects.link(obj)
        obj.parent = parent
        obj.matrix_parent_inverse = mathutils.Matrix.Identity(4)
        bpy.context.view_layer.update()
        bpy.context.scene.remix_export_scale = 1.0
        out = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(out, UsdGeom.Tokens.z)
        reporter = RecordingReporter()
        try:
            with patch('rtx_remix_importer.operators.export_operator.get_game_stage', return_value=None):
                self.assertFalse(export_light(reporter, bpy.context, obj, out, '', ''))
            self.assertTrue(any('Cannot export light' in text for _, text in reporter.reports))
            self.assertTrue(any(level == {'ERROR'} for level, _ in reporter.reports))
            self.assertEqual(list(out.Traverse()), [])
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.lights.remove(light_data)
            bpy.data.objects.remove(parent, do_unlink=True)

    def test_import_light_warns_on_sheared_chain(self):
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        parent = stage.DefinePrim('/World/ShearFrame', 'Xform')
        xform = UsdGeom.Xformable(parent)
        xform.AddTranslateOp().Set(Gf.Vec3d(10, 0, 0))
        xform.AddRotateXYZOp().Set(Gf.Vec3f(0, 0, 30))
        xform.AddScaleOp().Set(Gf.Vec3f(1, 2, 1))
        prim = UsdLux.SphereLight.Define(stage, '/World/ShearFrame/light_12345678').GetPrim()
        prim_xform = UsdGeom.Xformable(prim)
        prim_xform.AddTranslateOp().Set(Gf.Vec3d(5, 0, 0))
        prim_xform.AddRotateXYZOp().Set(Gf.Vec3f(0, 0, 45))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            obj = create_light_from_usd(prim, stage, 1.0)
        self.assertIsNotNone(obj)
        self.assertIn('contains shear', stdout.getvalue())
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        try:
            translation = obj.matrix_world.to_translation()
            for got, expected in zip(translation, (10 + 5 * math.cos(math.radians(30)), 5 * math.sin(math.radians(30)), 0.0)):
                self.assertAlmostEqual(got, expected, places=5)
        finally:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.lights.remove(data)

    def test_camera_import_keeps_parented_world_matrix(self):
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        frame = stage.DefinePrim('/World/Frame', 'Xform')
        xform = UsdGeom.Xformable(frame)
        xform.AddTranslateOp().Set(Gf.Vec3d(10, 0, 0))
        xform.AddRotateXYZOp().Set(Gf.Vec3f(0, 0, 90))
        xform.AddScaleOp().Set(Gf.Vec3f(0.5, 0.5, 0.5))
        UsdGeom.Camera.Define(stage, '/World/Frame/camera_00000000')
        composition = type('Composition', (), {'up_axis_is_y': False, 'path': 'capture.usda'})()
        cam_prim = stage.GetPrimAtPath('/World/Frame/camera_00000000')
        obj = _create_camera(bpy.context, composition, cam_prim, 1.0)
        try:
            original = xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            translation = obj.matrix_world.to_translation()
            for i in range(3):
                self.assertAlmostEqual(translation[i], original[3][i], delta=.002)
            # Camera aim (-Z of the composed frame) survives the placement.
            got_direction = obj.matrix_world @ mathutils.Vector((0, 0, -1.0))
            want_matrix = mathutils.Matrix(tuple(
                tuple(float(c) for c in row) for row in original.GetTranspose()
            ))
            want_direction = want_matrix @ mathutils.Vector((0, 0, -1.0))
            for got, want in zip(got_direction, want_direction):
                self.assertAlmostEqual(got, want, delta=.002)
        finally:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.cameras.remove(data)

    def test_area_approximations_pin_cylinder_and_ellipse(self):
        """CylinderLight imports as Rectangle; ELLIPSE exports as a disk."""
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        prim = UsdLux.CylinderLight.Define(stage, '/World/light_cylinder').GetPrim()
        prim.CreateAttribute('inputs:radius', Sdf.ValueTypeNames.Float).Set(2)
        prim.CreateAttribute('inputs:length', Sdf.ValueTypeNames.Float).Set(4)
        obj = create_light_from_usd(prim, stage, 1.0)
        try:
            self.assertEqual(obj.data.type, 'AREA')
            self.assertEqual(obj.data.shape, 'RECTANGLE')
            self.assertAlmostEqual(obj.data.size, 4, delta=.001)
            self.assertAlmostEqual(obj.data.size_y, 4, delta=.001)
        finally:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.lights.remove(data)

        light_data = bpy.data.lights.new('EllipseArea', 'AREA')
        light_data.shape = 'ELLIPSE'
        light_data.size = 3
        light_data.size_y = 1.4
        obj = bpy.data.objects.new('EllipseArea', light_data)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        bpy.context.scene.remix_export_scale = 1.0
        out = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(out, UsdGeom.Tokens.z)
        reporter = RecordingReporter()
        round_light = None
        try:
            with patch('rtx_remix_importer.operators.export_operator.get_game_stage', return_value=None):
                self.assertTrue(export_light(reporter, bpy.context, obj, out, '', ''))
            self.assertTrue(any('ELLIPSE' in text and 'WARNING' in str(level)
                                for level, text in reporter.reports))
            exported = out.GetPrimAtPath(obj['usd_light_path'])
            self.assertEqual(exported.GetTypeName(), 'DiskLight')
            self.assertAlmostEqual(exported.GetAttribute('inputs:radius').Get(), 1.5, delta=.001)
            round_light = create_light_from_usd(exported, out, 1.0)
            self.assertEqual(round_light.data.type, 'AREA')
            self.assertEqual(round_light.data.shape, 'DISK')
            self.assertAlmostEqual(round_light.data.size, 3, delta=.001)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.lights.remove(light_data)
            if round_light is not None:
                data = round_light.data
                bpy.data.objects.remove(round_light, do_unlink=True)
                bpy.data.lights.remove(data)
