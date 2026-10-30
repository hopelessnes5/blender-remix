"""Hide or restore captured meshes by masking their capture reference.

Port of the cross-layer branch of
``lightspeed.trex.asset_replacements.core.shared.Setup.remove_reference`` from
NVIDIA RTX Remix Toolkit 1.6.1 (Apache-2.0):

    SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
    SPDX-License-Identifier: Apache-2.0

The Toolkit "removes" the reference of the prim that makes a captured asset
visible, which the runtime interprets as *do not draw the captured geometry*.
Only the mod layer is written into, and the resulting override is the same one
the official Toolkit authors:

    over "<prim>" ( references = None )
    {
    }

Which prim needs the mask depends on the capture: the geometry of a
``mesh_<HASH>`` prototype is defined inline, so a mask on the prototype does
nothing.  The prototype is instantiated by ``inst_<HASH>_<N>`` prims that own the
``prepend references = </RootNode/meshes/mesh_<HASH>>`` opinion; those instances
are the reference owners and are what has to be masked.  Both kinds of path are
accepted here so callers can pass whichever prim their Blender object maps to.

The empty *explicit* reference list is what masks the capture layer; clearing a
layer's reference edits (``ClearReferences()``) would not hide anything, because
the stronger capture opinion would simply survive.

Children authored under the same prim in the mod layer (for example the
``ref_<uuid>`` replacement holder written by
:mod:`rtx_remix_importer.operators.export_operator`) are *not* affected: USD
still composes local child prim specs below a masked reference, so a replacement
keeps rendering while the captured geometry disappears.
"""

from __future__ import annotations

import logging
import re

from pxr import Sdf

from . import constants

_LOG = logging.getLogger(__name__)

__all__ = ["hide_mesh", "is_mesh_hidden", "unhide_mesh"]


def _validated_prim_path(prim_path) -> Sdf.Path:
    """Return *prim_path* as an ``Sdf.Path``, rejecting non-Remix-mesh paths.

    Both the captured ``mesh_<HASH>`` prototypes and the reference-owning
    ``inst_<HASH>_<N>`` instances are accepted.
    """
    path = Sdf.Path(str(prim_path))
    if not path or path.isEmpty or not path.IsAbsolutePath():
        raise ValueError(f"Not an absolute USD path: {prim_path!r}")
    text = str(path)
    if not (
        re.match(constants.REGEX_MESH_PATH, text)
        or re.match(constants.REGEX_INSTANCE_PATH, text)
    ):
        raise ValueError(f"Not a Remix captured mesh or instance path: {path}")
    return path


def _references_op(spec: Sdf.PrimSpec | None):
    return spec.GetInfo(Sdf.PrimSpec.ReferencesKey) if spec is not None else None


def _authored_items(op) -> list:
    """Return every reference item *op* would compose (explicit or list-op edits)."""
    if op is None:
        return []
    items = list(op.explicitItems)
    for attribute in ("prependedItems", "appendedItems", "addedItems"):
        items.extend(getattr(op, attribute))
    return items


def _ensure_mod_layer(layer: Sdf.Layer | None) -> Sdf.Layer:
    """Reject capture layers so a hide can never edit the game capture."""
    if layer is None:
        raise ValueError("No mod layer to author into")
    layer_type = layer.customLayerData.get(constants.LAYER_TYPE_KEY)
    if layer_type == constants.LAYER_TYPE_CAPTURE:
        raise ValueError(
            f"Refusing to write into the capture layer {layer.identifier}; "
            "hide meshes in the mod (replacement) layer instead"
        )
    return layer


def is_mesh_hidden(layer: Sdf.Layer, mesh_path) -> bool:
    """Whether *layer* masks the capture reference of *mesh_path*."""
    if layer is None:
        return False
    spec = layer.GetPrimAtPath(_validated_prim_path(mesh_path))
    op = _references_op(spec)
    return bool(op is not None and op.isExplicit and not list(op.explicitItems))


def hide_mesh(layer: Sdf.Layer, mesh_path) -> bool:
    """Author ``references = None`` for *mesh_path* into *layer*.

    Args:
        layer: The mod (replacement) layer; capture layers are rejected.
        mesh_path: ``/RootNode/meshes/mesh_<HASH>`` (inline prototype) or
            ``/RootNode/instances/inst_<HASH>_<N>`` (reference owner).

    Returns:
        ``True`` when a new mask was authored, ``False`` when the mesh was
        already hidden in that layer.

    Raises:
        ValueError: for non-mesh paths, capture layers, or a prim that already
            carries its own replacement reference in *layer* (masking it would
            hide the replacement as well).
    """
    layer = _ensure_mod_layer(layer)
    path = _validated_prim_path(mesh_path)

    spec = layer.GetPrimAtPath(path)
    op = _references_op(spec)
    if op is not None and op.isExplicit and not list(op.explicitItems):
        return False
    existing_items = _authored_items(op)
    if existing_items:
        raise ValueError(
            f"{path} already authors a replacement reference in "
            f"{layer.identifier}; remove that replacement first instead of "
            "masking the captured mesh"
        )

    if spec is None:
        spec = Sdf.CreatePrimInLayer(layer, path)
        if spec is None:  # pragma: no cover - pxr always creates the spec
            raise RuntimeError(f"Could not create a prim spec for {path} in {layer.identifier}")
        spec.specifier = Sdf.SpecifierOver

    spec.referenceList.ClearEditsAndMakeExplicit()
    _LOG.info("Hid captured mesh %s in %s", path, layer.identifier)
    return True


def unhide_mesh(layer: Sdf.Layer, mesh_path) -> bool:
    """Remove the ``references = None`` mask of *mesh_path* from *layer*.

    Returns:
        ``True`` when a mask was removed, ``False`` when the mesh was not hidden
        in that layer.  An override that becomes empty is deleted, while one that
        still holds authored data (a replacement holder, xforms, a stronger
        specifier) is kept.
    """
    layer = _ensure_mod_layer(layer)
    path = _validated_prim_path(mesh_path)

    spec = layer.GetPrimAtPath(path)
    if spec is None or not is_mesh_hidden(layer, path):
        return False

    spec.referenceList.ClearEdits()
    removable = (
        spec.specifier == Sdf.SpecifierOver
        and not spec.properties
        and not spec.nameChildren
        and not spec.hasReferences
        and not spec.GetInfo(Sdf.PrimSpec.KindKey)
    )
    if removable:
        parent = layer.GetPrimAtPath(path.GetParentPath())
        if parent is not None:
            del parent.nameChildren[path.name]

    _LOG.info("Unhid captured mesh %s in %s", path, layer.identifier)
    return True
