"""Tests for the ported ``copy_replacement_overrides_to_path``.

The scenarios mirror the Toolkit's own unit tests for the function
(``lightspeed.trex.asset_replacements.core.shared.tests.unit.test_core``,
Apache-2.0), rebuilt with anonymous layers instead of the Kit context.
The replacement layer is a sublayer tagged ``lightspeed_layer_type =
"replacement"``; source and destination prims are defined on the root layer,
exactly like a capture composed with a mod layer.  A final test runs the same
algorithm through ``ProjectComposition`` on the real Toolkit fixture project.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from pxr import Sdf, Usd, UsdGeom

from rtx_remix_importer.nvidia_core import composition, constants, copier

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "full_project"
FULL_PROJECT = DATA_DIR / "full_project.usda"

ROOT_SOURCE = "/RootNode/meshes/test_copy_src"
ROOT_DEST = "/RootNode/meshes/test_copy_dst"


def _mark_specs(layer: Sdf.Layer, *paths) -> None:
    """Port of the Toolkit test helper ``_mark_replacement_specs``."""
    for path in paths:
        prim_spec = Sdf.CreatePrimInLayer(layer, path)
        prim_spec.specifier = Sdf.SpecifierOver


class _StageCase(unittest.TestCase):
    """Two-layer test stage: anonymous capture root + anonymous mod layer."""

    def setUp(self):
        self.root = Sdf.Layer.CreateAnonymous("root.usda")
        self.mod = Sdf.Layer.CreateAnonymous("mod.usda")
        # customLayerData returns a copy in pxr; the setter is the only write-back.
        self.mod.customLayerData = {constants.LAYER_TYPE_KEY: constants.LAYER_TYPE_REPLACEMENT}
        self.root.subLayerPaths.append(self.mod.identifier)
        self.stage = Usd.Stage.Open(self.root)


class TestCopyOverridesToPath(_StageCase):
    def test_copies_descendant_attribute_and_material_overrides(self):
        """Attribute and material overrides on child/descendant prims are copied (not the root)."""
        source_root = f"{ROOT_SOURCE}1"
        dest_root = f"{ROOT_DEST}1"
        child_path = Sdf.Path(f"{source_root}/api_child")
        grand_path = Sdf.Path(f"{source_root}/api_child/grand_scope")
        dest_grand_path = Sdf.Path(f"{dest_root}/api_child/grand_scope")
        UsdGeom.Scope.Define(self.stage, source_root)
        UsdGeom.Scope.Define(self.stage, dest_root)
        UsdGeom.Scope.Define(self.stage, str(child_path))
        UsdGeom.Scope.Define(self.stage, str(grand_path))
        UsdGeom.Scope.Define(self.stage, f"{dest_root}/api_child")
        UsdGeom.Scope.Define(self.stage, str(dest_grand_path))
        attr_name = "copyReplaceTestAttrApi"

        # Author attribute and material override in the replacement-layer spec.
        _mark_specs(self.mod, child_path, grand_path)
        grand_spec = self.mod.GetPrimAtPath(grand_path)
        Sdf.AttributeSpec(grand_spec, attr_name, Sdf.ValueTypeNames.Float).default = 55.0
        mat_target = Sdf.Path("/RootNode/Looks/mat_BC868CE5A075ABB1")
        Sdf.RelationshipSpec(grand_spec, "material:binding", custom=False).targetPathList.Prepend(mat_target)

        # Pre-create only the dest root spec so Sdf.CopySpec can create child/grandchild
        # specs fresh — same-layer CopySpec does not copy attributes into existing specs.
        _mark_specs(self.mod, Sdf.Path(dest_root))

        # Pre-author the custom attribute on dest_grand_path in the root layer so
        # the CreateAttribute branch cannot implicitly create a replacement-layer
        # spec for dest_grand_path before Sdf.CopySpec runs.
        self.stage.GetPrimAtPath(str(dest_grand_path)).CreateAttribute(attr_name, Sdf.ValueTypeNames.Float, custom=True)

        with Usd.EditContext(self.stage, self.mod):
            # No explicit layer: resolved through the "replacement" customLayerData tag.
            copier.copy_replacement_overrides_to_path(self.stage, source_root, dest_root)

        # Attribute was copied via Sdf.CopySpec into the replacement-layer dest spec.
        dest_grand_spec = self.mod.GetPrimAtPath(dest_grand_path)
        self.assertIsNotNone(dest_grand_spec)
        copied_attr = dest_grand_spec.attributes.get(attr_name)
        self.assertIsNotNone(copied_attr)
        self.assertAlmostEqual(copied_attr.default, 55.0, places=5)
        # Material override was copied via SetTargets.
        dst_mat = self.stage.GetPrimAtPath(str(dest_grand_path)).GetRelationship("material:binding")
        self.assertTrue(dst_mat.IsValid())
        self.assertEqual(dst_mat.GetTargets(), [mat_target])
        # The root spec was authored for the duplicate; it must not gain the override.
        self.assertIsNone(self.mod.GetPrimAtPath(Sdf.Path(dest_root)).attributes.get(attr_name))

    def test_relationship_override_copied_to_dest(self):
        """A material:binding override authored in the mod layer reaches the dest prim."""
        source_root = f"{ROOT_SOURCE}2"
        dest_root = f"{ROOT_DEST}2"
        child_path = Sdf.Path(f"{source_root}/rel_child")
        dest_child_path = Sdf.Path(f"{dest_root}/rel_child")
        UsdGeom.Scope.Define(self.stage, source_root)
        UsdGeom.Scope.Define(self.stage, dest_root)
        UsdGeom.Scope.Define(self.stage, str(child_path))
        UsdGeom.Scope.Define(self.stage, str(dest_child_path))

        _mark_specs(self.mod, child_path)
        rel_spec = Sdf.RelationshipSpec(self.mod.GetPrimAtPath(child_path), "material:binding", custom=False)
        mat_target = Sdf.Path("/RootNode/Looks/mat_BC868CE5A075ABB1")
        rel_spec.targetPathList.Prepend(mat_target)

        _mark_specs(self.mod, dest_child_path)

        copier.copy_replacement_overrides_to_path(self.stage, source_root, dest_root, self.mod)

        dest_prim = self.stage.GetPrimAtPath(str(dest_child_path))
        dest_rel = dest_prim.GetRelationship("material:binding")
        self.assertTrue(dest_rel.IsValid())
        self.assertEqual(dest_rel.GetTargets(), [mat_target])

    def test_relationship_targets_inside_source_are_remapped(self):
        """Targets under the source prefix are remapped to the dest prefix."""
        source_root = f"{ROOT_SOURCE}3"
        dest_root = f"{ROOT_DEST}3"
        child_path = Sdf.Path(f"{source_root}/remap_child")
        dest_child_path = Sdf.Path(f"{dest_root}/remap_child")
        UsdGeom.Scope.Define(self.stage, source_root)
        UsdGeom.Scope.Define(self.stage, dest_root)
        UsdGeom.Scope.Define(self.stage, str(child_path))
        UsdGeom.Scope.Define(self.stage, str(dest_child_path))

        # Target lives inside the source sub-hierarchy and must be remapped.
        source_mat_target = Sdf.Path(f"{source_root}/Looks/CubeMaterial")
        expected_target = Sdf.Path(f"{dest_root}/Looks/CubeMaterial")

        _mark_specs(self.mod, child_path)
        rel_spec = Sdf.RelationshipSpec(self.mod.GetPrimAtPath(child_path), "material:binding", custom=False)
        rel_spec.targetPathList.Prepend(source_mat_target)

        _mark_specs(self.mod, dest_child_path)

        copier.copy_replacement_overrides_to_path(self.stage, source_root, dest_root, self.mod)

        dest_prim = self.stage.GetPrimAtPath(str(dest_child_path))
        self.assertEqual(dest_prim.GetRelationship("material:binding").GetTargets(), [expected_target])

    def test_relationship_override_only_in_sublayer_is_copied(self):
        """Overrides authored only in a sublayer of the mod layer are still copied."""
        source_root = f"{ROOT_SOURCE}4"
        dest_root = f"{ROOT_DEST}4"
        child_path = Sdf.Path(f"{source_root}/sub_rel_child")
        dest_child_path = Sdf.Path(f"{dest_root}/sub_rel_child")
        UsdGeom.Scope.Define(self.stage, source_root)
        UsdGeom.Scope.Define(self.stage, dest_root)
        UsdGeom.Scope.Define(self.stage, str(child_path))
        UsdGeom.Scope.Define(self.stage, str(dest_child_path))

        sublayer = Sdf.Layer.CreateAnonymous()
        self.mod.subLayerPaths.append(sublayer.identifier)

        # Author the override in the sublayer only.
        _mark_specs(sublayer, child_path)
        rel_spec = Sdf.RelationshipSpec(sublayer.GetPrimAtPath(child_path), "material:binding", custom=False)
        mat_target = Sdf.Path("/RootNode/Looks/mat_BC868CE5A075ABB1")
        rel_spec.targetPathList.Prepend(mat_target)

        # Dest must have parent specs in both layers so Sdf.CopySpec succeeds when the
        # attribute pass recurses into the sublayer.
        _mark_specs(self.mod, dest_child_path)
        _mark_specs(sublayer, dest_child_path)

        copier.copy_replacement_overrides_to_path(self.stage, source_root, dest_root, self.mod)

        dest_prim = self.stage.GetPrimAtPath(str(dest_child_path))
        self.assertEqual(dest_prim.GetRelationship("material:binding").GetTargets(), [mat_target])

    def test_only_child_specs_are_copied(self):
        """The source root prim's own override spec stays untouched, children are copied."""
        source_root = f"{ROOT_SOURCE}5"
        dest_root = f"{ROOT_DEST}5"
        child_path = Sdf.Path(f"{source_root}/root_child")
        dest_child_path = Sdf.Path(f"{dest_root}/root_child")
        UsdGeom.Scope.Define(self.stage, source_root)
        UsdGeom.Scope.Define(self.stage, dest_root)
        UsdGeom.Scope.Define(self.stage, str(child_path))
        UsdGeom.Scope.Define(self.stage, str(dest_child_path))

        _mark_specs(self.mod, source_root)
        Sdf.AttributeSpec(self.mod.GetPrimAtPath(Sdf.Path(source_root)), "rootOnly", Sdf.ValueTypeNames.Float).default = 7.0
        _mark_specs(self.mod, child_path)
        Sdf.AttributeSpec(self.mod.GetPrimAtPath(child_path), "childOnly", Sdf.ValueTypeNames.Float).default = 9.0

        _mark_specs(self.mod, Sdf.Path(dest_root))

        # Like the Toolkit's duplicate flow, the mod layer is the edit target: the
        # copied specs are authored into it, not into the root (capture) layer.
        with Usd.EditContext(self.stage, self.mod):
            copier.copy_replacement_overrides_to_path(self.stage, source_root, dest_root, self.mod)

        self.assertIsNone(self.mod.GetPrimAtPath(Sdf.Path(dest_root)).attributes.get("rootOnly"))
        copied = self.mod.GetPrimAtPath(dest_child_path)
        self.assertIsNotNone(copied)
        self.assertAlmostEqual(copied.attributes.get("childOnly").default, 9.0, places=5)

    def test_missing_source_prim_is_a_noop(self):
        """Without a source prim on the stage nothing is authored."""
        copier.copy_replacement_overrides_to_path(self.stage, f"{ROOT_SOURCE}6", f"{ROOT_DEST}6", self.mod)
        self.assertIsNone(self.mod.GetPrimAtPath(Sdf.Path(f"{ROOT_DEST}6")))
        self.assertIsNone(self.root.GetPrimAtPath(Sdf.Path(f"{ROOT_DEST}6")))

    def test_missing_dest_prim_is_a_noop(self):
        """A source override whose destination prim does not exist is skipped."""
        source_root = f"{ROOT_SOURCE}7"
        child_path = Sdf.Path(f"{source_root}/ghost_child")
        UsdGeom.Scope.Define(self.stage, source_root)
        UsdGeom.Scope.Define(self.stage, str(child_path))
        _mark_specs(self.mod, child_path)
        Sdf.AttributeSpec(self.mod.GetPrimAtPath(child_path), "ghostAttr", Sdf.ValueTypeNames.Float).default = 3.0

        copier.copy_replacement_overrides_to_path(self.stage, source_root, f"{ROOT_DEST}7", self.mod)

        self.assertIsNone(self.mod.GetPrimAtPath(Sdf.Path(f"{ROOT_DEST}7/ghost_child")))
        self.assertIsNone(self.root.GetPrimAtPath(Sdf.Path(f"{ROOT_DEST}7")))

    def test_identity_paths_are_a_noop(self):
        """Saving a prim back into itself copies nothing."""
        source_root = f"{ROOT_SOURCE}8"
        child_path = Sdf.Path(f"{source_root}/self_child")
        UsdGeom.Scope.Define(self.stage, source_root)
        UsdGeom.Scope.Define(self.stage, str(child_path))
        _mark_specs(self.mod, child_path)
        Sdf.AttributeSpec(self.mod.GetPrimAtPath(child_path), "sameAttr", Sdf.ValueTypeNames.Float).default = 2.0

        before = self.mod.ExportToString()
        copier.copy_replacement_overrides_to_path(self.stage, source_root, source_root, self.mod)
        self.assertEqual(self.mod.ExportToString(), before)

    def test_no_replacement_layer_is_a_noop(self):
        """Without a replacement-typed layer the Toolkit early return is honoured."""
        bare_root = Sdf.Layer.CreateAnonymous("bare_root.usda")
        bare_stage = Usd.Stage.Open(bare_root)
        UsdGeom.Scope.Define(bare_stage, f"{ROOT_SOURCE}9")
        copier.copy_replacement_overrides_to_path(bare_stage, f"{ROOT_SOURCE}9", f"{ROOT_DEST}9")
        self.assertIsNone(bare_root.GetPrimAtPath(Sdf.Path(f"{ROOT_DEST}9")))

    def test_capture_layer_as_source_is_rejected(self):
        """A capture-typed layer cannot stand in for the replacement layer."""
        capture = Sdf.Layer.CreateAnonymous("capture.usda")
        capture.customLayerData = {constants.LAYER_TYPE_KEY: constants.LAYER_TYPE_CAPTURE}
        self.root.subLayerPaths.append(capture.identifier)
        UsdGeom.Scope.Define(self.stage, f"{ROOT_SOURCE}10")
        UsdGeom.Scope.Define(self.stage, f"{ROOT_DEST}10")
        with self.assertRaises(ValueError):
            copier.copy_replacement_overrides_to_path(self.stage, f"{ROOT_SOURCE}10", f"{ROOT_DEST}10", capture)

    def test_capture_edit_target_is_rejected(self):
        """Overrides are never authored into a capture-typed edit target."""
        capture = Sdf.Layer.CreateAnonymous("capture2.usda")
        capture.customLayerData = {constants.LAYER_TYPE_KEY: constants.LAYER_TYPE_CAPTURE}
        self.root.subLayerPaths.append(capture.identifier)
        UsdGeom.Scope.Define(self.stage, f"{ROOT_SOURCE}11")
        UsdGeom.Scope.Define(self.stage, f"{ROOT_DEST}11")

        # A spec exists in the mod layer, but the edit target points at the capture.
        _mark_specs(self.mod, Sdf.Path(f"{ROOT_SOURCE}11"))
        Sdf.AttributeSpec(self.mod.GetPrimAtPath(Sdf.Path(f"{ROOT_SOURCE}11")), "dummyAttr", Sdf.ValueTypeNames.Float).default = 1.0

        self.stage.SetEditTarget(Usd.EditTarget(capture))
        with self.assertRaises(ValueError):
            copier.copy_replacement_overrides_to_path(self.stage, f"{ROOT_SOURCE}11", f"{ROOT_DEST}11", self.mod)
        self.assertIsNone(self.mod.GetPrimAtPath(Sdf.Path(f"{ROOT_DEST}11")))


class TestCopyOverridesInLayerSublayer(_StageCase):
    def test_attribute_specs_on_a_sublayer_are_copied(self):
        """Attribute specs on a sublayer of the replacement stack are copied within that sublayer."""
        sublayer = Sdf.Layer.CreateAnonymous()
        self.mod.subLayerPaths.append(sublayer.identifier)

        source_root = f"{ROOT_SOURCE}12"
        dest_root = f"{ROOT_DEST}12"
        child_path = Sdf.Path(f"{source_root}/sub_child")
        dest_child_path = Sdf.Path(f"{dest_root}/sub_child")
        UsdGeom.Scope.Define(self.stage, source_root)
        UsdGeom.Scope.Define(self.stage, dest_root)
        UsdGeom.Scope.Define(self.stage, str(child_path))
        UsdGeom.Scope.Define(self.stage, str(dest_child_path))
        attr_name = "copyReplaceTestAttrSub"

        # Author attribute in the sublayer spec; pre-create only the dest root spec (not
        # dest_child_path itself) so Sdf.CopySpec can create the child spec fresh.
        _mark_specs(sublayer, child_path)
        Sdf.AttributeSpec(sublayer.GetPrimAtPath(child_path), attr_name, Sdf.ValueTypeNames.Float).default = 12.0
        _mark_specs(sublayer, Sdf.Path(dest_root))

        # Pre-author all source attributes on the dest prim in the root layer so the
        # CreateAttribute branch cannot implicitly create a sublayer spec for
        # dest_child_path before Sdf.CopySpec runs.
        dest_prim = self.stage.GetPrimAtPath(str(dest_child_path))
        src_prim = self.stage.GetPrimAtPath(str(child_path))
        for src_attr in src_prim.GetAttributes():
            if src_attr and src_attr.IsValid() and not dest_prim.GetAttribute(src_attr.GetName()).IsValid():
                dest_prim.CreateAttribute(src_attr.GetName(), src_attr.GetTypeName(), custom=src_attr.IsCustom())

        # Set the edit target to sublayer so Sdf.CopySpec writes into the sublayer
        # (where the source spec lives), not the root layer.
        self.stage.SetEditTarget(Usd.EditTarget(sublayer))
        copier._copy_overrides_in_layer(
            self.stage,
            self.mod,
            Sdf.Path(source_root),
            Sdf.Path(dest_root),
            [child_path],
        )

        dest_spec = sublayer.GetPrimAtPath(dest_child_path)
        self.assertIsNotNone(dest_spec)
        copied_attr = dest_spec.attributes.get(attr_name)
        self.assertIsNotNone(copied_attr)
        self.assertAlmostEqual(copied_attr.default, 12.0, places=5)


class TestCopyOnRealProject(unittest.TestCase):
    """End-to-end on the Toolkit fixture: ProjectComposition + the copier."""

    def test_copies_override_specs_into_the_mod_layer(self):
        project = composition.ProjectComposition.open(str(FULL_PROJECT))
        try:
            stage = project.stage
            mod = project.mod_layer
            self.assertIsNotNone(mod)
            # The mod layer is the edit target, so the test authoring below and
            # the copied specs both land in the mod layer, never in the capture.
            self.assertEqual(stage.GetEditTarget().GetLayer(), mod)

            source_root = "/RootNode/meshes/copier_src"
            dest_root = "/RootNode/meshes/copier_dst"
            grand_path = f"{source_root}/child/grand"
            dest_grand_path = f"{dest_root}/child/grand"
            UsdGeom.Scope.Define(stage, source_root)
            UsdGeom.Scope.Define(stage, f"{source_root}/child")
            UsdGeom.Scope.Define(stage, grand_path)
            # The attribute/relationship overrides live in the mod layer.
            grand = stage.GetPrimAtPath(grand_path)
            grand.CreateAttribute("copierAttr", Sdf.ValueTypeNames.Float, custom=True)
            binding = grand.CreateRelationship("material:binding", custom=False)
            mat_target = Sdf.Path("/RootNode/Looks/mat_8D1946B4993CE5A3")
            binding.SetTargets([mat_target])

            # The destination subtree, as the Toolkit's CopyPrimCommand would
            # author a duplicate in the authoring layer (fresh child specs).
            UsdGeom.Scope.Define(stage, dest_root)
            UsdGeom.Scope.Define(stage, f"{dest_root}/child")
            UsdGeom.Scope.Define(stage, dest_grand_path)

            capture_before = project.capture_layer.ExportToString()

            # The replacement layer is resolved through the standard tag.
            copier.copy_replacement_overrides_to_path(stage, source_root, dest_root)

            dest = stage.GetPrimAtPath(dest_grand_path)
            self.assertEqual(
                dest.GetRelationship("material:binding").GetTargets(),
                [mat_target],
            )
            # The attribute definition was authored on the destination; whether
            # Sdf.CopySpec also carries the value depends on its existing-spec
            # semantics (see the module docs) — only the relationship is
            # guaranteed verbatim on an authored destination.
            self.assertTrue(dest.GetAttribute("copierAttr").IsValid())

            self.assertEqual(project.capture_layer.ExportToString(), capture_before)
        finally:
            project.close()


if __name__ == "__main__":
    unittest.main()
