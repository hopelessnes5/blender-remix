"""Sublayer insertion must preserve composition strength and reject cycles."""
import os
import tempfile
import unittest

from pxr import Sdf, Usd
from rtx_remix_importer.ui.operators.project_ops import _insert_sublayer


class TestSublayerOrdering(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def stage(self, name, kind='replacement'):
        stage = Usd.Stage.CreateNew(os.path.join(self.root, name))
        stage.GetRootLayer().customLayerData = {'lightspeed_layer_type': kind}
        return stage

    def test_new_replacement_is_stronger_than_capture(self):
        capture = self.stage('capture.usda', 'capture')
        capture.OverridePrim('/Material').CreateAttribute('roughness', Sdf.ValueTypeNames.Float).Set(.8)
        capture.GetRootLayer().Save()
        replacement = self.stage('replacement.usda')
        replacement.OverridePrim('/Material').CreateAttribute('roughness', Sdf.ValueTypeNames.Float).Set(.2)
        replacement.GetRootLayer().Save()
        parent = self.stage('mod.usda')
        parent.GetRootLayer().subLayerPaths.append('./capture.usda')
        self.assertTrue(_insert_sublayer(parent.GetRootLayer(), replacement.GetRootLayer().realPath))
        self.assertEqual(list(parent.GetRootLayer().subLayerPaths), ['./replacement.usda', './capture.usda'])
        self.assertAlmostEqual(parent.GetPrimAtPath('/Material').GetAttribute('roughness').Get(), .2, places=5)
        self.assertFalse(_insert_sublayer(parent.GetRootLayer(), replacement.GetRootLayer().realPath))

    def test_self_and_indirect_cycle_rejected_without_modification(self):
        parent = self.stage('parent.usda')
        child = self.stage('child.usda')
        child.GetRootLayer().subLayerPaths.append('./parent.usda')
        child.GetRootLayer().Save()
        for path in (parent.GetRootLayer().realPath, child.GetRootLayer().realPath):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'cycle'):
                _insert_sublayer(parent.GetRootLayer(), path)
            self.assertEqual(list(parent.GetRootLayer().subLayerPaths), [])

    def test_nested_capture_branch_stays_weaker(self):
        capture = self.stage('capture.usda', 'capture')
        capture.GetRootLayer().Save()
        branch = self.stage('branch.usda')
        branch.GetRootLayer().subLayerPaths.append('./capture.usda')
        branch.GetRootLayer().Save()
        replacement = self.stage('replacement.usda')
        replacement.GetRootLayer().Save()
        parent = self.stage('mod.usda')
        parent.GetRootLayer().subLayerPaths.append('./branch.usda')
        _insert_sublayer(parent.GetRootLayer(), replacement.GetRootLayer().realPath)
        self.assertEqual(list(parent.GetRootLayer().subLayerPaths), ['./replacement.usda', './branch.usda'])

    def test_operator_reports_cycle_and_does_not_edit_layer(self):
        from types import SimpleNamespace
        from rtx_remix_importer.ui.operators.project_ops import AddRemixSublayer
        parent = self.stage('mod.usda')
        parent.GetRootLayer().Save()
        with open(parent.GetRootLayer().realPath, 'rb') as stream:
            before = stream.read()
        messages = []
        operator = SimpleNamespace(filepath=parent.GetRootLayer().realPath,
                                   report=lambda level, message: messages.append(message))
        context = SimpleNamespace(scene=SimpleNamespace(remix_mod_file_path=parent.GetRootLayer().realPath,
                                                        remix_active_sublayer_path=''))
        self.assertEqual(AddRemixSublayer.execute(operator, context), {'CANCELLED'})
        self.assertTrue(any('cycle' in message for message in messages))
        with open(parent.GetRootLayer().realPath, 'rb') as stream:
            self.assertEqual(stream.read(), before)
