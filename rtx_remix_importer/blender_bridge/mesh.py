"""Create Blender mesh datablocks from :class:`nvidia_core.geometry.MeshData`."""

from __future__ import annotations

import bpy

from ..core_utils import set_custom_normals_compatible
from ..nvidia_core import constants
from ..nvidia_core.geometry import MeshData

__all__ = ["create_mesh_datablock"]


def create_mesh_datablock(mesh_data: MeshData, name: str) -> bpy.types.Mesh:
    """Build a Blender mesh with n-gons, UVs and custom normals preserved.

    One datablock is created per USD prototype so every instance shares it;
    per-instance material overrides go through object-linked material slots
    (see :mod:`rtx_remix_importer.blender_bridge.materials`).

    Args:
        mesh_data: Geometry extracted from the prototype mesh prim.
        name: Blender datablock name.

    Returns:
        The new mesh datablock.
    """
    bl_mesh = bpy.data.meshes.new(name=name)
    bl_mesh.from_pydata(list(mesh_data.points), [], mesh_data.polygons())
    bl_mesh.update(calc_edges=True)

    uvs = mesh_data.corner_uvs()
    if uvs:
        uv_layer = bl_mesh.uv_layers.new(name="st")
        flat = [component for uv in uvs for component in uv]
        uv_layer.data.foreach_set("uv", flat)
    else:
        print(f"  Info: no usable 'st' UV primvar for {mesh_data.prototype_path}")

    normals = mesh_data.corner_normals()
    if normals:
        set_custom_normals_compatible(bl_mesh, normals)
    else:
        print(f"  Info: no 'normals' primvar for {mesh_data.prototype_path}; Blender will compute them")

    bl_mesh.validate(verbose=False)
    if bl_mesh.polygons:
        bl_mesh.polygons.foreach_set("use_smooth", [True] * len(bl_mesh.polygons))
    bl_mesh.update()

    # Store the USD provenance on the datablock (Blender 5.x needs the
    # id-properties block to exist before custom properties can be written).
    try:
        bl_mesh.id_properties_ensure()
    except AttributeError:
        pass
    bl_mesh[constants.PROP_USD_PRIM_PATH] = mesh_data.prototype_path
    bl_mesh[constants.PROP_REMIX_MESH_HASH] = mesh_data.prototype_path.rsplit("/", 1)[-1]
    bl_mesh[constants.PROP_SOURCE_LAYER] = mesh_data.orientation
    return bl_mesh
