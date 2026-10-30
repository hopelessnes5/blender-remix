"""Tests for hiding captured meshes in the mod layer (no mesh export)."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from pxr import Sdf, Usd

from rtx_remix_importer.nvidia_core import constants, hide

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "full_project"
MESH_PATH = "/RootNode/meshes/mesh_6CA2F12444DEBE09"
INSTANCE_PATH = "/RootNode/instances/inst_6CA2F12444DEBE09_0"
INSTANCE_MESH = INSTANCE_PATH + "/mesh"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mesh_child(stage: Usd.Stage) -> Usd.Prim | None:
    """Return the composed ``mesh`` child of the instance, or ``None``.

    USD returns an *expired* (``IsValid() == False``) prim when a reference is
    masked, so a plain ``is None`` check is not enough.
    """
    prim = stage.GetPrimAtPath(INSTANCE_MESH)
    return prim if prim is not None and prim.IsValid() else None


class HideTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "project"
        shutil.copytree(DATA_DIR, self.project)
        self.mod_path = self.project / "mod.usda"
        self.capture_path = self.project / "capture.usda"
        self.capture_hash = _sha256(self.capture_path)
        self.layer = Sdf.Layer.FindOrOpen(str(self.mod_path))
        self.assertIsNotNone(self.layer)

    def tearDown(self):
        self._tmp.cleanup()

    def composed(self) -> Usd.Stage:
        return Usd.Stage.Open(str(self.project / "full_project.usda"))

    def test_hide_masks_capture_geometry_only(self):
        self.assertIsNotNone(_mesh_child(self.composed()))

        self.assertTrue(hide.hide_mesh(self.layer, INSTANCE_PATH))
        self.layer.Save()

        stage = self.composed()
        # The instance itself still exists as an empty over, but its referenced
        # capture geometry is gone.
        self.assertIsNotNone(stage.GetPrimAtPath(INSTANCE_PATH))
        self.assertIsNone(_mesh_child(stage))
        self.assertIn("references = None", self.mod_path.read_text(encoding="utf-8"))
        self.assertEqual(_sha256(self.capture_path), self.capture_hash)

    def test_hide_is_idempotent(self):
        self.assertTrue(hide.hide_mesh(self.layer, INSTANCE_PATH))
        self.assertTrue(hide.is_mesh_hidden(self.layer, INSTANCE_PATH))
        self.assertFalse(hide.hide_mesh(self.layer, INSTANCE_PATH))

    def test_unhide_restores_the_capture_and_drops_the_empty_override(self):
        hide.hide_mesh(self.layer, INSTANCE_PATH)
        self.layer.Save()
        self.assertIsNone(_mesh_child(self.composed()))

        self.assertTrue(hide.unhide_mesh(self.layer, INSTANCE_PATH))
        self.layer.Save()

        self.assertFalse(hide.is_mesh_hidden(self.layer, INSTANCE_PATH))
        self.assertIsNone(self.layer.GetPrimAtPath(INSTANCE_PATH))
        self.assertIsNotNone(_mesh_child(self.composed()))
        self.assertNotIn("references = None", self.mod_path.read_text(encoding="utf-8"))
        self.assertEqual(_sha256(self.capture_path), self.capture_hash)

    def test_unhide_keeps_overrides_that_still_hold_data(self):
        hide.hide_mesh(self.layer, INSTANCE_PATH)
        holder = Sdf.CreatePrimInLayer(self.layer, INSTANCE_PATH + "/ref_0123456789abcdef")
        holder.specifier = Sdf.SpecifierDef
        holder.typeName = "Xform"

        self.assertTrue(hide.unhide_mesh(self.layer, INSTANCE_PATH))
        spec = self.layer.GetPrimAtPath(INSTANCE_PATH)
        self.assertIsNotNone(spec)
        self.assertEqual([child.name for child in spec.nameChildren], ["ref_0123456789abcdef"])
        self.assertFalse(hide.is_mesh_hidden(self.layer, INSTANCE_PATH))

    def test_unhide_is_a_noop_when_not_hidden(self):
        self.assertFalse(hide.unhide_mesh(self.layer, INSTANCE_PATH))

    def test_non_mesh_paths_are_rejected(self):
        with self.assertRaises(ValueError):
            hide.hide_mesh(self.layer, "/RootNode/lights/light_9907D0B07D040077")
        with self.assertRaises(ValueError):
            hide.hide_mesh(self.layer, "mesh_6CA2F12444DEBE09")

    def test_prototype_paths_are_accepted(self):
        # Captures that reference external geometry mask the prototype instead.
        self.assertTrue(hide.hide_mesh(self.layer, MESH_PATH))
        self.assertTrue(hide.is_mesh_hidden(self.layer, MESH_PATH))
        self.assertTrue(hide.unhide_mesh(self.layer, MESH_PATH))

    def test_capture_layer_is_never_written(self):
        capture_layer = Sdf.Layer.FindOrOpen(str(self.capture_path))
        self.assertEqual(
            capture_layer.customLayerData.get(constants.LAYER_TYPE_KEY),
            constants.LAYER_TYPE_CAPTURE,
        )
        with self.assertRaises(ValueError):
            hide.hide_mesh(capture_layer, INSTANCE_PATH)
        self.assertEqual(_sha256(self.capture_path), self.capture_hash)

    def test_existing_replacement_reference_is_not_masked(self):
        spec = Sdf.CreatePrimInLayer(self.layer, INSTANCE_PATH)
        spec.specifier = Sdf.SpecifierOver
        spec.referenceList.prependedItems = [Sdf.Reference("./assets/ingested/mesh.usda")]

        with self.assertRaises(ValueError):
            hide.hide_mesh(self.layer, INSTANCE_PATH)


if __name__ == "__main__":
    unittest.main()
