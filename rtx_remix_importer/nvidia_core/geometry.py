"""Geometry extraction from USD captures for the direct Remix backend.

The mesh traversal follows NVIDIA RTX Remix Toolkit conventions (prototypes
under ``/RootNode/meshes/mesh_<HASH>``, instances under
``/RootNode/instances/inst_<HASH>_<N>``), but the extraction code is written
for Blender: n-gons are preserved, ``orientation = "leftHanded"`` faces are
re-wound instead of being triangulated, and every per-corner attribute stays
aligned with ``faceVertexIndices``.

Portions of the layout and primvar conventions are derived from RTX Remix
Toolkit 1.6.1 (Apache-2.0):

    SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
    SPDX-License-Identifier: Apache-2.0

This module never imports ``bpy``; :mod:`rtx_remix_importer.blender_bridge`
turns the plain data below into Blender datablocks.
"""

from __future__ import annotations

from dataclasses import dataclass

from pxr import Gf, Usd, UsdGeom

__all__ = [
    "InterpolatedAttribute",
    "MeshData",
    "SubsetData",
    "extract_mesh_data",
    "find_mesh_prim",
    "matrix_usd_to_blender",
]

_LEFT_HANDED = "leftHanded"


@dataclass(frozen=True)
class SubsetData:
    """A GeomSubset of a mesh (used by P1 per-face material assignment)."""

    name: str
    family_name: str
    indices: tuple[int, ...]
    element_type: str = "face"


@dataclass(frozen=True)
class InterpolatedAttribute:
    """A USD primvar resolved lazily against a mesh's corner ordering."""

    interpolation: str
    values: tuple
    indices: tuple[int, ...] | None = None

    def resolve(self, corner_index: int, point_index: int, face_index: int, corner_count: int):
        """Return the value for one corner of the mesh.

        Args:
            corner_index: Index into the original (unfiltered) ``faceVertexIndices``.
            point_index: Index of the corner's point.
            face_index: Original face index (for ``uniform`` primvars).
            corner_count: Number of corners in the original mesh.

        Returns:
            The raw value, or ``None`` when the attribute cannot supply one.
        """
        values = self.values
        if not values:
            return None

        if self.interpolation == UsdGeom.Tokens.faceVarying:
            if self.indices is not None:
                if corner_index >= len(self.indices):
                    return None
                value_index = self.indices[corner_index]
            else:
                value_index = corner_index
        elif self.interpolation == UsdGeom.Tokens.vertex:
            value_index = point_index
        elif self.interpolation == UsdGeom.Tokens.uniform:
            value_index = face_index
        else:  # constant
            value_index = 0

        if value_index is None or not 0 <= value_index < len(values):
            return None
        return values[value_index]


@dataclass
class MeshData:
    """Plain-Python geometry extracted from one ``UsdGeom.Mesh`` prim."""

    prototype_path: str
    points: tuple[tuple[float, float, float], ...]
    face_vertex_counts: tuple[int, ...]
    face_vertex_indices: tuple[int, ...]
    # Original corner offsets / face ids of the faces we kept (count >= 3).
    face_corner_offsets: tuple[int, ...]
    face_ids: tuple[int, ...]
    orientation: str = "rightHanded"
    uvs: InterpolatedAttribute | None = None
    normals: InterpolatedAttribute | None = None
    subsets: tuple[SubsetData, ...] = ()
    original_corner_count: int = 0

    @property
    def is_left_handed(self) -> bool:
        return self.orientation == _LEFT_HANDED

    @property
    def total_corners(self) -> int:
        return sum(self.face_vertex_counts)

    def polygons(self) -> list[tuple[int, ...]]:
        """Return faces as tuples, with USD left-handed winding corrected."""
        faces: list[tuple[int, ...]] = []
        offset = 0
        for count in self.face_vertex_counts:
            face = tuple(self.face_vertex_indices[offset : offset + count])
            offset += count
            if self.is_left_handed:
                face = tuple(reversed(face))
            faces.append(face)
        return faces

    def _corner_ordering(self) -> list[tuple[int, int, int]]:
        """Return ``(original_corner, point, original_face)`` in Blender loop order."""
        records: list[tuple[int, int, int]] = []
        for count, face_id, start in zip(self.face_vertex_counts, self.face_ids, self.face_corner_offsets):
            for i in range(count):
                corner = start + i
                records.append((corner, self.face_vertex_indices[corner], face_id))
        if self.is_left_handed:
            reversed_records: list[tuple[int, int, int]] = []
            index = 0
            for count in self.face_vertex_counts:
                reversed_records.extend(reversed(records[index : index + count]))
                index += count
            records = reversed_records
        return records

    def corner_values(self, attribute: InterpolatedAttribute | None, width: int) -> list[tuple[float, ...]] | None:
        """Resolve *attribute* to one value per mesh corner (loop)."""
        if attribute is None:
            return None
        result: list[tuple[float, ...]] = []
        for corner, point, face in self._corner_ordering():
            value = attribute.resolve(corner, point, face, self.original_corner_count)
            if value is None:
                return None
            result.append(tuple(float(component) for component in value[:width]))
        return result

    def corner_uvs(self) -> list[tuple[float, float]] | None:
        """Return one UV per loop, ready for ``Mesh.uv_layers``."""
        return self.corner_values(self.uvs, 2)

    def corner_normals(self) -> list[tuple[float, float, float]] | None:
        """Return one normal per loop, in USD (Y-up) space."""
        return self.corner_values(self.normals, 3)


def _as_tuple(value) -> tuple:
    if value is None:
        return ()
    return tuple(value)


def _get_primvar(prim: Usd.Prim, name: str, time_code: Usd.TimeCode) -> InterpolatedAttribute | None:
    primvars_api = UsdGeom.PrimvarsAPI(prim)
    primvar = primvars_api.GetPrimvar(name)
    if not primvar:
        return None
    values = primvar.Get(time_code)
    if values is None:
        return None
    indices = primvar.GetIndices(time_code)
    interpolation = primvar.GetInterpolation() or UsdGeom.Tokens.constant
    return InterpolatedAttribute(
        interpolation=interpolation,
        values=_as_tuple(values),
        indices=tuple(indices) if indices is not None else None,
    )


def _read_subsets(prim: Usd.Prim, time_code: Usd.TimeCode) -> tuple[SubsetData, ...]:
    subsets = []
    for child in prim.GetChildren():
        if not child.IsA(UsdGeom.Subset):
            continue
        subset = UsdGeom.Subset(child)
        indices = subset.GetIndicesAttr().Get(time_code)
        if indices is None:
            continue
        subsets.append(
            SubsetData(
                name=str(child.GetName()),
                family_name=str(subset.GetFamilyNameAttr().Get(time_code) or "materialBind"),
                indices=tuple(int(i) for i in indices),
                element_type=str(subset.GetElementTypeAttr().Get(time_code) or "face"),
            )
        )
    return tuple(subsets)


def find_mesh_prim(child_prim: Usd.Prim) -> Usd.Prim | None:
    """Find the actual ``UsdGeom.Mesh`` prim within a prototype container.

    Port of the legacy helper, with ``IsA`` checks kept explicit so a container
    whose reference is missing is reported as empty instead of crashing.
    """
    if not child_prim:
        return None
    if child_prim.IsA(UsdGeom.Mesh):
        return child_prim
    if child_prim.IsA(UsdGeom.Xformable):
        for grandchild in child_prim.GetChildren():
            if grandchild.IsA(UsdGeom.Mesh):
                return grandchild
    return None


def extract_mesh_data(
    prim: Usd.Prim,
    time_code: Usd.TimeCode | None = None,
    prototype_path: str | None = None,
) -> MeshData | None:
    """Extract points, faces, UVs and normals from a ``UsdGeom.Mesh`` prim.

    Args:
        prim: The mesh prim (or a prototype container that is a Mesh).
        time_code: Evaluation time; defaults to ``Usd.TimeCode.Default()``.
        prototype_path: Prototype prim path recorded on the result. Defaults
            to ``prim.GetPath()``.

    Returns:
        :class:`MeshData`, or ``None`` when the prim has no usable geometry.
        Degenerate faces (fewer than three vertices) are skipped; the remaining
        faces keep their original n-gon topology.
    """
    time_code = time_code or Usd.TimeCode.Default()
    mesh = UsdGeom.Mesh(prim)
    if not mesh:
        return None

    points = mesh.GetPointsAttr().Get(time_code)
    counts = mesh.GetFaceVertexCountsAttr().Get(time_code)
    indices = mesh.GetFaceVertexIndicesAttr().Get(time_code)
    if not points or not counts or not indices:
        return None

    kept_counts: list[int] = []
    kept_offsets: list[int] = []
    kept_face_ids: list[int] = []
    offset = 0
    for face_index, count in enumerate(counts):
        if count >= 3 and offset + count <= len(indices):
            kept_counts.append(int(count))
            kept_offsets.append(offset)
            kept_face_ids.append(face_index)
        offset += int(count)

    if not kept_counts:
        return None

    orientation = str(mesh.GetOrientationAttr().Get(time_code) or "rightHanded")
    original_corner_count = int(sum(int(count) for count in counts))

    return MeshData(
        prototype_path=prototype_path or str(prim.GetPath()),
        points=tuple((float(p[0]), float(p[1]), float(p[2])) for p in points),
        face_vertex_counts=tuple(kept_counts),
        face_vertex_indices=tuple(int(i) for i in indices),
        face_corner_offsets=tuple(kept_offsets),
        face_ids=tuple(kept_face_ids),
        orientation=orientation,
        uvs=_get_primvar(prim, "st", time_code),
        normals=_get_primvar(prim, "normals", time_code),
        subsets=_read_subsets(prim, time_code),
        original_corner_count=original_corner_count,
    )


def matrix_usd_to_blender(matrix: Gf.Matrix4d):
    """Convert a ``Gf.Matrix4d`` (row-vector, translation in row 3) to mathutils.

    Importing ``mathutils`` here would tie the core to ``bpy``; the caller
    passes the result straight to ``mathutils.Matrix`` (a transpose).
    """
    return (
        (matrix[0][0], matrix[1][0], matrix[2][0], matrix[3][0]),
        (matrix[0][1], matrix[1][1], matrix[2][1], matrix[3][1]),
        (matrix[0][2], matrix[1][2], matrix[2][2], matrix[3][2]),
        (matrix[0][3], matrix[1][3], matrix[2][3], matrix[3][3]),
    )
