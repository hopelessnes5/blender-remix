"""Copy replacement-layer overrides to a new prim path.

Port of ``Setup.copy_replacement_overrides_to_path``,
``Setup._copy_overrides_in_layer`` and ``Setup._copy_material_overrides`` from
``lightspeed.trex.asset_replacements.core.shared`` of NVIDIA RTX Remix Toolkit
1.6.1 (Apache-2.0):

    SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
    SPDX-License-Identifier: Apache-2.0

The Toolkit calls this when duplicating a replacement prim
(``SetupUi._on_duplicate_prim``: ``CopyPrimCommand`` authors the root prim's
opinions, then this copies the *descendant* overrides the duplicate left out)
and when duplicating a reference (``SetupUi._add_new_ref_mesh``).  For every
prim under the source path it copies, across the replacement layer and all of
its sublayers:

* attribute-override specs, with ``Sdf.CopySpec`` into the stage's edit-target
  layer after making sure each destination attribute definition exists, and
* relationship overrides such as ``material:binding``, remapping targets that
  live inside the source sub-hierarchy to the destination prefix and leaving
  external targets (shared ``/RootNode/Looks/`` materials) untouched.

The Kit dependencies were replaced with plain ``pxr``: the Kit layer manager is
:func:`rtx_remix_importer.nvidia_core.composition.find_layer_of_type`, and
``SetRelationshipTargetsCommand`` becomes ``Usd.Relationship.SetTargets``.
Only the mod (replacement) layer is ever written into — a replacement layer
that is actually a capture layer, or an edit target pointing at the capture
layer, is rejected instead of corrupting the game capture.
"""

from __future__ import annotations

__all__ = ["copy_replacement_overrides_to_path"]

import logging

from pxr import Sdf, Usd

from . import composition, constants

_LOG = logging.getLogger(__name__)


def _ensure_mod_layer(layer: Sdf.Layer) -> Sdf.Layer:
    """Reject capture layers so override copying can never edit the game capture."""
    layer_type = layer.customLayerData.get(constants.LAYER_TYPE_KEY)
    if layer_type == constants.LAYER_TYPE_CAPTURE:
        raise ValueError(
            f"Refusing to read from or write into the capture layer {layer.identifier}; "
            "copy replacement overrides in the mod (replacement) layer instead"
        )
    return layer


def _ensure_capture_edit_target(stage: Usd.Stage) -> None:
    """Reject an edit target that would author the copied overrides into the capture."""
    target_layer = stage.GetEditTarget().GetLayer()
    layer_type = target_layer.customLayerData.get(constants.LAYER_TYPE_KEY) if target_layer else None
    if layer_type == constants.LAYER_TYPE_CAPTURE:
        raise ValueError(
            f"Refusing to author into the capture layer {target_layer.identifier}; "
            "set the edit target to the mod (replacement) layer first"
        )


def copy_replacement_overrides_to_path(
    stage: Usd.Stage,
    source_prim_path: Sdf.Path | str,
    dest_prim_path: Sdf.Path | str,
    replacement_layer: Sdf.Layer | None = None,
) -> None:
    """Copy replacement-layer override specs for a prim and all its descendants
    from *source_prim_path* to *dest_prim_path*, across the replacement layer
    and all its sublayers.

    CopyPrimCommand already handles the root prim's overrides. This function
    fills the gap for child prims whose override specs in the replacement layer
    are not included by the default ``duplicate_layers=False`` copy: both
    attribute overrides and relationship overrides (e.g. ``material:binding``)
    are copied.  The root prim itself is deliberately skipped.

    Args:
        stage: The composed stage; *source* and *destination* prims must exist
            here.  Copied specs are authored into the stage's edit-target layer.
        source_prim_path: Path of the prim whose replacement overrides to copy.
        dest_prim_path: Path of the duplicated prim to copy overrides to.
        replacement_layer: The mod layer to scan.  Defaults to the stage's
            strongest ``replacement``-typed layer; without one the call is a
            no-op, matching the Toolkit's layer-manager early return.  Capture
            layers are rejected.
    """
    layer = replacement_layer or composition.find_layer_of_type(stage, constants.LAYER_TYPE_REPLACEMENT)
    if layer is None:
        _LOG.info("No replacement layer; not copying overrides from %s to %s", source_prim_path, dest_prim_path)
        return
    _ensure_mod_layer(layer)
    _ensure_capture_edit_target(stage)

    source_path = Sdf.Path(str(source_prim_path))
    dest_path = Sdf.Path(str(dest_prim_path))

    source_prim = stage.GetPrimAtPath(source_path)
    if not source_prim:
        return

    # GetChildren returns direct children only. For each child, include
    # it and all of its descendants so every override spec is covered.
    prim_paths = [p.GetPath() for child in source_prim.GetChildren() for p in Usd.PrimRange(child)]

    _LOG.info("Copying replacement overrides from %s to %s (%d prim spec(s))", source_path, dest_path, len(prim_paths))
    _copy_overrides_in_layer(stage, layer, source_path, dest_path, prim_paths)
    _copy_material_overrides(stage, layer, source_path, dest_path, prim_paths)


def _copy_overrides_in_layer(
    stage: Usd.Stage,
    layer: Sdf.Layer,
    source_path: Sdf.Path,
    dest_path: Sdf.Path,
    prim_paths: list[Sdf.Path],
) -> None:
    """Copy prim specs for replacement overrides from under *source_path* to under *dest_path*
    within the same physical *layer*, for each path in *prim_paths* that has a prim spec in
    *layer*, then recurse into *layer*'s sublayers.

    For each remapped pair ``old_path`` → ``new_path`` (via ``ReplacePrefix``), requires valid
    source and destination prims on *stage*.  Ensures each attribute on the source prim has a
    matching attribute definition on the destination prim (creating it when missing), then runs
    ``Sdf.CopySpec(layer, old_path, <edit target layer>, new_path)`` to copy the subtree of
    specs at *old_path* to *new_path* in that same layer (not the stage edit target by other
    means).  The edit target must not be the capture layer; writes never touch *layer* unless
    it *is* the edit target.

    Args:
        stage: Stage used to resolve prims and author attribute values.
        layer: Layer or sublayer to scan for ``GetPrimAtPath`` specs.
        source_path: The source prim path prefix to copy overrides from.
        dest_path: The destination prim path prefix to copy overrides to.
        prim_paths: The prim paths whose specs should be remapped.
    """
    # Find which prim paths have specs in this layer.
    paths_with_specs = [p for p in prim_paths if layer.GetPrimAtPath(p)]
    target_layer = stage.GetEditTarget().GetLayer()

    if paths_with_specs:
        for old_path in paths_with_specs:
            new_path = old_path.ReplacePrefix(source_path, dest_path)
            if old_path == new_path:
                continue
            old_prim = stage.GetPrimAtPath(old_path)
            new_prim = stage.GetPrimAtPath(new_path)
            if not old_prim or not old_prim.IsValid() or not new_prim or not new_prim.IsValid():
                continue
            with Usd.EditContext(stage):
                for attr in old_prim.GetAttributes():
                    if not attr or not attr.IsValid():
                        continue
                    name = attr.GetName()
                    if not name:
                        continue
                    dst_attr = new_prim.GetAttribute(name)
                    if not dst_attr or not dst_attr.IsValid():
                        dst_attr = new_prim.CreateAttribute(
                            name,
                            attr.GetTypeName(),
                            custom=attr.IsCustom(),
                        )
                        dst_attr.SetVariability(attr.GetVariability())
                Sdf.CopySpec(layer, old_path, target_layer, new_path)

    # Recurse into sublayers
    for sub_path in layer.subLayerPaths:
        sublayer = Sdf.Layer.FindOrOpen(layer.ComputeAbsolutePath(sub_path))
        if sublayer:
            _copy_overrides_in_layer(stage, sublayer, source_path, dest_path, prim_paths)


def _copy_material_overrides(
    stage: Usd.Stage,
    layer: Sdf.Layer,
    source_path: Sdf.Path,
    dest_path: Sdf.Path,
    prim_paths: list[Sdf.Path],
) -> None:
    """Copy relationship overrides from source_path to dest_path for each path in prim_paths,
    scanning *layer* and recursing into sublayers.

    Only relationships explicitly authored in *layer* are copied — base-file bindings coming
    from referenced assets are not touched.  Targets that live inside the source prim's
    sub-hierarchy are remapped to the dest prefix; targets that point elsewhere (e.g. shared
    Remix materials under ``/RootNode/Looks/``) are copied as-is.

    Only targets authored in *layer*'s own relationship spec are written — not the full
    composed target set from the stage.  ``Sdf.PathListOp.explicitItems`` covers a
    full-replace override; ``prependedItems`` covers an additive override.  Either
    represents what the replacement layer explicitly authored, excluding base-asset bindings
    that were not overridden.  The copied list is authored explicitly into the stage's
    edit-target layer (``Usd.Relationship.SetTargets`` stands in for the Toolkit's
    ``SetRelationshipTargetsCommand``).

    Args:
        stage: Stage used to check dest prim validity.
        layer: Layer or sublayer to scan for relationship specs.
        source_path: The source prim path prefix to copy overrides from.
        dest_path: The destination prim path prefix to copy overrides to.
        prim_paths: The prim paths whose relationship specs should be remapped.
    """
    for old_path in prim_paths:
        prim_spec = layer.GetPrimAtPath(old_path)
        if not prim_spec:
            continue

        new_path = old_path.ReplacePrefix(source_path, dest_path)
        if new_path == old_path:
            continue

        dest_prim = stage.GetPrimAtPath(new_path)
        if not dest_prim or not dest_prim.IsValid():
            continue

        for rel_spec in prim_spec.relationships:
            rel_name = rel_spec.name

            # Read only the targets authored in this layer's spec, not the full composed set
            # from the stage. explicitItems = full-replace override (takes precedence);
            # prependedItems = additive override. Both represent only what this layer
            # overrides, excluding base-asset bindings the replacement layer did not author.
            target_list = rel_spec.targetPathList
            layer_targets = list(target_list.explicitItems) or list(target_list.prependedItems) + list(
                target_list.appendedItems
            )
            if not layer_targets:
                continue

            # Remap targets inside the source sub-hierarchy; leave external paths unchanged.
            remapped_targets = [
                t.ReplacePrefix(source_path, dest_path) if t.HasPrefix(source_path) else t for t in layer_targets
            ]

            dest_rel = dest_prim.GetRelationship(rel_name)
            if not dest_rel or not dest_rel.IsValid():
                dest_rel = dest_prim.CreateRelationship(rel_name, custom=False)

            dest_rel.SetTargets(remapped_targets)

    # Recurse into sublayers
    for sub_path in layer.subLayerPaths:
        sublayer = Sdf.Layer.FindOrOpen(layer.ComputeAbsolutePath(sub_path))
        if sublayer:
            _copy_material_overrides(stage, sublayer, source_path, dest_path, prim_paths)
