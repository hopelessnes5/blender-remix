"""Tests for project composition and capture locking."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from pxr import Sdf, Tf, Usd, UsdGeom

from rtx_remix_importer.nvidia_core import composition, constants

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "full_project"
CAPTURE = DATA_DIR / "capture.usda"
FULL_PROJECT = DATA_DIR / "full_project.usda"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class TestCaptureOnly(unittest.TestCase):
    def test_capture_only_project(self):
        project = composition.ProjectComposition.open(str(CAPTURE))
        try:
            self.assertTrue(project.is_capture_only)
            self.assertIsNotNone(project.capture_layer)
            self.assertIsNone(project.mod_layer)
            self.assertTrue((not project.capture_layer.permissionToEdit))
            self.assertTrue(project.up_axis_is_y)
            self.assertAlmostEqual(project.meters_per_unit, 0.01)
            self.assertEqual(Path(project.capture_directory), DATA_DIR)
        finally:
            project.close()

    def test_closed_project_raises(self):
        project = composition.ProjectComposition.open(str(CAPTURE))
        project.close()
        self.assertFalse(project.is_open)
        with self.assertRaises(RuntimeError):
            _ = project.stage


class TestComposedProject(unittest.TestCase):
    def test_mod_layer_is_edit_target(self):
        project = composition.ProjectComposition.open(str(FULL_PROJECT))
        try:
            self.assertFalse(project.is_capture_only)
            self.assertIsNotNone(project.capture_layer)
            self.assertIsNotNone(project.mod_layer)
            self.assertTrue((not project.capture_layer.permissionToEdit))
            self.assertTrue(project.mod_layer.identifier.endswith("mod.usda"))
            self.assertEqual(
                project.stage.GetEditTarget().GetLayer().identifier,
                project.mod_layer.identifier,
            )
            self.assertEqual(
                project.mod_layer.customLayerData[constants.LAYER_TYPE_KEY],
                constants.LAYER_TYPE_REPLACEMENT,
            )
            # Capture metadata wins over the (older) project root metadata.
            self.assertTrue(project.up_axis_is_y)
            self.assertAlmostEqual(project.meters_per_unit, 0.01)
        finally:
            project.close()

    def test_mod_layer_prefers_replacement_over_workfile(self):
        stage = Usd.Stage.Open(str(FULL_PROJECT))
        layer = composition.find_mod_layer(stage)
        self.assertIsNotNone(layer)
        self.assertEqual(layer.customLayerData[constants.LAYER_TYPE_KEY], constants.LAYER_TYPE_REPLACEMENT)

    def test_omni_layer_muteness_is_applied(self):
        project = composition.ProjectComposition.open(str(FULL_PROJECT))
        try:
            muted = {Path(identifier).name for identifier in project.muted_layers}
            self.assertEqual(muted, {"mod_capture_baker.usda", "sublayer_child_01.usda"})
            for identifier in project.muted_layers:
                self.assertTrue(project.stage.IsLayerMuted(identifier))

            # sublayer_child_01 would override the texture if it composed.
            shader = project.stage.GetPrimAtPath(
                "/RootNode/Looks/mat_8D1946B4993CE5A3/Shader"
            )
            texture = shader.GetAttribute("inputs:diffuse_texture").Get()
            self.assertTrue(str(texture.path).endswith("8D1946B4993CE5A3.dds"))

            # The mod edit target is never muted.
            self.assertNotIn(Path(project.mod_layer.identifier).name, muted)
            self.assertFalse(project.stage.IsLayerMuted(project.mod_layer.identifier))
        finally:
            project.close()

    def test_omni_layer_locked_layers_are_read_only(self):
        project = composition.ProjectComposition.open(str(FULL_PROJECT))
        try:
            locked = {Path(layer.identifier).name for layer in project.locked_layers}
            self.assertEqual(locked, {"capture.usda", "sublayer_child_02.usda"})
            for layer in project.locked_layers:
                self.assertFalse(layer.permissionToEdit)
            self.assertTrue(project.mod_layer.permissionToEdit)
        finally:
            project.close()


class TestAddCaptureSublayer(unittest.TestCase):
    def test_capture_is_appended_as_weakest_sublayer(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            shutil.copytree(DATA_DIR, target)
            project = composition.ProjectComposition.open(str(target / "full_project.usda"))
            try:
                capture_layer = project.add_capture_sublayer(str(target / "capture.usda"))
                self.assertIsNotNone(capture_layer)
                self.assertTrue((not capture_layer.permissionToEdit))
                sublayers = list(project.stage.GetRootLayer().subLayerPaths)
                self.assertEqual(sublayers.count("./capture.usda"), 1)
                self.assertEqual(sublayers[-1], "./capture.usda")
                # The replacement layer stays the edit target.
                self.assertEqual(
                    project.stage.GetEditTarget().GetLayer().identifier,
                    project.mod_layer.identifier,
                )
                # Capture metadata is republished on the project root.
                self.assertEqual(UsdGeom.GetStageUpAxis(project.stage), UsdGeom.Tokens.y)
                self.assertAlmostEqual(UsdGeom.GetStageMetersPerUnit(project.stage), 0.01)
            finally:
                project.close()


class TestCaptureReadOnly(unittest.TestCase):
    def test_authoring_into_capture_is_rejected(self):
        project = composition.ProjectComposition.open(str(CAPTURE))
        try:
            with self.assertRaises(Tf.ErrorException):
                Sdf.CreatePrimInLayer(project.capture_layer, "/RootNode/Forbidden")
        finally:
            project.close()

    def test_save_writes_only_the_mod_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            shutil.copytree(DATA_DIR, target)
            capture_path = target / "capture.usda"
            mod_path = target / "mod.usda"
            capture_before = _sha256(capture_path)
            mod_before = _sha256(mod_path)

            project = composition.ProjectComposition.open(str(target / "full_project.usda"))
            try:
                project.stage.DefinePrim("/RootNode/SavedFromTest", "Xform")
                project.save()
            finally:
                project.close()

            self.assertEqual(_sha256(capture_path), capture_before)
            self.assertNotEqual(_sha256(mod_path), mod_before)
            self.assertIn("SavedFromTest", mod_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
