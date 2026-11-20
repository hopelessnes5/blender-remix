"""Mesh export rewritten from the RTX Remix Toolkit replacement model.

The Toolkit does two things when a mesh is replaced (asset_replacements
setup.py):

1. It writes a separate USD asset under ``/ReferenceTarget/XForms`` and
   references it from a ``ref_<uuid>`` child of the captured ``mesh_<HASH>``.
2. For a replacement it authors ``references = None`` on the capture prim;
   for an append it keeps the capture reference intact.

A replaced mesh inherits the captured material through USD composition. The
asset therefore carries no ``material:binding`` unless the user explicitly
asks to export the material from Blender nodes.

This module has no dependency on the legacy exporter helpers. The caller
supplies the game stage, the report callback and the material writer.
"""
from __future__ import annotations

import math
import os
import re
import uuid

import bpy
import mathutils

try:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt
except ImportError:  # pxr is supplied by Blender, not by pip
    Gf = Sdf = Usd = UsdGeom = UsdShade = Vt = None

from . import write_guard
from .core_utils import get_corner_normals_compatible, to_mesh_clear_compatible
from .nvidia_core.constants import ROOTNODE_MESHES, ROOTNODE_INSTANCES

_HASH_RE = re.compile(r"(?:mesh|inst)_([0-9A-F]{16})")
_DEFAULT_MATERIAL = "AperturePBR_Opacity.mdl"
_LAYER_TYPE_DATA = "data"


def _set_attr(prim, name, type_name, value, custom=False):
    """Set an attribute, reusing one authored by an earlier export."""
    attr = prim.GetAttribute(name)
    if not attr or not attr.IsValid():
        attr = prim.CreateAttribute(name, type_name, custom=custom)
    attr.Set(value)
    return attr


def _identity_xform(prim):
    """Write Toolkit-style identity xformOps on a prim."""
    _set_attr(prim, "xformOp:translate", Sdf.ValueTypeNames.Double3, (0.0, 0.0, 0.0))
    _set_attr(prim, "xformOp:rotateXYZ", Sdf.ValueTypeNames.Double3, (0.0, 0.0, 0.0))
    _set_attr(prim, "xformOp:scale", Sdf.ValueTypeNames.Double3, (1.0, 1.0, 1.0))
    _set_attr(prim, "xformOpOrder", Sdf.ValueTypeNames.TokenArray,
              ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"])


def _mesh_hash(path):
    match = _HASH_RE.search(str(path or ""))
    return match.group(1) if match else None


def bake_transforms(obj):
    """Apply rotation and scale into mesh data without bpy.ops.

    bpy.ops.object.transform_apply needs an object-mode 3D-view context. The
    export operator can be called from other contexts, so the bake uses the
    data API. Parent transforms are untouched: matrix_basis is local.
    """
    if obj is None or obj.type != "MESH":
        return False
    matrix = obj.matrix_basis.copy()
    matrix.translation = (0.0, 0.0, 0.0)
    if matrix == mathutils.Matrix.Identity(4):
        return False
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    obj.data.transform(matrix, shape_keys=bool(obj.data.shape_keys))
    obj.data.update()
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    obj.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
    obj.scale = (1.0, 1.0, 1.0)
    if "remix_processed" in obj:
        del obj["remix_processed"]
    return True


def open_game_stage(context):
    """Read-only composed mod stage used for capture paths and transforms."""
    try:
        path = bpy.path.abspath(context.scene.remix_mod_file_path or "")
    except Exception:
        path = ""
    if not path or not os.path.exists(path):
        return None
    try:
        return Usd.Stage.Open(path)
    except Exception:
        return None


def _scene_setting(scene, name):
    """Read a scene setting from RNA or legacy ID properties."""
    value = getattr(scene, name, None)
    if value is None:
        try:
            value = scene.get(name, None)
        except Exception:
            value = None
    return value or ""


def _usd_matrix_to_blender(gf_matrix):
    """Row-major Gf.Matrix4d to Blender's column-vector mathutils.Matrix."""
    m = gf_matrix
    return mathutils.Matrix((
        (m[0][0], m[1][0], m[2][0], m[3][0]),
        (m[0][1], m[1][1], m[2][1], m[3][1]),
        (m[0][2], m[1][2], m[2][2], m[3][2]),
        (m[0][3], m[1][3], m[2][3], m[3][3]),
    ))


def _capture_instance_world(instance_path, context):
    """Find an instance transform in the capture files, not in the mod stage.

    The mod stage normally has no capture sublayers. The instance prim lives
    in one of the capture USD files listed in remix_capture_folder_path.
    """
    if not instance_path:
        return None
    try:
        folder = bpy.path.abspath(_scene_setting(context.scene, "remix_capture_folder_path"))
    except Exception:
        folder = ""
    if not folder or not os.path.isdir(folder):
        return None
    # Several captures contain the same hash. Some old captures mirror the
    # instance translation (e.g. -98.56 instead of +98.56). Prefer the newest
    # capture: it is the one the scene was most likely imported from.
    for name in sorted(os.listdir(folder), reverse=True):
        if not name.lower().endswith((".usd", ".usda", ".usdc")):
            continue
        try:
            stage = Usd.Stage.Open(os.path.join(folder, name))
        except Exception:
            continue
        prim = stage.GetPrimAtPath(instance_path) if stage else None
        if prim and prim.IsValid():
            return UsdGeom.XformCache().GetLocalToWorldTransform(prim)
    return None


def resolve_target(context, obj, game_stage):
    """Return ``(mesh_prim_path, instance_world)`` for the replaced mesh.

    Stored USD paths on the object and its mesh data have priority. A hash
    that is absent from the current game stage is still accepted: the mod
    layer may override a prim introduced by another capture.
    """
    candidates = []
    for source in (getattr(context.scene, "remix_anchor_object_target", None), obj):
        if source is None:
            continue
        for key in ("usd_prototype_path", "usd_prim_path", "usd_instance_path", "usd_path"):
            value = source.get(key, None)
            if value:
                candidates.append(str(value))
    if obj is not None and getattr(obj, "data", None):
        value = obj.data.get("remix_mesh_hash", None)
        if value:
            candidates.append(str(value))
    if obj and obj.name:
        candidates.append(obj.name)

    mesh_path = None
    for raw in candidates:
        mesh_hash = _mesh_hash(raw)
        if mesh_hash:
            mesh_path = f"{ROOTNODE_MESHES}/mesh_{mesh_hash}"
            if game_stage is None or game_stage.GetPrimAtPath(mesh_path):
                break

    instance_path = obj.get("usd_instance_path") if obj else None
    instance_world = None
    if instance_path:
        gf_world = None
        if game_stage is not None:
            prim = game_stage.GetPrimAtPath(instance_path)
            if prim and prim.IsValid():
                gf_world = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
        if gf_world is None:
            gf_world = _capture_instance_world(instance_path, context)
        if gf_world is not None:
            instance_world = _usd_matrix_to_blender(gf_world)
    return mesh_path, instance_world


def asset_path_for(context, project_root, obj):
    """Absolute path of the ingested asset for this object.

    If multiple mesh objects resolve to the same base asset filename, disambiguate
    with a deterministic suffix derived from the object name.
    """
    instance_path = obj.get("usd_instance_path") or ""
    mesh_token = _mesh_hash(instance_path)
    if mesh_token:
        base_name = f"mesh_{mesh_token}"
    else:
        base_name = re.sub(r"[^A-Za-z0-9_]", "_", obj.name or "object")

    # Count how many mesh objects in the scene resolve to the same base name.
    collision_count = 0
    try:
        for o in bpy.data.objects:
            if getattr(o, "type", None) != "MESH":
                continue
            o_inst = o.get("usd_instance_path") or ""
            o_tok = _mesh_hash(o_inst)
            if o_tok:
                o_base = f"mesh_{o_tok}"
            else:
                o_base = re.sub(r"[^A-Za-z0-9_]", "_", o.name or "object")
            if o_base == base_name:
                collision_count += 1
    except Exception:
        collision_count = 1

    final_name = base_name
    if collision_count > 1:
        # Deterministic across sessions: stable hash of object name.
        suffix = uuid.uuid5(uuid.NAMESPACE_DNS, obj.name or "object").hex[:8]
        final_name = f"{base_name}_{suffix}"

    folder = context.scene.get("remix_custom_mesh_dir", "") or "assets/ingested"
    return os.path.normpath(os.path.join(project_root, folder, f"{final_name}.usda"))


def capture_display_primvars(context, game_stage, mesh_hash):
    """Read displayColor/displayOpacity from the captured mesh USD.

    The importer currently drops these primvars, so most Blender scenes have
    no color attributes yet. Reading them from the source capture keeps the
    baked-lighting vertex colors on the replacement mesh.
    """
    if not mesh_hash:
        return None, None
    mesh_path = f"{ROOTNODE_MESHES}/mesh_{mesh_hash}"

    def read_from(stage):
        if stage is None:
            return None, None
        mesh_prim = None
        root = stage.GetPrimAtPath(mesh_path)
        if root and root.IsValid():
            for prim in Usd.PrimRange(root):
                if prim.IsA(UsdGeom.Mesh):
                    mesh_prim = prim
                    break
        if mesh_prim is None:
            for prim in stage.Traverse():
                if prim.IsA(UsdGeom.Mesh):
                    mesh_prim = prim
                    break
        if mesh_prim is None:
            return None, None
        api = UsdGeom.PrimvarsAPI(mesh_prim)
        colors = None
        opacity = None
        pv = api.GetPrimvar("displayColor")
        if pv and pv.Get():
            colors = [tuple(float(c) for c in value[:3]) for value in pv.Get()]
        pv = api.GetPrimvar("displayOpacity")
        if pv and pv.Get():
            opacity = [float(value) for value in pv.Get()]
        return colors, opacity

    colors, opacity = read_from(game_stage)
    if colors or opacity:
        return colors, opacity

    try:
        folder = bpy.path.abspath(_scene_setting(context.scene, "remix_capture_folder_path"))
    except Exception:
        folder = ""
    if not folder:
        return None, None
    path = os.path.join(folder, "meshes", f"mesh_{mesh_hash}.usd")
    if not os.path.exists(path):
        return None, None
    return read_from(Usd.Stage.Open(path))


def _mesh_color_attributes(mesh_data, vertex_count):
    """Prefer Blender color attributes, fall back to nothing."""
    colors = None
    opacity = None
    attr = mesh_data.color_attributes.get("displayColor") if hasattr(mesh_data, "color_attributes") else None
    if attr is not None and getattr(attr, "domain", "") == "POINT" and len(attr.data) >= vertex_count:
        colors = [tuple(float(c) for c in item.color[:3]) for item in attr.data[:vertex_count]]
    attr = mesh_data.attributes.get("displayOpacity") if hasattr(mesh_data, "attributes") else None
    if attr is not None and getattr(attr, "domain", "") == "POINT" and len(attr.data) >= vertex_count:
        opacity = [float(item.value) for item in attr.data[:vertex_count]]
    return colors, opacity


def _prepare_asset_layer(stage, context, up_axis):
    """Reset the asset layer and write Remix data-layer metadata."""
    layer = stage.GetRootLayer()
    layer.Clear()
    try:
        game_name = context.scene.remix_game_name
    except Exception:
        game_name = "Unknown Game"
    layer.customLayerData = {
        "lightspeed_game_name": game_name,
        "lightspeed_layer_type": _LAYER_TYPE_DATA,
    }
    layer.comment = "Generated by RTX Remix Toolkit"
    layer.startTimeCode = 0
    layer.endTimeCode = 100
    layer.timeCodesPerSecond = 24
    UsdGeom.SetStageUpAxis(stage, up_axis)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)


def _author_default_material(stage, part_path, part_name):
    """Fallback AperturePBR_Opacity material for an exported mesh part."""
    looks = Sdf.Path("/ReferenceTarget/Looks")
    stage.DefinePrim(looks, "Scope")
    material_path = looks.AppendPath(f"{part_name}__")
    material = UsdShade.Material.Define(stage, material_path)
    shader = UsdShade.Shader.Define(stage, material_path.AppendPath("Shader"))
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("inputs:diffuse_texture", Sdf.ValueTypeNames.Asset).Set(
        Sdf.AssetPath("")
    )
    for output in ("surface", "displacement", "volume"):
        material.CreateOutput(f"mdl:{output}", Sdf.ValueTypeNames.Token).ConnectToSource(
            shader.CreateOutput("out", Sdf.ValueTypeNames.Token)
        )
    shader.GetPrim().CreateAttribute(
        "info:implementationSource", Sdf.ValueTypeNames.Token
    ).Set("sourceAsset")
    shader.GetPrim().CreateAttribute(
        "info:mdl:sourceAsset", Sdf.ValueTypeNames.Asset
    ).Set(Sdf.AssetPath(_DEFAULT_MATERIAL))
    shader.GetPrim().CreateAttribute(
        "info:mdl:sourceAsset:subIdentifier", Sdf.ValueTypeNames.Token
    ).Set("AperturePBR_Opacity")
    UsdShade.MaterialBindingAPI(stage.GetPrimAtPath(part_path)).Bind(material)
    return str(material_path)


def write_asset(stage, context, obj, points_matrix, up_axis, project_root,
                target_sublayer_path, inherit_material, export_material_fn, report,
                game_stage=None):
    """Write the ingested mesh asset: geometry, UV, normals, optional material."""
    _prepare_asset_layer(stage, context, up_axis)

    root = UsdGeom.Xform.Define(stage, "/ReferenceTarget")
    Usd.ModelAPI(root).SetKind("group")
    root.GetPrim().CreateAttribute("visibility", Sdf.ValueTypeNames.Token).Set(
        UsdGeom.Tokens.inherited
    )
    stage.SetDefaultPrim(root.GetPrim())
    xforms = UsdGeom.Xform.Define(stage, "/ReferenceTarget/XForms")
    Usd.ModelAPI(xforms).SetKind("group")
    xforms.GetPrim().CreateAttribute("visibility", Sdf.ValueTypeNames.Token).Set(
        UsdGeom.Tokens.inherited
    )
    _identity_xform(root.GetPrim())
    _identity_xform(xforms.GetPrim())
    stage.DefinePrim("/ReferenceTarget/Looks", "Scope")

    depsgraph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh_data = evaluated.to_mesh()
    try:
        if not mesh_data.vertices or not mesh_data.polygons:
            report({"WARNING"}, f"Skipping {obj.name}: no geometry")
            return None

        normal_matrix = points_matrix.to_3x3().inverted_safe().transposed()
        corner_normals = get_corner_normals_compatible(mesh_data)
        mesh_hash = _mesh_hash(obj.get("usd_instance_path") or obj.name)
        source_colors, source_opacity = capture_display_primvars(context, game_stage, mesh_hash)
        blender_colors, blender_opacity = _mesh_color_attributes(mesh_data, len(mesh_data.vertices))
        colors = blender_colors or source_colors
        opacity = blender_opacity or source_opacity
        slots = evaluated.material_slots
        groups = []
        for poly in mesh_data.polygons:
            material = None
            if not inherit_material:
                index = poly.material_index
                if index < len(slots):
                    material = slots[index].material
                elif slots:
                    material = slots[0].material
                if material is not None:
                    material = material.original
            group = next((item for item in groups if item[0] == material), None)
            if group is None:
                group = (material, [])
                groups.append(group)
            group[1].append(poly)

        asset_mesh_path = None
        material_path = None
        base_name = re.sub(r"[^A-Za-z0-9_]", "_", obj.name)
        for index, (material, polygons) in enumerate(groups):
            part_name = base_name if len(groups) == 1 else f"{base_name}_part_{index + 1}"
            part_path = Sdf.Path("/ReferenceTarget/XForms").AppendPath(part_name)
            mesh_prim = stage.DefinePrim(part_path, "Mesh")
            api = UsdGeom.Mesh(mesh_prim)
            UsdShade.MaterialBindingAPI.Apply(mesh_prim)

            vertex_indices = (
                list(range(len(mesh_data.vertices)))
                if len(groups) == 1
                else list(dict.fromkeys(i for poly in polygons for i in poly.vertices))
            )
            remap = {old: new for new, old in enumerate(vertex_indices)}
            points = []
            for old in vertex_indices:
                point = points_matrix @ mesh_data.vertices[old].co
                points.append(Gf.Vec3f(point[0], point[1], point[2]))
            api.CreatePointsAttr().Set(Vt.Vec3fArray(points))
            api.CreateFaceVertexCountsAttr().Set(
                Vt.IntArray([len(poly.vertices) for poly in polygons])
            )
            api.CreateFaceVertexIndicesAttr().Set(
                Vt.IntArray([remap[i] for poly in polygons for i in poly.vertices])
            )
            api.CreateOrientationAttr().Set(UsdGeom.Tokens.rightHanded)
            api.CreateDoubleSidedAttr().Set(False)
            api.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
            mesh_prim.CreateAttribute("visibility", Sdf.ValueTypeNames.Token).Set(
                UsdGeom.Tokens.inherited
            )
            mesh_prim.CreateAttribute("no_delete", Sdf.ValueTypeNames.Bool, custom=True).Set(True)
            lows = [min(p[a] for p in points) for a in range(3)]
            highs = [max(p[a] for p in points) for a in range(3)]
            api.CreateExtentAttr().Set(
                Vt.Vec3fArray([Gf.Vec3f(*lows), Gf.Vec3f(*highs)])
            )

            primvars = UsdGeom.PrimvarsAPI(mesh_prim)
            if colors and all(0 <= old < len(colors) for old in vertex_indices):
                primvars.CreatePrimvar(
                    "displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex
                ).Set(Vt.Vec3fArray([Gf.Vec3f(*colors[old][:3]) for old in vertex_indices]))
            if opacity and all(0 <= old < len(opacity) for old in vertex_indices):
                primvars.CreatePrimvar(
                    "displayOpacity", Sdf.ValueTypeNames.FloatArray, UsdGeom.Tokens.vertex
                ).Set(Vt.FloatArray([float(opacity[old]) for old in vertex_indices]))

            loops = [i for poly in polygons for i in poly.loop_indices]
            if mesh_data.uv_layers and mesh_data.uv_layers.active:
                uv_data = mesh_data.uv_layers.active.data
                values = [Gf.Vec2f(uv_data[i].uv[0], uv_data[i].uv[1]) for i in loops]
                UsdGeom.PrimvarsAPI(mesh_prim).CreatePrimvar(
                    "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying
                ).Set(Vt.Vec2fArray(values))
            if corner_normals:
                normals = []
                for loop_index in loops:
                    normal = normal_matrix @ mathutils.Vector(corner_normals[loop_index])
                    normals.append(Gf.Vec3f(normal[0], normal[1], normal[2]))
                api.CreateNormalsAttr().Set(Vt.Vec3fArray(normals))
                api.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)

            if asset_mesh_path is None:
                asset_mesh_path = str(part_path)

            if inherit_material:
                continue
            if material is not None and export_material_fn is not None:
                material_path = export_material_fn(
                    material, stage, project_root, target_sublayer_path,
                    material_scope_path="/ReferenceTarget/Looks",
                    bind_prim_path=str(part_path),
                    bind_strength="weakerThanDescendants",
                    material_name=f"{part_name}__",
                )
            else:
                material_path = _author_default_material(stage, str(part_path), part_name)

        stage.GetRootLayer().Save()
        return asset_mesh_path, material_path
    except Exception:
        report({"ERROR"}, f"Could not write mesh asset for {obj.name}")
        raise
    finally:
        to_mesh_clear_compatible(evaluated)


def _remove_spec(layer, path):
    """Delete a prim spec from its parent spec (Sdf.Layer has no RemovePrim)."""
    parent = layer.GetPrimAtPath(Sdf.Path(path).GetParentPath())
    if parent is not None:
        del parent.nameChildren[Sdf.Path(path).name]


def _holder_for(stage, mesh_path, edit_layer, preferred_holder_path=None):
    """Return a Remix ref holder under mesh_path.

    If preferred_holder_path is provided and still exists under mesh_path as a
    Remix ref (IsRemixRef=1), reuse it. Otherwise create a new ref_<uuid> child.
    Never deletes other ref_* siblings or XForms.
    """
    if preferred_holder_path:
        try:
            ph = str(preferred_holder_path)
            prim = stage.GetPrimAtPath(ph)
            if prim and prim.IsValid():
                # Must be a child of mesh_path
                parent = prim.GetParent()
                if parent and str(parent.GetPath()) == str(mesh_path):
                    attr = prim.GetAttribute("IsRemixRef")
                    if attr and attr.IsValid() and attr.Get() is True:
                        return ph
        except Exception:
            pass

    holder_path = f"{mesh_path}/ref_{uuid.uuid4().hex}"
    holder_spec = Sdf.CreatePrimInLayer(edit_layer, Sdf.Path(holder_path))
    holder_spec.specifier = Sdf.SpecifierDef
    holder_spec.typeName = "Xform"
    return holder_path


def author_reference(stage, mesh_path, asset_rel, hide_original, report, preferred_holder_path=None):
    """Author the Toolkit replacement shape in the mod layer.

    hide_original=True  -> over mesh_HASH (references = None) + ref child.
    hide_original=False -> over mesh_HASH (no mask) + ref child: the capture
    geometry stays and the new asset is appended.

    If preferred_holder_path is given, only that holder is modified (reused if valid).
    """
    edit_layer = stage.GetEditTarget().GetLayer()
    write_guard.ensure_writable_layer(edit_layer)
    mesh_spec = edit_layer.GetPrimAtPath(mesh_path)
    if mesh_spec is None:
        mesh_spec = Sdf.CreatePrimInLayer(edit_layer, Sdf.Path(mesh_path))
    # Convert stale `def mesh_HASH` from earlier exports into an `over`.
    mesh_spec.specifier = Sdf.SpecifierOver
    mesh_spec.typeName = ""
    for name in list(mesh_spec.attributes.keys()):
        mesh_spec.RemoveProperty(mesh_spec.attributes[name])
    for relation in list(mesh_spec.relationships.keys()):
        mesh_spec.RemoveProperty(mesh_spec.relationships[relation])

    if hide_original:
        # Usd.References.SetReferences([]) authors an explicit empty list,
        # serialized as `references = None`, just like the Toolkit.
        mesh_prim = stage.GetPrimAtPath(mesh_path)
        mesh_prim.GetReferences().SetReferences([])
    else:
        mesh_spec.referenceList.ClearEdits()

    # The capture prim may carry `visibility = invisible` to hide its original
    # draw call. `inherited` on the asset alone does not override that parent
    # opinion: USD keeps walking up and finds `invisible`. Author the neutral
    # value in the mod layer so the replacement is visible.
    mesh_prim = stage.GetPrimAtPath(mesh_path)
    _set_attr(mesh_prim, "visibility", Sdf.ValueTypeNames.Token, UsdGeom.Tokens.inherited)

    holder_path = _holder_for(stage, mesh_path, edit_layer, preferred_holder_path=preferred_holder_path)
    holder = stage.GetPrimAtPath(holder_path)
    _set_attr(holder, "IsRemixRef", Sdf.ValueTypeNames.Bool, True, custom=True)
    _identity_xform(holder)
    holder.GetReferences().ClearReferences()
    holder.GetReferences().AddReference(assetPath=asset_rel.replace("\\", "/"))
    report(
        {"INFO"},
        f"  Mesh {'replaced' if hide_original else 'appended'}: {mesh_path} -> @{asset_rel}@",
    )
    return holder_path


def author_new_mesh(stage, asset_rel, report):
    """Add a brand new mesh prototype and instance when no target exists."""
    mesh_hash = uuid.uuid4().hex[:16].upper()
    mesh_path = f"{ROOTNODE_MESHES}/mesh_{mesh_hash}"
    instance_path = f"{ROOTNODE_INSTANCES}/inst_{mesh_hash}_0"
    edit_layer = stage.GetEditTarget().GetLayer()
    write_guard.ensure_writable_layer(edit_layer)

    mesh_prim = stage.DefinePrim(mesh_path, "Xform")
    Usd.ModelAPI(mesh_prim).SetKind("assembly")
    _identity_xform(mesh_prim)
    holder = stage.DefinePrim(f"{mesh_path}/ref_{uuid.uuid4().hex}", "Xform")
    _set_attr(holder, "IsRemixRef", Sdf.ValueTypeNames.Bool, True, custom=True)
    _identity_xform(holder)
    holder.GetReferences().AddReference(assetPath=asset_rel.replace("\\", "/"))

    instance = stage.DefinePrim(instance_path, "Xform")
    _identity_xform(instance)
    instance.GetReferences().AddReference(primPath=Sdf.Path(mesh_path))
    report({"INFO"}, f"  New Remix mesh {mesh_path} and instance {instance_path}")
    return mesh_path, instance_path


def export_mesh(context, obj, sublayer_stage, project_root, target_sublayer_path,
                game_stage, report, export_material_fn=None):
    """Full mesh export: asset, then replacement/append reference in mod layer."""
    if obj is None or obj.type != "MESH":
        return False

    scale = context.scene.remix_export_scale or 1.0
    if getattr(context.scene, "remix_auto_apply_transforms", False):
        bake_transforms(obj)

    mesh_path, instance_world = resolve_target(context, obj, game_stage)
    points_matrix = mathutils.Matrix.Scale(1.0 / scale, 4) @ obj.matrix_world
    if instance_world is not None:
        points_matrix = get_safe_matrix_inverse(instance_world) @ points_matrix

    # Per-object binding rule overrides (fallback to scene defaults).
    obj_inherit = obj.get(
        "remix_inherit_parent_material",
        getattr(context.scene, "remix_inherit_parent_material", True),
    )
    obj_hide = obj.get(
        "remix_hide_original_mesh",
        getattr(context.scene, "remix_hide_original_mesh", True),
    )

    asset_path = asset_path_for(context, project_root, obj)
    asset_stage = Usd.Stage.Open(asset_path) if os.path.exists(asset_path) else Usd.Stage.CreateNew(asset_path)
    write_guard.ensure_writable_layer(asset_stage.GetRootLayer(), open_path=asset_path)
    up_axis = UsdGeom.GetStageUpAxis(game_stage) if game_stage else UsdGeom.Tokens.z
    written = write_asset(
        asset_stage, context, obj, points_matrix, up_axis, project_root,
        target_sublayer_path, obj_inherit, export_material_fn, report,
        game_stage=game_stage,
    )
    if written is None:
        return False
    asset_mesh_path, material_path = written

    relative = os.path.relpath(asset_path, os.path.dirname(target_sublayer_path)).replace("\\", "/")
    if not relative.startswith("."):
        relative = "./" + relative
    if mesh_path:
        # Mesh-prim-level hide_original is aggregated across all Blender objects
        # assigned to this mesh_path: if ANY assigned object requests hide, the
        # mesh prim authors references=None (replace capture); otherwise reference
        # edits are cleared (append/keep capture). Recomputed from objects whose
        # remix_reference_prim_path is under mesh_path on each authoring pass.
        agg_hide = bool(obj_hide)
        try:
            mprefix = str(mesh_path) + "/"
            for o in bpy.data.objects:
                if o == obj:
                    continue
                if getattr(o, "type", None) != "MESH":
                    continue
                holderp = o.get("remix_reference_prim_path")
                if holderp and isinstance(holderp, str) and holderp.startswith(mprefix):
                    ohide = o.get(
                        "remix_hide_original_mesh",
                        getattr(context.scene, "remix_hide_original_mesh", True),
                    )
                    if ohide:
                        agg_hide = True
                        break
        except Exception:
            agg_hide = bool(obj_hide)
        holder = author_reference(
            sublayer_stage, mesh_path, relative, agg_hide, report,
            preferred_holder_path=obj.get("remix_reference_prim_path"),
        )
        obj["remix_reference_prim_path"] = holder
    else:
        mesh_path, _ = author_new_mesh(sublayer_stage, relative, report)
        obj["remix_mesh_prim_path"] = mesh_path
    sublayer_stage.GetRootLayer().Save()
    obj["remix_processed"] = True
    obj["remix_mesh_file_path"] = asset_path
    obj["remix_material_path"] = str(material_path) if material_path else ""
    return True


def get_safe_matrix_inverse(matrix):
    """Inverse that falls back to identity for singular matrices."""
    try:
        return matrix.inverted()
    except ValueError:
        return mathutils.Matrix.Identity(4)
