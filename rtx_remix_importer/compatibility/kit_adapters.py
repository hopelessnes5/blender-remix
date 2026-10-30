"""Narrow adapters that replace the Omniverse Kit helpers used by the Toolkit.

The NVIDIA RTX Remix Toolkit source is written against ``omni.usd``,
``omni.client`` and ``omni.flux``.  Blender ships OpenUSD (``pxr``) but none of
those modules, so this file re-implements exactly the handful of helpers the
ported core needs.  Every adapter keeps the Observable behaviour of its Kit
counterpart; where that is not possible the difference is documented on the
function.

SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
SPDX-License-Identifier: Apache-2.0

(The ported helper semantics follow NVIDIA RTX Remix Toolkit 1.6.1; the
implementation below is a Blender/pxr rewrite.)
"""

from __future__ import annotations

import posixpath
import re
from enum import Enum

from pxr import Sdf, Usd, UsdShade

__all__ = [
    "LightTypes",
    "get_composed_references_from_prim",
    "get_introducing_layer",
    "get_light_type",
    "get_shader_from_material",
    "normalize_url",
]


class LightTypes(Enum):
    """USD Lux light types and their display names (``omni.flux.utils.common.lights``)."""

    CylinderLight = "Cylinder Light"
    DiskLight = "Disk Light"
    DistantLight = "Distant Light"
    DomeLight = "Dome Light"
    PortalLight = "Portal Light"
    RectLight = "Rect Light"
    SphereLight = "Sphere Light"


_LIGHT_TYPES = {
    "CylinderLight": LightTypes.CylinderLight,
    "DiskLight": LightTypes.DiskLight,
    "DistantLight": LightTypes.DistantLight,
    "DomeLight": LightTypes.DomeLight,
    # Older capture files spell the dome light ``DomeLight_1``.
    "DomeLight_1": LightTypes.DomeLight,
    "PortalLight": LightTypes.PortalLight,
    "RectLight": LightTypes.RectLight,
    "SphereLight": LightTypes.SphereLight,
}


def get_light_type(class_name: str) -> LightTypes | None:
    """Return the :class:`LightTypes` for a USD Lux type name, or ``None``.

    Mirrors ``omni.flux.utils.common.lights.get_light_type``, including the
    historical ``DomeLight_1`` alias.
    """
    if class_name is None:
        return None
    return _LIGHT_TYPES.get(str(class_name))


_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*://)(.*)$")
_DRIVE_RE = re.compile(r"^([A-Za-z]:)(.*)$")


def normalize_url(path: str) -> str:
    """Normalize a file path the way ``omni.client.normalize_url`` does.

    Backslashes become forward slashes, duplicate separators and ``.`` /
    ``..`` segments are resolved, and a trailing separator is removed.  URL
    schemes and Windows drive letters are preserved.
    """
    if not path:
        return path
    text = str(path).replace("\\", "/")

    scheme = ""
    match = _SCHEME_RE.match(text)
    if match:
        scheme, text = match.group(1), match.group(2)

    drive = ""
    match = _DRIVE_RE.match(text)
    if match:
        drive, text = match.group(1), match.group(2)

    normalized = posixpath.normpath(text)
    if normalized == ".":
        normalized = ""
    return f"{scheme}{drive}{normalized}"


def _reference_items(prim: Usd.Prim) -> list[Sdf.Reference]:
    """Flatten the composed ``references`` list op of *prim*."""
    if not prim or not prim.IsValid():
        return []
    metadata = prim.GetMetadata("references")
    if not metadata:
        return []
    return [item for item in metadata.GetAppliedItems() if isinstance(item, Sdf.Reference)]


def _reference_authoring_layer(prim: Usd.Prim, reference: Sdf.Reference) -> Sdf.Layer | None:
    """Return the strongest layer that authors *reference* on *prim*."""
    for spec in prim.GetPrimStack():
        if not spec.hasReferences:
            continue
        if _prim_spec_has_reference(spec, reference):
            return spec.layer
    return None


def _prim_spec_has_reference(spec: Sdf.PrimSpec, reference: Sdf.Reference) -> bool:
    if spec is None or not spec.hasReferences:
        return False
    reference_list = spec.GetInfo(Sdf.PrimSpec.ReferencesKey)
    return bool(reference_list and reference_list.HasItem(reference))


def get_composed_references_from_prim(
    prim: Usd.Prim, fix_slashes: bool = True
) -> list[tuple[Sdf.Reference, Sdf.Layer]]:
    """Return ``(reference, authoring_layer)`` for every composed reference.

    Port of ``omni.usd.get_composed_references_from_prim``.  The second tuple
    member is the layer the reference is authored in, so relative asset paths
    resolve against it (``layer.ComputeAbsolutePath(ref.assetPath)``).

    Differences from the Kit helper:

    * ``fix_slashes`` is honoured by rebuilding the returned reference with
      forward slashes; the identity of the reference in the layer is unchanged.
    * References authored on the session layer are returned like any other
      layer.  Kit filters some transient session references; RTX Remix never
      authors capture references there, so the distinction is not observable.
    """
    if not prim or not prim.IsValid():
        return []

    stage = prim.GetStage()
    stage_root = stage.GetRootLayer() if stage is not None else None
    result: list[tuple[Sdf.Reference, Sdf.Layer]] = []
    for reference in _reference_items(prim):
        layer = _reference_authoring_layer(prim, reference)
        if layer is None:
            # Should not happen: the composed item must come from some layer.
            # Falling back to the root layer still yields a usable absolute path.
            layer = stage_root
        if layer is None:
            continue
        if fix_slashes and reference.assetPath:
            fixed = str(reference.assetPath).replace("\\", "/")
            if fixed != reference.assetPath:
                reference = Sdf.Reference(
                    fixed,
                    reference.primPath,
                    reference.layerOffset,
                    reference.customData,
                )
        result.append((reference, layer))
    return result


def get_introducing_layer(prim: Usd.Prim) -> tuple[Sdf.Layer | None, Sdf.Path]:
    """Return the layer that introduces *prim* and the introducing prim path.

    Port of ``omni.usd.get_introducing_layer``.  The strongest non-session
    layer that authors a ``def`` for the prim wins; if only ``over`` specs
    exist, the strongest such layer is reported.  A prim that only exists
    through a composition arc falls back to the arc's introducing layer.
    """
    if not prim or not prim.IsValid():
        return None, Sdf.Path()

    stage = prim.GetStage()
    if stage is None:
        return None, prim.GetPath()

    session_layer = stage.GetSessionLayer()
    root_layers = set(stage.GetLayerStack(includeSessionLayers=False))
    fallback = None
    for spec in prim.GetPrimStack():
        if spec.layer == session_layer:
            continue
        if fallback is None:
            fallback = spec.layer
        if spec.specifier == Sdf.SpecifierDef:
            if spec.layer in root_layers:
                return spec.layer, prim.GetPath()
            # The definition lives in a referenced layer: report the path it
            # has in that layer, which is what the Kit helper returns.
            for arc in Usd.PrimCompositionQuery(prim).GetCompositionArcs():
                if arc.GetTargetLayer() == spec.layer:
                    return spec.layer, arc.GetTargetPrimPath() or prim.GetPath()
            return spec.layer, prim.GetPath()
    if fallback is not None:
        return fallback, prim.GetPath()

    for arc in Usd.PrimCompositionQuery(prim).GetCompositionArcs():
        layer = arc.GetIntroducingLayer()
        if layer is not None:
            return layer, arc.GetIntroducingPrimPath()

    return stage.GetRootLayer(), prim.GetPath()


def get_shader_from_material(material, get_all_shaders: bool = False):
    """Return the shader prim of *material* (first child by default).

    Port of the subset of ``omni.usd.get_shader_from_material`` used by
    ``get_extended_selection``.  Returns ``None`` when the material has no
    Shader child.
    """
    if not material:
        return None
    prim = material.GetPrim() if isinstance(material, UsdShade.Material) else material
    if not prim or not prim.IsValid():
        return None
    shaders = [child for child in prim.GetChildren() if child.IsA(UsdShade.Shader)]
    if not shaders:
        return None
    return shaders if get_all_shaders else shaders[0]
