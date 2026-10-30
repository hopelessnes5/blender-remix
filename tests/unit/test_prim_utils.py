"""Tests for the ported ``prim_utils`` helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pxr import Sdf, Usd, UsdGeom

from rtx_remix_importer.nvidia_core import prim_utils

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "full_project"
CAPTURE = DATA_DIR / "capture.usda"

INSTANCE_PATH = "/RootNode/instances/inst_6CA2F12444DEBE09_0"
PROTOTYPE_PATH = "/RootNode/meshes/mesh_6CA2F12444DEBE09"


class TestClassification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage = Usd.Stage.Open(str(CAPTURE))

    def test_instance_children(self):
        self.assertTrue(prim_utils.is_instance(self.stage.GetPrimAtPath(INSTANCE_PATH + "/mesh")))

    def test_instance_root_is_not_an_instance_child(self):
        # The Toolkit regex matches prims *inside* an instance path, not the
        # instance root itself; keep that behaviour byte-for-byte.
        self.assertFalse(prim_utils.is_instance(self.stage.GetPrimAtPath(INSTANCE_PATH)))

    def test_mesh_prototype(self):
        self.assertTrue(prim_utils.is_mesh_prototype(self.stage.GetPrimAtPath(PROTOTYPE_PATH + "/mesh")))
        self.assertFalse(prim_utils.is_mesh_prototype(self.stage.GetPrimAtPath(PROTOTYPE_PATH)))

    def test_mesh_asset(self):
        self.assertTrue(prim_utils.is_mesh_asset(self.stage.GetPrimAtPath(PROTOTYPE_PATH)))
        self.assertFalse(prim_utils.is_mesh_asset(self.stage.GetPrimAtPath(PROTOTYPE_PATH + "/mesh")))

    def test_light_prototype(self):
        self.assertTrue(prim_utils.is_light(self.stage.GetPrimAtPath("/RootNode/lights/light_9907D0B07D040077")))
        self.assertTrue(prim_utils.is_light_prototype(self.stage.GetPrimAtPath("/RootNode/lights/light_9907D0B07D040077")))

    def test_material_prototype(self):
        self.assertTrue(prim_utils.is_material_prototype(self.stage.GetPrimAtPath("/RootNode/Looks/mat_8D1946B4993CE5A3")))

    def test_empty_mesh_prim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "root.usda")
            stage = Usd.Stage.CreateNew(root)
            empty = stage.DefinePrim(PROTOTYPE_PATH, "Xform")
            self.assertTrue(prim_utils.is_empty_mesh_prim(empty))
            stage.DefinePrim(PROTOTYPE_PATH + "/mesh", "Mesh")
            self.assertFalse(prim_utils.is_empty_mesh_prim(stage.GetPrimAtPath(PROTOTYPE_PATH)))


class TestPrototypeLookup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage = Usd.Stage.Open(str(CAPTURE))

    def test_instance_maps_to_prototype(self):
        prim = prim_utils.get_prototype(self.stage.GetPrimAtPath(INSTANCE_PATH))
        self.assertIsNotNone(prim)
        self.assertEqual(str(prim.GetPath()), PROTOTYPE_PATH)

    def test_prototype_is_idempotent(self):
        prim = self.stage.GetPrimAtPath(PROTOTYPE_PATH)
        self.assertEqual(prim_utils.get_prototype(prim), prim)

    def test_missing_prototype_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "root.usda")
            stage = Usd.Stage.CreateNew(root)
            stage.DefinePrim("/RootNode/instances/inst_0123456789ABCDEF_0", "Xform")
            self.assertIsNone(
                prim_utils.get_prototype(stage.GetPrimAtPath("/RootNode/instances/inst_0123456789ABCDEF_0"))
            )


class TestPrimPaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage = Usd.Stage.Open(str(CAPTURE))

    def test_models_only_returns_mesh_prims(self):
        paths = prim_utils.get_prim_paths(self.stage, prim_type=prim_utils.PrimTypes.MODELS)
        self.assertEqual(paths, [PROTOTYPE_PATH + "/mesh"])

    def test_hash_filter(self):
        paths = prim_utils.get_prim_paths(
            self.stage, {"6CA2F12444DEBE09"}, prim_type=prim_utils.PrimTypes.MODELS
        )
        self.assertEqual(paths, [PROTOTYPE_PATH + "/mesh"])
        self.assertEqual(
            prim_utils.get_prim_paths(self.stage, {"FFFFFFFFFFFFFFFF"}, prim_type=prim_utils.PrimTypes.MODELS),
            [],
        )


class TestReferences(unittest.TestCase):
    def test_reference_file_paths_and_find(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_path = os.path.join(tmp, "root.usda")
            stage = Usd.Stage.CreateNew(root_path)
            stage.DefinePrim("/Ref", "Xform").GetReferences().AddReference(
                str(CAPTURE), "/RootNode"
            )
            stage.Save()
            stage = Usd.Stage.Open(root_path)

            prim = stage.GetPrimAtPath("/Ref/lights")
            owner, items = prim_utils.find_prim_with_references(prim)
            self.assertEqual(str(owner.GetPath()), "/Ref")
            self.assertEqual(len(items), 1)
            _, reference, layer, index = items[0]
            self.assertEqual(index, 0)
            self.assertTrue(reference.assetPath.endswith("capture.usda"))
            self.assertEqual(layer.identifier, stage.GetRootLayer().identifier)

    def test_has_replacement_ref_edits(self):
        stage = Usd.Stage.Open(str(CAPTURE))
        prim = stage.GetPrimAtPath(PROTOTYPE_PATH)
        layer = Sdf.Layer.CreateAnonymous("replacement.usda")
        self.assertFalse(prim_utils.has_replacement_ref_edits(prim, {layer}))

        spec = Sdf.CreatePrimInLayer(layer, PROTOTYPE_PATH)
        spec.specifier = Sdf.SpecifierOver
        spec.referenceList.ClearEditsAndMakeExplicit()
        self.assertTrue(prim_utils.has_replacement_ref_edits(prim, {layer}))

    def test_get_reference_file_paths_skips_empty_asset_paths(self):
        stage = Usd.Stage.Open(str(CAPTURE))
        items, count = prim_utils.get_reference_file_paths(stage.GetPrimAtPath(INSTANCE_PATH))
        self.assertEqual(items, [])
        self.assertEqual(count, 0)


class TestGhostAndTraversal(unittest.TestCase):
    def test_ghost_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "root.usda")
            stage = Usd.Stage.CreateNew(root)
            ghost = stage.DefinePrim("/RootNode/instances/inst_0123456789ABCDEF_0/child")
            self.assertFalse(ghost.GetTypeName())
            self.assertTrue(prim_utils.is_ghost_prim(ghost))

    def test_children_can_skip_remix_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "root.usda")
            stage = Usd.Stage.CreateNew(root)
            parent = stage.DefinePrim("/P", "Xform")
            ref_child = stage.DefinePrim("/P/ref", "Xform")
            plain_child = stage.DefinePrim("/P/plain", "Xform")
            ref_child.CreateAttribute("IsRemixRef", Sdf.ValueTypeNames.Int).Set(1)

            children = prim_utils.get_children_prims(parent, skip_remix_ref=True)
            self.assertEqual([str(child.GetPath()) for child in children], ["/P/plain"])
            children = prim_utils.get_children_prims(parent)
            self.assertEqual(
                sorted(str(child.GetPath()) for child in children), ["/P/plain", "/P/ref"]
            )
            # ``level=1`` stops after the direct children.
            self.assertEqual(
                sorted(str(child.GetPath()) for child in prim_utils.get_children_prims(parent, level=1)),
                ["/P/plain", "/P/ref"],
            )


class TestExtendedSelection(unittest.TestCase):
    def test_instance_expands_to_mesh(self):
        stage = Usd.Stage.Open(str(CAPTURE))
        selection = prim_utils.get_extended_selection(stage, [INSTANCE_PATH + "/mesh"])
        self.assertIn(PROTOTYPE_PATH + "/mesh", selection)


if __name__ == "__main__":
    unittest.main()
