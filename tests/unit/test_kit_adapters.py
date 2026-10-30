"""Tests for the narrow Kit adapters used by the direct backend."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pxr import Sdf, Usd, UsdShade

from rtx_remix_importer.compatibility import kit_adapters

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "full_project"
CAPTURE = DATA_DIR / "capture.usda"


class TestNormalizeUrl(unittest.TestCase):
    def test_backslashes_become_slashes(self):
        self.assertEqual(kit_adapters.normalize_url(r"C:\games\remix\capture.usda"), "C:/games/remix/capture.usda")

    def test_dot_segments_collapse(self):
        self.assertEqual(kit_adapters.normalize_url("C:/games/./remix/../capture.usda"), "C:/games/capture.usda")

    def test_scheme_is_preserved(self):
        self.assertEqual(kit_adapters.normalize_url("file:///C:/games//remix/capture.usda"), "file:///C:/games/remix/capture.usda")

    def test_empty_input(self):
        self.assertEqual(kit_adapters.normalize_url(""), "")


class TestLightTypes(unittest.TestCase):
    def test_known_types(self):
        self.assertEqual(kit_adapters.get_light_type("SphereLight"), kit_adapters.LightTypes.SphereLight)
        self.assertEqual(kit_adapters.get_light_type("DomeLight_1"), kit_adapters.LightTypes.DomeLight)

    def test_unknown_and_none(self):
        self.assertIsNone(kit_adapters.get_light_type("Xform"))
        self.assertIsNone(kit_adapters.get_light_type(None))


class TestComposedReferences(unittest.TestCase):
    def test_internal_reference_reports_authoring_layer(self):
        stage = Usd.Stage.Open(str(CAPTURE))
        instance = stage.GetPrimAtPath("/RootNode/instances/inst_6CA2F12444DEBE09_0")
        refs = kit_adapters.get_composed_references_from_prim(instance)
        self.assertEqual(len(refs), 1)
        reference, layer = refs[0]
        self.assertEqual(str(reference.primPath), "/RootNode/meshes/mesh_6CA2F12444DEBE09")
        self.assertEqual(reference.assetPath, "")
        self.assertTrue(layer.identifier.endswith("capture.usda"))

    def test_file_reference_reports_root_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_path = os.path.join(tmp, "root.usda")
            stage = Usd.Stage.CreateNew(root_path)
            ref_prim = stage.DefinePrim("/Ref", "Xform")
            ref_prim.GetReferences().AddReference(str(CAPTURE), "/RootNode")
            stage.Save()
            stage = Usd.Stage.Open(root_path)

            refs = kit_adapters.get_composed_references_from_prim(stage.GetPrimAtPath("/Ref"))
            self.assertEqual(len(refs), 1)
            reference, layer = refs[0]
            self.assertTrue(reference.assetPath.endswith("capture.usda"))
            self.assertEqual(layer.identifier, stage.GetRootLayer().identifier)

    def test_invalid_prim_returns_empty(self):
        self.assertEqual(kit_adapters.get_composed_references_from_prim(Usd.Prim()), [])


class TestIntroducingLayer(unittest.TestCase):
    def test_local_definition(self):
        stage = Usd.Stage.Open(str(CAPTURE))
        layer, path = kit_adapters.get_introducing_layer(stage.GetPrimAtPath("/RootNode/Camera"))
        self.assertEqual(str(path), "/RootNode/Camera")
        self.assertTrue(layer.identifier.endswith("capture.usda"))

    def test_prim_from_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_path = os.path.join(tmp, "root.usda")
            stage = Usd.Stage.CreateNew(root_path)
            stage.DefinePrim("/Ref", "Xform").GetReferences().AddReference(
                str(CAPTURE), "/RootNode"
            )
            stage.Save()
            stage = Usd.Stage.Open(root_path)
            layer, path = kit_adapters.get_introducing_layer(
                stage.GetPrimAtPath("/Ref/lights/light_9907D0B07D040077")
            )
            self.assertIsNotNone(layer)
            self.assertTrue(layer.identifier.endswith("capture.usda"))
            self.assertEqual(str(path), "/RootNode/lights/light_9907D0B07D040077")


class TestShaderFromMaterial(unittest.TestCase):
    def test_returns_first_shader(self):
        stage = Usd.Stage.Open(str(CAPTURE))
        material = UsdShade.Material(stage.GetPrimAtPath("/RootNode/Looks/mat_8D1946B4993CE5A3"))
        shader = kit_adapters.get_shader_from_material(material)
        self.assertIsNotNone(shader)
        self.assertTrue(str(shader.GetPath()).endswith("/Shader"))

    def test_material_without_shader(self):
        stage = Usd.Stage.Open(str(CAPTURE))
        material = UsdShade.Material(stage.GetPrimAtPath("/RootNode"))
        self.assertIsNone(kit_adapters.get_shader_from_material(material))


if __name__ == "__main__":
    unittest.main()
