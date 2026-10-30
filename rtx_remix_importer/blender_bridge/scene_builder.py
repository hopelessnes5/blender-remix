"""Blender scene builder for the direct RTX Remix backend.

Reads a capture (or a composed Remix project) with ``pxr`` and creates Blender
objects, materials, lights and cameras.  It is the direct replacement for the
legacy importer: no Kit process, no REST/MCP/HTTP, no intermediate files.
"""

from __future__ import annotations

import math
import os
import traceback

import bpy
import mathutils
from pxr import Usd, UsdGeom

from ..legacy_import_core import get_remix_metadata, setup_blender_collections
from ..light_utils import import_lights_from_usd
from ..nvidia_core import constants, geometry, prim_utils
from ..nvidia_core.composition import ProjectComposition
from .materials import assign_material, resolve_bound_material_path
from .mesh import create_mesh_datablock

__all__ = ["import_with_direct_backend", "resolve_unit_scale"]

# The add-on historically exposed a 0.01 "Capture Scale" default.  When the
# user leaves that default untouched the direct backend uses the capture's own
# metersPerUnit instead of assuming centimetres.
LEGACY_DEFAULT_SCENE_SCALE = 0.01


def resolve_unit_scale(composition: ProjectComposition, scene_scale: float | None) -> float:
    """Return the USD-unit -> Blender-metre factor for an import.

    ``scene_scale`` is the UI multiplier.  Its historical default (``0.01``)
    is treated as "derive from the capture": the stage's ``metersPerUnit`` is
    used instead, as RTX Remix captures author ``0.01`` for centimetre data.
    Any other value is an explicit user override and is honoured verbatim.
    """
    stage_scale = composition.meters_per_unit
    if scene_scale is None:
        return stage_scale
    scene_scale = float(scene_scale)
    if abs(scene_scale - LEGACY_DEFAULT_SCENE_SCALE) < 1e-9:
        return stage_scale
    return scene_scale


def _object_matrix(composition: ProjectComposition, prim: Usd.Prim, unit_scale: float) -> mathutils.Matrix:
    """Compose primitive world transform, Y-up -> Z-up and unit scaling."""
    time_code = Usd.TimeCode.Default()
    xform_cache = UsdGeom.XformCache(time_code)
    matrix = mathutils.Matrix(geometry.matrix_usd_to_blender(xform_cache.GetLocalToWorldTransform(prim)))
    if composition.up_axis_is_y:
        matrix = mathutils.Matrix.Rotation(math.radians(90.0), 4, "X") @ matrix
    if unit_scale != 1.0:
        matrix = mathutils.Matrix.Scale(unit_scale, 4) @ matrix
    return matrix


def _blender_name(prim: Usd.Prim) -> str:
    return bpy.path.clean_name(prim.GetName()) or "remix_prim"


def _hash_from_path(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    parts = name.split("_")
    if len(parts) >= 2 and len(parts[1]) == 16:
        return parts[1]
    return None


class _MeshRegistry:
    """Prototype path / hash -> Blender mesh datablock."""

    def __init__(self):
        self._by_path: dict[str, bpy.types.Mesh] = {}
        self._by_hash: dict[str, bpy.types.Mesh] = {}

    def add(self, prototype_path: str, mesh_prim_path: str, bl_mesh: bpy.types.Mesh) -> None:
        self._by_path[prototype_path] = bl_mesh
        if mesh_prim_path != prototype_path:
            self._by_path[mesh_prim_path] = bl_mesh
        mesh_hash = _hash_from_path(prototype_path)
        if mesh_hash:
            self._by_hash.setdefault(mesh_hash, bl_mesh)

    def find(self, prototype_prim: Usd.Prim | None, instance_path: str) -> tuple[bpy.types.Mesh | None, str | None]:
        if prototype_prim is not None and prototype_prim.IsValid():
            path = str(prototype_prim.GetPath())
            mesh = self._by_path.get(path)
            if mesh is not None:
                return mesh, path
        mesh_hash = _hash_from_path(instance_path)
        if mesh_hash:
            mesh = self._by_hash.get(mesh_hash)
            if mesh is not None:
                return mesh, mesh_hash
        return None, None


def _build_prototypes(stage: Usd.Stage, registry: _MeshRegistry) -> int:
    """Create one Blender mesh datablock per ``/RootNode/meshes/mesh_<HASH>``."""
    meshes_root = stage.GetPrimAtPath(constants.ROOTNODE_MESHES)
    if not meshes_root or not meshes_root.IsValid():
        print("No /RootNode/meshes found in stage")
        return 0

    created = 0
    for prototype_prim in meshes_root.GetChildren():
        if not (prototype_prim.IsA(UsdGeom.Xformable) or prototype_prim.IsA(UsdGeom.Mesh)):
            continue
        prototype_path = str(prototype_prim.GetPath())
        mesh_prim = geometry.find_mesh_prim(prototype_prim)
        if mesh_prim is None:
            if prim_utils.is_empty_mesh_prim(prototype_prim):
                print(f"  Info: {prototype_path} is an empty mesh prototype (capture reference removed)")
            else:
                print(f"  Warning: no UsdGeom.Mesh under prototype {prototype_path}")
            continue
        try:
            mesh_data = geometry.extract_mesh_data(mesh_prim, prototype_path=prototype_path)
            if mesh_data is None:
                print(f"  Warning: no usable geometry on {mesh_prim.GetPath()}")
                continue
            bl_mesh = create_mesh_datablock(mesh_data, f"{_blender_name(prototype_prim)}_data")
            registry.add(prototype_path, str(mesh_prim.GetPath()), bl_mesh)
            created += 1
        except Exception:
            print(f"ERROR extracting geometry from {prototype_path}:")
            traceback.print_exc()
    print(f"Created {created} Blender mesh datablocks from capture prototypes.")
    return created


def _create_camera(
    context, composition: ProjectComposition, cam_prim: Usd.Prim, unit_scale: float
) -> bpy.types.Object | None:
    """Create a Blender camera from a ``UsdGeom.Camera`` prim (legacy behaviour)."""
    time_code = Usd.TimeCode.Default()
    usd_camera = UsdGeom.Camera(cam_prim)
    name = _blender_name(cam_prim)
    bl_cam_data = bpy.data.cameras.new(name=name)

    focal_length = usd_camera.GetFocalLengthAttr().Get(time_code)
    horizontal_aperture = usd_camera.GetHorizontalApertureAttr().Get(time_code)
    clipping_range = usd_camera.GetClippingRangeAttr().Get(time_code)
    if focal_length is not None:
        bl_cam_data.lens = focal_length
    if horizontal_aperture is not None:
        bl_cam_data.sensor_width = horizontal_aperture
        bl_cam_data.sensor_fit = "HORIZONTAL"
    if clipping_range is not None:
        bl_cam_data.clip_start = clipping_range[0]
        bl_cam_data.clip_end = clipping_range[1]

    bl_cam_obj = bpy.data.objects.new(name, bl_cam_data)
    bl_cam_obj.matrix_world = _object_matrix(composition, cam_prim, unit_scale)
    bl_cam_obj.data["is_remix_camera"] = True
    bl_cam_obj.data["remix_capture_source"] = os.path.basename(composition.path)
    return bl_cam_obj


def import_with_direct_backend(
    context,
    usd_file_path: str,
    import_materials: bool,
    import_lights: bool,
    scene_scale: float | None,
):
    """Import *usd_file_path* with the direct ``pxr`` backend.

    Returns the same ``(objects, lights, cameras, message)`` tuple as the
    legacy importer, so the UI operators do not change.
    """
    _import_collection, collections = setup_blender_collections(context, usd_file_path)
    composition = ProjectComposition.open(usd_file_path)
    stage = composition.stage
    unit_scale = resolve_unit_scale(composition, scene_scale)

    if composition.capture_layer is None:
        print(f"Warning: {usd_file_path} has no layer with lightspeed_layer_type = 'capture'")
    else:
        print(
            f"Direct backend: capture='{composition.capture_layer.identifier}', "
            f"metersPerUnit={composition.meters_per_unit}, upAxisIsY={composition.up_axis_is_y}, "
            f"unit_scale={unit_scale}"
        )

    created_objects: set[bpy.types.Object] = set()
    created_lights: list[bpy.types.Object] = []
    created_cameras: list[bpy.types.Object] = []
    material_cache: dict = {}

    try:
        registry = _MeshRegistry()
        _build_prototypes(stage, registry)

        instances_root = stage.GetPrimAtPath(constants.ROOTNODE_INSTANCES)
        instance_count = 0
        if instances_root and instances_root.IsValid():
            for instance_prim in instances_root.GetChildren():
                if not instance_prim.IsA(UsdGeom.Xformable):
                    continue
                instance_path = str(instance_prim.GetPath())
                prototype_prim = prim_utils.get_prototype(instance_prim)
                bl_mesh, resolved_path = registry.find(prototype_prim, instance_path)
                if bl_mesh is None:
                    print(f"  Warning: no prototype mesh for instance {instance_path}")
                    continue
                if prim_utils.is_ghost_prim(instance_prim):
                    print(f"  Info: skipping ghost instance {instance_path}")
                    continue

                bl_object = bpy.data.objects.new(_blender_name(instance_prim), bl_mesh)
                bl_object[constants.PROP_USD_PRIM_PATH] = resolved_path or ""
                bl_object[constants.PROP_USD_INSTANCE_PATH] = instance_path
                if prototype_prim is not None and prototype_prim.IsValid():
                    bl_object[constants.PROP_USD_PROTOTYPE_PATH] = str(prototype_prim.GetPath())
                bl_object.matrix_world = _object_matrix(composition, instance_prim, unit_scale)
                collections["meshes"].objects.link(bl_object)
                created_objects.add(bl_object)
                instance_count += 1

                if import_materials:
                    material_path = resolve_bound_material_path(instance_prim, prototype_prim)
                    if material_path:
                        assign_material(
                            bl_object,
                            material_path,
                            stage,
                            usd_file_path,
                            get_remix_metadata(instance_prim),
                            material_cache,
                        )
                    else:
                        print(f"  Warning: no material binding for {instance_path}")
            print(f"Created {instance_count} objects from capture instances.")

        # A capture without instances (or a hand-authored test stage) still
        # carries prototypes; expose them so the import is not empty.
        if not created_objects:
            meshes_root = stage.GetPrimAtPath(constants.ROOTNODE_MESHES)
            for prototype_prim in meshes_root.GetChildren() if meshes_root else ():
                prototype_path = str(prototype_prim.GetPath())
                bl_mesh, resolved_path = registry.find(prototype_prim, prototype_path)
                if bl_mesh is None:
                    continue
                bl_object = bpy.data.objects.new(_blender_name(prototype_prim), bl_mesh)
                bl_object[constants.PROP_USD_PRIM_PATH] = resolved_path or prototype_path
                bl_object.matrix_world = _object_matrix(composition, prototype_prim, unit_scale)
                collections["meshes"].objects.link(bl_object)
                created_objects.add(bl_object)
                if import_materials:
                    material_path = resolve_bound_material_path(prototype_prim)
                    if material_path:
                        assign_material(
                            bl_object,
                            material_path,
                            stage,
                            usd_file_path,
                            None,
                            material_cache,
                        )
            if created_objects:
                print("No instances found; imported mesh prototypes directly.")

        if import_lights:
            created_lights = import_lights_from_usd(stage, collections["lights"], unit_scale)
            for light in created_lights:
                light[constants.PROP_SOURCE_CAPTURE] = usd_file_path

        for prim in stage.TraverseAll():
            if not prim.IsA(UsdGeom.Camera):
                continue
            try:
                camera = _create_camera(context, composition, prim, unit_scale)
            except Exception:
                print(f"ERROR creating camera from {prim.GetPath()}:")
                traceback.print_exc()
                continue
            if camera is not None:
                collections["cameras"].objects.link(camera)
                created_cameras.append(camera)

        context.scene[constants.PROP_SOURCE_CAPTURE] = usd_file_path
        context.scene[constants.PROP_PROJECT_ID] = os.path.basename(
            composition.capture_directory or os.path.dirname(usd_file_path)
        )
        context.scene["remix_import_backend"] = "direct"
        context.scene["remix_import_unit_scale"] = unit_scale

        message = (
            f"{os.path.basename(usd_file_path)}: {len(created_objects)} objects, "
            f"{len(created_lights)} lights, {len(created_cameras)} cameras "
            f"[direct pxr backend, scale {unit_scale:g}]"
        )
        return created_objects, created_lights, created_cameras, message
    finally:
        composition.close()
