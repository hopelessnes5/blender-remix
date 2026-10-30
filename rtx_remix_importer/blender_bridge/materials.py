"""Material resolution and assignment for the direct RTX Remix backend.

Material *conversion* stays in :mod:`rtx_remix_importer.material_utils` (the
existing AperturePBR -> Blender node graph); this module only resolves which
USD material a capture instance uses and applies it at object level so a
shared prototype mesh can carry per-instance overrides.
"""

from __future__ import annotations

import bpy
from pxr import Usd, UsdGeom, UsdShade

from ..material_utils import create_material, get_or_create_instance_material

__all__ = ["assign_material", "resolve_bound_material_path"]


def _material_prim_exists(prim: Usd.Prim, path: str) -> bool:
    """Return whether *path* resolves to a real material prim.

    Capture instances routinely point their ``material:binding`` at a path
    that no longer exists (``/RootNode/Looks/...`` while the capture authors
    ``/Looks/...``).  ``ComputeBoundMaterial`` happily returns such a target,
    so every candidate is validated before it is handed to the converter.
    """
    stage = prim.GetStage() if prim else None
    if stage is None:
        return False
    material_prim = stage.GetPrimAtPath(path)
    return bool(material_prim and material_prim.IsValid() and material_prim.IsA(UsdShade.Material))


def _direct_binding_path(prim: Usd.Prim) -> str | None:
    """Read the raw ``material:binding`` relationship of *prim*.

    This is the lookup the legacy importer used.  It is needed on top of
    ``ComputeBoundMaterial`` because that helper ignores bindings authored on
    prims that do not apply ``MaterialBindingAPI`` (common in older captures).
    """
    if not prim or not prim.IsValid():
        return None
    relationship = UsdShade.MaterialBindingAPI(prim).GetDirectBindingRel()
    if not relationship:
        return None
    for target in relationship.GetTargets():
        path = str(target)
        if _material_prim_exists(prim, path):
            return path
    return None


def _bound_material_path(prim: Usd.Prim) -> str | None:
    if not prim or not prim.IsValid():
        return None
    path = _direct_binding_path(prim)
    if path:
        return path
    material, _relationship = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    if material:
        material_prim = material.GetPrim()
        if material_prim and material_prim.IsValid():
            path = str(material_prim.GetPath())
            if _material_prim_exists(prim, path):
                return path
    return None


def resolve_bound_material_path(
    prim: Usd.Prim, prototype_prim: Usd.Prim | None = None
) -> str | None:
    """Resolve the material bound to *prim*, including prototype fallbacks.

    A capture's instance binding can point at a material that no longer
    exists (``/RootNode/Looks/...``), while the prototype still binds the
    capture's ``/Looks/...`` material.  The lookup therefore tries the
    instance, the prototype, and finally the mesh/subset children of both.

    Args:
        prim: The instance prim.
        prototype_prim: Optional ``/RootNode/meshes/mesh_<HASH>`` prim.

    Returns:
        The material prim path, or ``None`` when nothing is bound.
    """
    for candidate in (prim, prototype_prim):
        path = _bound_material_path(candidate)
        if path:
            return path

    roots = [candidate for candidate in (prim, prototype_prim) if candidate and candidate.IsValid()]
    for root in roots:
        for child in Usd.PrimRange(root):
            if child.IsA(UsdGeom.Mesh) or child.IsA(UsdGeom.Subset):
                path = _bound_material_path(child)
                if path:
                    return path
    return None


def assign_material(
    bl_object: bpy.types.Object,
    material_path: str,
    usd_stage: Usd.Stage,
    source_file_path: str,
    instance_metadata: dict | None,
    material_cache: dict | None = None,
) -> bpy.types.Material | None:
    """Create/convert *material_path* and bind it to *bl_object*.

    The material is linked to the object (``slot.link = 'OBJECT'``) so two
    instances sharing one prototype mesh can use different materials without
    duplicating geometry.

    Returns:
        The Blender material, or ``None`` when conversion failed.
    """
    if not material_path:
        return None
    cache = material_cache if material_cache is not None else {}
    try:
        if instance_metadata:
            bl_material = get_or_create_instance_material(
                base_material_path=material_path,
                instance_metadata=instance_metadata,
                usd_stage=usd_stage,
                usd_file_path_context=source_file_path,
                material_cache=cache,
            )
        else:
            bl_material = create_material(material_path, usd_stage, source_file_path)
    except Exception as exc:  # material conversion must not abort the geometry import
        print(f"  Warning: material conversion failed for {material_path}: {exc}")
        return None

    if not bl_material:
        return None

    mesh = bl_object.data
    if not mesh.materials:
        mesh.materials.append(None)
    if not bl_object.material_slots:
        bl_object.data.materials.append(None)
    slot = bl_object.material_slots[0]
    slot.link = "OBJECT"
    slot.material = bl_material
    return bl_material
