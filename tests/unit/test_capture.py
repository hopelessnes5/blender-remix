"""Tests for the ported capture helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pxr import Sdf, Usd

from rtx_remix_importer.nvidia_core import capture, constants

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "full_project"
CAPTURE = DATA_DIR / "capture.usda"
FULL_PROJECT = DATA_DIR / "full_project.usda"
MOD = DATA_DIR / "mod.usda"


class TestCaptureDetection(unittest.TestCase):
    def test_capture_file_is_detected(self):
        self.assertTrue(capture.is_capture_file(str(CAPTURE)))

    def test_workfile_and_replacement_are_not_captures(self):
        self.assertFalse(capture.is_capture_file(str(MOD)))
        self.assertFalse(capture.is_capture_file(str(FULL_PROJECT)))

    def test_missing_file_is_not_a_capture(self):
        self.assertFalse(capture.is_capture_file(str(DATA_DIR / "does_not_exist.usda")))

    def test_layer_metadata(self):
        metadata = capture.get_layer_metadata(str(CAPTURE))
        self.assertEqual(metadata[constants.LAYER_TYPE_KEY], constants.LAYER_TYPE_CAPTURE)
        self.assertIn("Portal with RTX", metadata[constants.LAYER_GAME_NAME_KEY])


class TestCaptureLayerLookup(unittest.TestCase):
    def test_capture_only_stage(self):
        stage = Usd.Stage.Open(str(CAPTURE))
        layer = capture.get_capture_layer(stage)
        self.assertIsNotNone(layer)
        self.assertTrue(layer.identifier.endswith("capture.usda"))

    def test_composed_project(self):
        stage = Usd.Stage.Open(str(FULL_PROJECT))
        layer = capture.get_capture_layer(stage)
        self.assertIsNotNone(layer)
        self.assertTrue(layer.identifier.endswith("capture.usda"))

    def test_stage_without_capture(self):
        stage = Usd.Stage.Open(str(FULL_PROJECT))
        stage.GetRootLayer().subLayerPaths.clear()
        self.assertIsNone(capture.get_capture_layer(stage))


class TestCaptureHashes(unittest.TestCase):
    def test_hashes_group_material_to_mesh(self):
        layer = Sdf.Layer.FindOrOpen(str(CAPTURE))
        hashes, grouped = capture.get_hashes_from_capture_layer(layer)
        self.assertIn("6CA2F12444DEBE09", hashes)
        self.assertIn("8D1946B4993CE5A3", hashes)
        self.assertIn("9907D0B07D040077", hashes)
        self.assertEqual(grouped["8D1946B4993CE5A3"], {"6CA2F12444DEBE09"})
        self.assertEqual(str(hashes["6CA2F12444DEBE09"]), "/RootNode/meshes/mesh_6CA2F12444DEBE09")

    def test_non_capture_layer_returns_empty(self):
        layer = Sdf.Layer.FindOrOpen(str(MOD))
        hashes, grouped = capture.get_captured_hashes(layer)
        self.assertEqual(hashes, {})
        self.assertEqual(grouped, {})


class TestRemixConfig(unittest.TestCase):
    def _stage_with_legacy_config(self):
        layer = Sdf.Layer.CreateAnonymous("project.usda")
        stage = Usd.Stage.Open(layer)
        prim = stage.DefinePrim(constants.CAPTURED_REMIX_SETTINGS, "RenderSettings")
        prim.CreateAttribute(constants.CAPTURED_REMIX_CONFIG_LEGACY_ATTR, Sdf.ValueTypeNames.String).Set(
            "rtx { sceneScale = 2 }"
        )
        return stage

    def test_legacy_config_is_published_to_session_layer(self):
        stage = self._stage_with_legacy_config()
        capture.publish_remix_config(stage)

        attribute = stage.GetPrimAtPath(constants.CAPTURED_REMIX_SETTINGS).GetAttribute(
            constants.CAPTURED_REMIX_CONFIG_ATTR
        )
        self.assertTrue(attribute.IsValid())
        self.assertEqual(attribute.Get(), "rtx { sceneScale = 2 }")
        session_spec = stage.GetSessionLayer().GetPrimAtPath(constants.CAPTURED_REMIX_SETTINGS)
        self.assertIsNotNone(session_spec)
        self.assertIn(constants.CAPTURED_REMIX_CONFIG_ATTR, session_spec.properties)

    def test_namespaced_config_wins(self):
        stage = self._stage_with_legacy_config()
        attribute = stage.GetPrimAtPath(constants.CAPTURED_REMIX_SETTINGS).CreateAttribute(
            constants.CAPTURED_REMIX_CONFIG_ATTR, Sdf.ValueTypeNames.String
        )
        attribute.Set("rtx { sceneScale = 1 }")
        capture.publish_remix_config(stage)
        session_spec = stage.GetSessionLayer().GetPrimAtPath(constants.CAPTURED_REMIX_SETTINGS)
        if session_spec is not None:
            self.assertNotIn(constants.CAPTURED_REMIX_CONFIG_ATTR, session_spec.properties)

    def test_empty_prim_is_ignored(self):
        stage = Usd.Stage.Open(Sdf.Layer.CreateAnonymous("empty.usda"))
        capture.publish_remix_config(stage)
        self.assertIsNone(stage.GetSessionLayer().GetPrimAtPath(constants.CAPTURED_REMIX_SETTINGS))


class TestStageMetadataCopy(unittest.TestCase):
    def test_metadata_is_copied(self):
        source = Usd.Stage.Open(str(CAPTURE))
        destination = Usd.Stage.Open(Sdf.Layer.CreateAnonymous("project.usda"))
        capture.copy_capture_stage_metadata(source, destination)
        from pxr import UsdGeom

        self.assertEqual(UsdGeom.GetStageUpAxis(destination), UsdGeom.Tokens.y)
        self.assertAlmostEqual(UsdGeom.GetStageMetersPerUnit(destination), 0.01)
        self.assertEqual(destination.GetTimeCodesPerSecond(), source.GetTimeCodesPerSecond())


if __name__ == "__main__":
    unittest.main()
