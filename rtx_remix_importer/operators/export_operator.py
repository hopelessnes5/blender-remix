import bpy
import os
import shutil
import tempfile
import subprocess
import bmesh
import mathutils
import math
import traceback
from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy.types import Operator

try:
    from pxr import Usd, UsdGeom, UsdShade, UsdLux, Sdf, Vt, Gf
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False

from .. import constants
from ..core_utils import (
    calc_normals_split_compatible,
    get_corner_normals_compatible,
    to_mesh_clear_compatible,
)

# --- Helper Functions ---

def get_safe_matrix_inverse(matrix):
    """Safely inverts a 4x4 matrix, handling zero-scale axes (flat decals, roads)."""
    try:
        if abs(matrix.determinant()) > 1e-8:
            return matrix.inverted()
    except Exception:
        pass
    
    loc, rot, sc = matrix.decompose()
    safe_sc = mathutils.Vector((
        sc.x if abs(sc.x) > 1e-6 else 1.0,
        sc.y if abs(sc.y) > 1e-6 else 1.0,
        sc.z if abs(sc.z) > 1e-6 else 1.0
    ))
    safe_matrix = mathutils.Matrix.LocRotScale(loc, rot, safe_sc)
    return safe_matrix.inverted()

def get_relative_path(from_path, to_path):
    """Calculates clean relative path starting with ./ or ../ depending on sublayer directory depth."""
    from_path_abs = os.path.abspath(from_path)
    if os.path.isfile(from_path_abs) or '.' in os.path.basename(from_path_abs):
        from_dir = os.path.dirname(from_path_abs)
    else:
        from_dir = from_path_abs

    rel = os.path.relpath(os.path.abspath(to_path), from_dir).replace('\\', '/')
    if not rel.startswith(('.', '/')):
        rel = f"./{rel}"
    return rel

def generate_uuid_name(name, prefix="ref_"):
    from .. import core_utils
    return core_utils.generate_uuid_name(name, prefix)

def sanitize_prim_name(name):
    from .. import core_utils
    return core_utils.sanitize_prim_name(name)

def extract_base_material_name(material_name):
    parts = material_name.split('_')
    base_parts = []
    for part in parts:
        if len(part) == 8 and all(c in '0123456789abcdefABCDEF' for c in part):
            break
        base_parts.append(part)
    if len(base_parts) == len(parts):
        return material_name
    return '_'.join(base_parts)

def find_existing_texture_for_base_material(base_material_name, texture_type, textures_dir, texture_processor):
    if not os.path.exists(textures_dir):
        return None
    
    type_suffix = texture_processor.get_texture_suffix(texture_type)
    if base_material_name.startswith('mat_'):
        texture_base = base_material_name[4:]
    else:
        texture_base = base_material_name
    
    expected_filename = f"{texture_base}{type_suffix}.dds"
    expected_path = os.path.join(textures_dir, expected_filename)
    if os.path.exists(expected_path):
        return expected_path
    
    try:
        for filename in os.listdir(textures_dir):
            if filename.startswith(texture_base) and filename.endswith(f"{type_suffix}.dds"):
                return os.path.join(textures_dir, filename)
    except OSError:
        pass
    return None

def ensure_mdl_files(project_root):
    addon_dir = os.path.dirname(os.path.dirname(__file__))
    mdl_source_dir = os.path.join(addon_dir, "materials")
    mdl_target_dir = os.path.join(project_root, "assets", "remix_materials")
    os.makedirs(mdl_target_dir, exist_ok=True)
    
    if os.path.exists(mdl_source_dir):
        for mdl_file in os.listdir(mdl_source_dir):
            if mdl_file.endswith(".mdl"):
                source_file = os.path.join(mdl_source_dir, mdl_file)
                target_file = os.path.join(mdl_target_dir, mdl_file)
                if not os.path.exists(target_file) or os.path.getmtime(source_file) > os.path.getmtime(target_file):
                    shutil.copy2(source_file, target_file)
    return mdl_target_dir

def resolve_anchor_prim_path(anchor_obj):
    if not anchor_obj:
        return None
    raw_path = anchor_obj.get("usd_prim_path", None)
    if not raw_path:
        raw_path = anchor_obj.get("usd_instance_path", None)
    if not raw_path:
        raw_path = anchor_obj.get("usd_path", None)
    if not raw_path or not isinstance(raw_path, str) or not raw_path.startswith('/'):
        return None
    path_parts = raw_path.split('/')
    for i, part in enumerate(path_parts):
        if part.startswith('mesh_'):
            return '/'.join(path_parts[:i+1])
    return raw_path


# --- RTX Remix naming / authoring constants ---------------------------------
# Mirrors `lightspeed.common.constants` and
# `lightspeed.trex.asset_replacements.core.shared` so the files written here are
# structurally identical to the ones produced by the official Toolkit.
REMIX_MESH_PREFIX = "mesh_"
REMIX_INSTANCE_PREFIX = "inst_"
REMIX_REF_PREFIX = "ref_"
REMIX_IS_REF_ATTR = "IsRemixRef"
REMIX_ROOT_NODE = "/RootNode"
REMIX_MESHES_PATH = REMIX_ROOT_NODE + "/meshes"
REMIX_INSTANCES_PATH = REMIX_ROOT_NODE + "/instances"
REMIX_LOOKS_PATH = REMIX_ROOT_NODE + "/Looks"
REMIX_REFERENCE_TARGET = "/ReferenceTarget"
# Ingested mesh asset layout: /ReferenceTarget/Looks/<material> and
# /ReferenceTarget/XForms/<mesh> (see asset_pipeline meta step).
ASSET_LOOKS_PATH = REMIX_REFERENCE_TARGET + "/Looks"

# Remix asset hashes are 16 uppercase hex characters captured from the game,
# e.g. `mesh_1A2B3C4D5E6F7A8B` / `inst_1A2B3C4D5E6F7A8B_0`.
_REMIX_HASH_RE = re.compile(r"^(?:mesh|inst|light|mat)_([A-Z0-9]{16})(?:_[0-9]+)?$")

_GAME_STAGE_CACHE = {}


def remix_new_hash():
    """Return a new 16 character uppercase hex hash as used in Remix prim names."""
    import uuid
    return uuid.uuid4().hex[:16].upper()


def remix_new_ref_name():
    """Return a new `ref_<32 hex>` holder name, as the Toolkit authors it."""
    import uuid
    return REMIX_REF_PREFIX + uuid.uuid4().hex


def remix_hash_from_prim_path(prim_path):
    """Extract the 16 character Remix hash from a prim path, if there is one."""
    if not prim_path:
        return None
    for part in str(prim_path).split("/"):
        match = _REMIX_HASH_RE.match(part)
        if match:
            return match.group(1)
    return None


def remix_mesh_prim_path(prim_path):
    """Resolve the Remix mesh prototype prim (`/RootNode/meshes/mesh_<HASH>`).

    Accepts a prototype path, an instance path (`inst_<HASH>_N`) or any prim
    below one of them. Returns None when the path carries no Remix hash.
    """
    hash_str = remix_hash_from_prim_path(prim_path)
    if not hash_str:
        return None
    return "%s/%s%s" % (REMIX_MESHES_PATH, REMIX_MESH_PREFIX, hash_str)


def usd_matrix_to_blender(gf_matrix):
    """Convert a row major `Gf.Matrix4d` into a Blender (column vector) matrix."""
    m = gf_matrix
    return mathutils.Matrix((
        (m[0][0], m[1][0], m[2][0], m[3][0]),
        (m[0][1], m[1][1], m[2][1], m[3][1]),
        (m[0][2], m[1][2], m[2][2], m[3][2]),
        (m[0][3], m[1][3], m[2][3], m[3][3]),
    ))


def get_game_stage(context):
    """Open and cache the stage of the loaded mod file.

    The export target can be a sublayer opened on its own, but the capture layer
    (and with it every captured transform) only exists in the composed mod
    stage. That stage is queried read only.
    """
    try:
        mod_path = bpy.path.abspath(context.scene.remix_mod_file_path or "")
    except Exception:
        mod_path = ""
    if not mod_path or not os.path.exists(mod_path):
        return None
    key = os.path.normcase(os.path.abspath(mod_path))
    if key in _GAME_STAGE_CACHE:
        return _GAME_STAGE_CACHE[key]
    stage = None
    try:
        stage = Usd.Stage.Open(mod_path)
    except Exception as e:
        print("  WARNING: could not open mod stage %s: %s" % (mod_path, e))
    _GAME_STAGE_CACHE[key] = stage
    return stage


def get_prim_world_matrix(stage, prim_path):
    """World matrix of a prim converted to Blender space, or None."""
    if stage is None or not prim_path:
        return None
    try:
        prim = stage.GetPrimAtPath(prim_path)
        if not prim:
            return None
        cache = UsdGeom.XformCache(Usd.TimeCode.Default())
        return usd_matrix_to_blender(cache.GetLocalToWorldTransform(prim))
    except Exception as e:
        print("  WARNING: could not read the transform of %s: %s" % (prim_path, e))
        return None


def set_identity_remix_xform(prim):
    """Author the identity translate/rotateXYZ/scale trio Remix replacements use."""
    xformable = UsdGeom.Xformable(prim)
    xformable.ClearXformOpOrder()
    translate_op = xformable.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble)
    translate_op.Set(Gf.Vec3d(0.0, 0.0, 0.0))
    rotate_op = xformable.AddRotateXYZOp(UsdGeom.XformOp.PrecisionFloat)
    rotate_op.Set(Gf.Vec3f(0.0, 0.0, 0.0))
    scale_op = xformable.AddScaleOp(UsdGeom.XformOp.PrecisionFloat)
    scale_op.Set(Gf.Vec3f(1.0, 1.0, 1.0))
    xformable.SetXformOpOrder([translate_op, rotate_op, scale_op])


def bind_material_on_prim(prim, material_prim, strength="weakerThanDescendants"):
    """Bind a material the way USD stores it: `bindMaterialAs` is metadata on the
    `material:binding` relationship, not an attribute."""
    if not prim or not material_prim:
        return False
    UsdShade.MaterialBindingAPI.Apply(prim)
    binding_strength = getattr(UsdShade.Tokens, strength, None)
    if binding_strength is None:
        binding_strength = UsdShade.Tokens.weakerThanDescendants
    UsdShade.MaterialBindingAPI(prim).Bind(
        UsdShade.Material(material_prim), bindingStrength=binding_strength
    )
    return True


def resolve_replacement_target(context, obj, anchor_obj=None):
    """Resolve the captured Remix mesh an exported object replaces.

    Returns `(game_stage, mesh_prim_path, instance_world_matrix)`.
    `mesh_prim_path` is None when the object has to be added as a new Remix mesh.
    """
    game_stage = get_game_stage(context)
    candidates = []
    if anchor_obj is not None:
        candidates.append(("target mesh", resolve_anchor_prim_path(anchor_obj)))
    if obj is not None:
        candidates.append(("object", resolve_anchor_prim_path(obj)))

    for source, raw_path in candidates:
        mesh_path = remix_mesh_prim_path(raw_path)
        if not mesh_path:
            continue
        if game_stage is not None and not game_stage.GetPrimAtPath(mesh_path):
            print("  %s prim %s is not part of the loaded mod - skipping" % (source, mesh_path))
            continue
        instance_world = None
        if obj is not None:
            instance_path = obj.get("usd_instance_path")
            if instance_path:
                instance_world = get_prim_world_matrix(game_stage, instance_path)
        print("  Using %s prim %s as the replacement target" % (source, mesh_path))
        return game_stage, mesh_path, instance_world

    return game_stage, None, None


def _remove_prim_spec(layer, prim_path):
    """Delete a prim spec (and its subtree) from a layer."""
    try:
        parent_spec = layer.GetPrimAtPath(Sdf.Path(prim_path).GetParentPath())
        if parent_spec is not None:
            del parent_spec.nameChildren[Sdf.Path(prim_path).name]
            return True
    except Exception as e:
        print("  WARNING: could not remove %s from %s: %s" % (prim_path, layer.identifier, e))
    return False


def _drop_stale_holder_overrides(edit_layer, holder_path):
    """Remove overrides written by older versions of this add-on under a holder.

    Earlier versions stored the placement of the replacement as
    `over <holder>/XForms { xformOp... }`; that would displace the geometry once
    the placement is authored properly, so it has to go.
    """
    holder_spec = edit_layer.GetPrimAtPath(Sdf.Path(holder_path))
    if holder_spec is None:
        return
    stale = [child.path for child in holder_spec.nameChildren if child.name == "XForms"]
    for path in stale:
        print("  Removing stale override %s written by an earlier version" % path)
        _remove_prim_spec(edit_layer, path)


def _reuse_or_create_ref_holder(stage, mesh_path, edit_layer):
    """Return the `ref_...` child of a mesh prim, reusing what is already there.

    Repeated exports must not stack holders up: every extra `ref_` child would
    render another copy of the replacement geometry.
    """
    mesh_spec = edit_layer.GetPrimAtPath(mesh_path)
    existing = []
    stale_mesh_xforms = None
    if mesh_spec is not None:
        for child in mesh_spec.nameChildren:
            if child.name.startswith(REMIX_REF_PREFIX):
                existing.append(child.path)
            elif child.name == "XForms":
                stale_mesh_xforms = child.path

    if stale_mesh_xforms is not None:
        # `XForms` directly under a mesh prim only ever came from older versions
        # of this add-on; it does not exist in Remix captures.
        print("  Removing stale override %s written by an earlier version" % stale_mesh_xforms)
        _remove_prim_spec(edit_layer, stale_mesh_xforms)

    if existing:
        holder_path = existing[0]
        for extra in existing[1:]:
            print("  Removing duplicate replacement holder %s" % extra)
            _remove_prim_spec(edit_layer, extra)
        print("  Reusing existing replacement holder %s" % holder_path)
        return holder_path

    holder_path = mesh_path.AppendPath(remix_new_ref_name())
    while stage.GetPrimAtPath(holder_path):
        holder_path = mesh_path.AppendPath(remix_new_ref_name())
    stage.DefinePrim(holder_path, "Xform")
    return holder_path


def author_mesh_replacement(stage, mesh_prim_path, asset_rel_path, report_fn=print, hide_original=True):
    """Mask a captured mesh reference and add the Remix `ref_` holder prim.

    This is what
    `lightspeed.trex.asset_replacements.core.shared.Setup.add_new_reference`
    does:

        over "mesh_<HASH>" ( references = None )
        {
            def Xform "ref_<uuid4hex>" ( prepend references = @asset@ )
            {
                custom bool IsRemixRef = 1
                <identity translate/rotateXYZ/scale + xformOpOrder>
            }
        }

    `references = None` masks the capture layer's reference to the captured mesh
    data, the child `ref_` prim is what Remix and the Toolkit treat as the
    replacement, and because it is a child of the captured mesh prim it inherits
    every instance transform for free.

    Returns the holder prim path, or None on failure.
    """
    mesh_path = Sdf.Path(mesh_prim_path)
    over_prim = stage.OverridePrim(mesh_path)
    if not over_prim:
        report_fn({'ERROR'}, "Failed to override mesh prim %s" % mesh_path)
        return None

    if hide_original:
        # Explicit empty reference list: prints as `references = None` and masks
        # the weaker layer's reference. Note that `ClearReferences()` would only
        # drop edits of the current layer and would not mask anything.
        over_prim.GetReferences().SetReferences([])
    else:
        print("  WARNING: 'Hide Target Mesh' is off - the captured geometry will still render")

    edit_layer = stage.GetEditTarget().GetLayer()
    holder_path = _reuse_or_create_ref_holder(stage, mesh_path, edit_layer)
    _drop_stale_holder_overrides(edit_layer, holder_path)

    holder_prim = stage.GetPrimAtPath(holder_path)
    if not holder_prim:
        report_fn({'ERROR'}, "Failed to create the replacement holder %s" % holder_path)
        return None

    # Drop whatever this layer authored on the holder before, then prepend the
    # new reference (prints as `prepend references = @...@`, like the Toolkit).
    holder_prim.GetReferences().ClearReferences()
    holder_prim.GetReferences().AddReference(assetPath=asset_rel_path)
    holder_prim.CreateAttribute(
        REMIX_IS_REF_ATTR, Sdf.ValueTypeNames.Bool, custom=True
    ).Set(True)
    set_identity_remix_xform(holder_prim)

    print("  Authored replacement %s -> %s = @%s@" % (mesh_path, holder_path, asset_rel_path))
    return holder_path


def author_new_remix_mesh(stage, asset_rel_path, report_fn=print):
    """Add a brand new Remix mesh: `mesh_<HASH>` prototype + `inst_<HASH>_0`.

    Remix only draws geometry that is placed by an `inst_...` prim referencing a
    `mesh_...` prototype, which is the layout captures use.
    """
    hash_str = remix_new_hash()

    stage.OverridePrim(Sdf.Path(REMIX_MESHES_PATH))
    stage.OverridePrim(Sdf.Path(REMIX_INSTANCES_PATH))

    mesh_path = Sdf.Path("%s/%s%s" % (REMIX_MESHES_PATH, REMIX_MESH_PREFIX, hash_str))
    mesh_prim = stage.DefinePrim(mesh_path, "Xform")
    if not mesh_prim:
        report_fn({'ERROR'}, "Failed to define %s" % mesh_path)
        return None
    Usd.ModelAPI(mesh_prim).SetKind("assembly")
    set_identity_remix_xform(mesh_prim)

    holder_path = mesh_path.AppendPath(remix_new_ref_name())
    holder_prim = stage.DefinePrim(holder_path, "Xform")
    holder_prim.GetReferences().AddReference(assetPath=asset_rel_path)
    holder_prim.CreateAttribute(
        REMIX_IS_REF_ATTR, Sdf.ValueTypeNames.Bool, custom=True
    ).Set(True)
    set_identity_remix_xform(holder_prim)

    instance_path = Sdf.Path("%s/%s%s_0" % (REMIX_INSTANCES_PATH, REMIX_INSTANCE_PREFIX, hash_str))
    instance_prim = stage.DefinePrim(instance_path, "Xform")
    instance_prim.GetReferences().AddInternalReference(mesh_path)
    set_identity_remix_xform(instance_prim)

    print("  Added new Remix mesh     %s (reference @%s@)" % (mesh_path, asset_rel_path))
    print("  Added new Remix instance %s" % instance_path)
    return mesh_path


def _prepare_ingested_asset_layer(stage, context, up_axis):
    """Reset an ingested mesh asset layer and apply the Remix layer metadata."""
    layer = stage.GetRootLayer()
    layer.Clear()
    try:
        game_name = context.scene.remix_game_name
    except Exception:
        game_name = "Unknown Game"
    layer.customLayerData = {
        'lightspeed_game_name': game_name,
        'lightspeed_layer_type': "data",
    }
    layer.comment = "Generated by RTX Remix Toolkit"
    layer.startTimeCode = 0
    layer.endTimeCode = 100
    layer.timeCodesPerSecond = 24
    UsdGeom.SetStageUpAxis(stage, up_axis)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)


def create_asset_default_material(stage, mesh_prim_path, mesh_name):
    """Author the fallback AperturePBR_Opacity material an ingested mesh needs.

    The ingest pipeline always guarantees a bound AperturePBR material
    (`DefaultMaterial` / `MaterialShaders` in model_ingestion.json).
    """
    mat_path = Sdf.Path(ASSET_LOOKS_PATH).AppendPath("%s__" % mesh_name)
    material_prim = stage.DefinePrim(mat_path, "Material")
    shader_prim = stage.DefinePrim(mat_path.AppendPath("Shader"), "Shader")

    shader_prim.CreateAttribute("info:implementationSource", Sdf.ValueTypeNames.Token).Set("sourceAsset")
    shader_prim.CreateAttribute("info:mdl:sourceAsset", Sdf.ValueTypeNames.Asset).Set(
        Sdf.AssetPath("AperturePBR_Opacity.mdl")
    )
    shader_prim.CreateAttribute("info:mdl:sourceAsset:subIdentifier", Sdf.ValueTypeNames.Token).Set(
        "AperturePBR_Opacity"
    )

    shader_output = UsdShade.Shader(shader_prim).CreateOutput("out", Sdf.ValueTypeNames.Token)
    shader_output.SetRenderType("material")

    material_api = UsdShade.Material(material_prim)
    material_api.CreateSurfaceOutput("mdl").ConnectToSource(shader_output)
    material_api.CreateDisplacementOutput("mdl").ConnectToSource(shader_output)
    material_api.CreateVolumeOutput("mdl").ConnectToSource(shader_output)

    mesh_prim = stage.GetPrimAtPath(mesh_prim_path)
    if mesh_prim:
        bind_material_on_prim(mesh_prim, material_prim)
    print("  Defined default material %s" % mat_path)
    return mat_path


def write_ingested_mesh_asset(stage, obj, context, mesh_name_sanitized, points_matrix, bl_mat,
                              inherit_material, project_root, mesh_file_path, up_axis,
                              report_fn=print):
    """Write an ingested mesh asset the way the Remix ingest pipeline does.

    Structure (asset_pipeline meta step + model_ingestion.json):

        /ReferenceTarget            Xform, kind=group, defaultPrim
            /Looks                  Scope
                /<name>__           Material + Shader
            /XForms                 Xform, kind=group
                /<mesh name>        Mesh, MaterialBindingAPI, bound to the material

    Returns `(mesh_prim_path, material_path)` or None on failure.
    """
    _prepare_ingested_asset_layer(stage, context, up_axis)

    ref_target_path = Sdf.Path(REMIX_REFERENCE_TARGET)
    xforms_path = ref_target_path.AppendPath("XForms")
    looks_path = Sdf.Path(ASSET_LOOKS_PATH)
    mesh_prim_path = xforms_path.AppendPath(mesh_name_sanitized)

    ref_target_prim = UsdGeom.Xform.Define(stage, ref_target_path).GetPrim()
    Usd.ModelAPI(ref_target_prim).SetKind("group")
    set_identity_remix_xform(ref_target_prim)

    xforms_prim = UsdGeom.Xform.Define(stage, xforms_path).GetPrim()
    Usd.ModelAPI(xforms_prim).SetKind("group")
    set_identity_remix_xform(xforms_prim)

    stage.DefinePrim(looks_path, "Scope")

    mesh_prim = stage.DefinePrim(mesh_prim_path, "Mesh")
    if not mesh_prim:
        report_fn({'ERROR'}, "Failed to define mesh prim %s" % mesh_prim_path)
        return None
    mesh_api = UsdGeom.Mesh(mesh_prim)
    UsdShade.MaterialBindingAPI.Apply(mesh_prim)

    try:
        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        mesh_data = eval_obj.to_mesh()
    except Exception as e:
        report_fn({'ERROR'}, "Failed to get evaluated mesh for %s: %s" % (obj.name, e))
        return None

    try:
        if not mesh_data.vertices or not mesh_data.polygons:
            report_fn({'WARNING'}, "Skipping mesh %s: no vertices or polygons." % obj.name)
            return None

        normal_matrix = points_matrix.to_3x3().inverted_safe().transposed()

        points = []
        for vertex in mesh_data.vertices:
            co = points_matrix @ vertex.co
            points.append((co[0], co[1], co[2]))
        mesh_api.CreatePointsAttr().Set(Vt.Vec3fArray(points))

        face_vertex_counts = [len(poly.vertices) for poly in mesh_data.polygons]
        face_vertex_indices = [index for poly in mesh_data.polygons for index in poly.vertices]
        mesh_api.CreateFaceVertexCountsAttr().Set(Vt.IntArray(face_vertex_counts))
        mesh_api.CreateFaceVertexIndicesAttr().Set(Vt.IntArray(face_vertex_indices))
        mesh_api.CreateOrientationAttr().Set(UsdGeom.Tokens.rightHanded)
        mesh_api.CreateDoubleSidedAttr().Set(False)
        # Without an authored subdivisionScheme USD falls back to catmullClark
        # and the replacement geometry gets subdivided in any conforming consumer.
        mesh_api.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
        if points:
            extent_min = Gf.Vec3f(*[min(p[axis] for p in points) for axis in range(3)])
            extent_max = Gf.Vec3f(*[max(p[axis] for p in points) for axis in range(3)])
            mesh_api.CreateExtentAttr().Set(Vt.Vec3fArray([extent_min, extent_max]))

        if mesh_data.uv_layers and mesh_data.uv_layers.active:
            active_uv_layer = mesh_data.uv_layers.active
            uv_values = []
            for loop_index in range(len(mesh_data.loops)):
                uv = active_uv_layer.data[loop_index].uv
                uv_values.append((uv[0], uv[1]))
            if uv_values:
                uv_primvar = UsdGeom.PrimvarsAPI(mesh_prim).CreatePrimvar(
                    "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying
                )
                uv_primvar.Set(Vt.Vec2fArray(uv_values))

        corner_normals = get_corner_normals_compatible(mesh_data)
        if corner_normals:
            normal_values = []
            for normal in corner_normals:
                n = normal_matrix @ mathutils.Vector((normal[0], normal[1], normal[2]))
                normal_values.append((n[0], n[1], n[2]))
            mesh_api.CreateNormalsAttr().Set(Vt.Vec3fArray(normal_values))
            mesh_api.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
        else:
            print("  Could not read corner normals - Remix will compute them.")

        material_path = None
        if inherit_material:
            print("  [Inherit Parent Material] no material is written into the asset")
        else:
            if bl_mat is not None:
                material_path = export_material(
                    bl_mat,
                    stage,
                    project_root,
                    mesh_file_path,
                    obj=obj,
                    material_scope_path=looks_path,
                    bind_prim_path=mesh_prim_path,
                    bind_strength="weakerThanDescendants",
                    material_name="%s__" % mesh_name_sanitized,
                )
            if not material_path:
                material_path = create_asset_default_material(stage, mesh_prim_path, mesh_name_sanitized)

        stage.SetDefaultPrim(ref_target_prim)
        stage.GetRootLayer().Save()
        print("  Saved mesh data to: %s" % mesh_file_path)
        return mesh_prim_path, material_path
    except Exception as e:
        report_fn({'ERROR'}, "Failed to write mesh asset %s: %s" % (mesh_file_path, e))
        traceback.print_exc()
        return None
    finally:
        to_mesh_clear_compatible(eval_obj)


# --- Material Export Helper ---

def get_mesh_name_from_object(obj):
    if not obj:
        return "Suzanne"
    mesh_name_to_use = obj.name
    if "usd_instance_path" in obj:
        usd_path = obj["usd_instance_path"]
        path_parts = usd_path.split('/')
        if len(path_parts) >= 2:
            for part in path_parts:
                if part.startswith('mesh_'):
                    mesh_name_to_use = part
                    break
    if mesh_name_to_use == obj.name and obj.type == 'MESH' and obj.data:
        mesh_data_name = obj.data.name
        if mesh_data_name.endswith('_data'):
            mesh_name_to_use = mesh_data_name[:-5]
        else:
            mesh_name_to_use = mesh_data_name
    return mesh_name_to_use

def export_material(blender_material, sublayer_stage, project_root, sublayer_path, parent_mesh_path=None, obj=None,
                    material_scope_path=None, bind_prim_path=None,
                    bind_strength="weakerThanDescendants", material_name=None):
    """Export a Blender material into `sublayer_stage`.

    Two destinations are supported, both matching what the RTX Remix Toolkit
    writes:

    * `material_scope_path` + `bind_prim_path` - material that belongs to an
      ingested mesh asset (`<asset>/ReferenceTarget/Looks/<name>`, bound on the
      asset mesh). This is where the material of a replacement mesh lives.
    * otherwise - a global `/RootNode/Looks/mat_<uuid>` material, used by the
      material replacement flow and by lights.
    """
    if not blender_material:
        return None

    ensure_mdl_files(project_root)

    if material_scope_path is not None:
        scope_path = Sdf.Path(material_scope_path)
        if not sublayer_stage.GetPrimAtPath(scope_path):
            sublayer_stage.DefinePrim(scope_path, "Scope")
        mat_name_sanitized = sanitize_prim_name(material_name or blender_material.name) or "Material"
        print(f"Exporting Material to {scope_path}: {blender_material.name}")
    else:
        scope_path = Sdf.Path(REMIX_LOOKS_PATH)
        sublayer_stage.OverridePrim(scope_path)
        mat_name_sanitized = generate_uuid_name(sanitize_prim_name(blender_material.name), prefix="mat_")
        print(f"Exporting Material to {scope_path}: {blender_material.name}")

    mat_path = scope_path.AppendPath(mat_name_sanitized)
    # Inside an asset every prim has to be *defined*, an `over` alone would not
    # compose once the asset is referenced.
    if material_scope_path is not None:
        material_prim = sublayer_stage.DefinePrim(mat_path, "Material")
    else:
        material_prim = sublayer_stage.OverridePrim(mat_path)
        material_prim.SetTypeName("Material")

    shader_path = mat_path.AppendPath("Shader")
    if material_scope_path is not None:
        shader_prim = sublayer_stage.DefinePrim(shader_path, "Shader")
    else:
        shader_prim = sublayer_stage.OverridePrim(shader_path)
        shader_prim.SetTypeName("Shader")

    if bind_prim_path is not None:
        target_prim = sublayer_stage.GetPrimAtPath(bind_prim_path)
        if target_prim:
            bind_material_on_prim(target_prim, material_prim, bind_strength)
            print(f"  Bound {mat_path} to {bind_prim_path} ({bind_strength})")
        else:
            print(f"  WARNING: cannot bind material, prim {bind_prim_path} was not found")

    material_api = UsdShade.Material(material_prim)
    shader_output = UsdShade.Shader(shader_prim).CreateOutput("out", Sdf.ValueTypeNames.Token)
    shader_output.SetRenderType("material")

    # `Create*Output` takes a render context: "mdl" produces `outputs:mdl:surface`.
    material_api.CreateSurfaceOutput("mdl").ConnectToSource(shader_output)
    material_api.CreateDisplacementOutput("mdl").ConnectToSource(shader_output)
    material_api.CreateVolumeOutput("mdl").ConnectToSource(shader_output)

    from ..export_utils import ShaderSetup
    material_type = ShaderSetup.detect_material_type(blender_material)
    
    shader_prim.CreateAttribute("info:implementationSource", Sdf.ValueTypeNames.Token).Set("sourceAsset")
    shader_prim.CreateAttribute("info:mdl:sourceAsset", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(f"{material_type}.mdl"))
    shader_prim.CreateAttribute("info:mdl:sourceAsset:subIdentifier", Sdf.ValueTypeNames.Token).Set(material_type)

    principled_node = None
    if blender_material.use_nodes and blender_material.node_tree:
        for node in blender_material.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                principled_node = node
                break
            elif node.type == 'GROUP' and node.node_tree:
                group_name = node.node_tree.name.lower()
                if any(keyword in group_name for keyword in ['aperture', 'pbr', 'remix']):
                    principled_node = node
                    break
                expected_inputs = ['Base Color', 'Albedo Color', 'Metallic', 'Roughness']
                if any(input_name in [inp.name for inp in node.inputs] for input_name in expected_inputs):
                    principled_node = node
                    break

    if not principled_node:
        return mat_path

    texture_tasks = []
    from .. import core_utils
    texture_processor = core_utils.get_texture_processor()
    
    from bpy import context as bpy_context
    custom_texture_dir = bpy_context.scene.remix_custom_texture_dir or "rtx-remix/textures"
    textures_dir = os.path.join(project_root, custom_texture_dir)
    os.makedirs(textures_dir, exist_ok=True)

    def find_texture_for_socket(socket_name, dds_format='BC7_UNORM_SRGB'):
        is_group = principled_node.type == 'GROUP'
        socket = None
        
        if is_group:
            socket_name_mapping = {
                'Base Color': 'Albedo Color',
                'Metallic': 'Metallic',
                'Roughness': 'Roughness',
                'Normal': 'Normal Map',
                'Emission': 'Emissive Color',
                'Emissive Color': 'Emissive Color'
            }
            mapped_name = socket_name_mapping.get(socket_name, socket_name)
            socket = principled_node.inputs.get(mapped_name)
            if not socket:
                for input_socket in principled_node.inputs:
                    if socket_name.lower() in input_socket.name.lower():
                        socket = input_socket
                        break
        else:
            socket = principled_node.inputs.get(socket_name)
            
        if not socket:
            return None, None
            
        image_node = None
        if socket and socket.is_linked:
            link = socket.links[0]
            node = link.from_node
            while node and node.type != 'TEX_IMAGE':
                input_found = False
                for input_socket in node.inputs:
                    if input_socket.is_linked:
                        node = input_socket.links[0].from_node
                        input_found = True
                        break
                if not input_found:
                    node = None
            if node and node.type == 'TEX_IMAGE':
                image_node = node

        if image_node and image_node.image and texture_processor:
            bl_image = image_node.image
            texture_type = socket_name.lower()
            if texture_type in ['emissive color', 'emission']:
                texture_type = 'emission'
            elif texture_type in ['base color', 'albedo color']:
                texture_type = 'base color'
            
            type_suffix = texture_processor.get_texture_suffix(texture_type)
            if dds_format == 'BC7_UNORM_SRGB':
                dds_format = texture_processor.get_recommended_format(texture_type)
            
            base_name, _ = os.path.splitext(bl_image.name)
            dds_file_name = f"{base_name}{type_suffix}.dds"
            absolute_dds_path = os.path.normpath(os.path.join(textures_dir, dds_file_name))
            
            if os.path.exists(absolute_dds_path):
                return bl_image, get_relative_path(sublayer_path, absolute_dds_path)
            
            if bpy_context.scene.remix_reuse_existing_textures:
                base_material_name = extract_base_material_name(blender_material.name)
                existing_texture_path = find_existing_texture_for_base_material(
                    base_material_name, texture_type, textures_dir, texture_processor
                )
                if existing_texture_path:
                    return bl_image, get_relative_path(sublayer_path, existing_texture_path)
            
            texture_tasks.append((bl_image, absolute_dds_path, texture_type, dds_format))
            return bl_image, get_relative_path(sublayer_path, absolute_dds_path)

        return None, None

    if material_type == "AperturePBR_Translucent":
        diffuse_image, diffuse_rel_path = find_texture_for_socket('Transmittance/Diffuse Albedo', 'BC7_UNORM_SRGB')
        metallic_image, metallic_rel_path = None, None
        roughness_image, roughness_rel_path = None, None
        normal_image, normal_rel_path = find_texture_for_socket('Normal Map', 'BC5_UNORM')
    else:
        diffuse_image, diffuse_rel_path = find_texture_for_socket('Base Color', 'BC7_UNORM_SRGB')
        metallic_image, metallic_rel_path = find_texture_for_socket('Metallic', 'BC4_UNORM')
        roughness_image, roughness_rel_path = find_texture_for_socket('Roughness', 'BC4_UNORM')
        normal_image, normal_rel_path = find_texture_for_socket('Normal', 'BC5_UNORM')
    
    enable_emission = False
    emissive_image, emissive_rel_path = None, None
    emissive_socket_name = 'Emissive Color' if principled_node.type == 'GROUP' else 'Emission Color'
    if emissive_socket_name not in [inp.name for inp in principled_node.inputs]:
        emissive_socket_name = 'Emission' if 'Emission' in [inp.name for inp in principled_node.inputs] else None
        
    emissive_socket = principled_node.inputs.get(emissive_socket_name) if emissive_socket_name else None
    
    if principled_node.type == 'GROUP':
        enable_emission_socket = principled_node.inputs.get('Enable Emission')
        if enable_emission_socket and enable_emission_socket.default_value:
            enable_emission = True
    else:
        emission_strength_socket = principled_node.inputs.get('Emission Strength')
        if emission_strength_socket and emission_strength_socket.default_value > 0.001:
            enable_emission = True
    
    if emissive_socket and emissive_socket.is_linked:
        emissive_image, emissive_rel_path = find_texture_for_socket(emissive_socket_name, 'BC7_UNORM_SRGB')
        if emissive_rel_path:
            enable_emission = True

    opacity_image, opacity_rel_path = None, None
    opacity_socket = principled_node.inputs.get('Alpha')
    if opacity_socket and opacity_socket.is_linked:
        alpha_uses_same_image = False
        if diffuse_image and opacity_socket.links:
            alpha_node = opacity_socket.links[0].from_node
            if alpha_node and alpha_node.type == 'TEX_IMAGE' and alpha_node.image == diffuse_image:
                alpha_uses_same_image = True
        if not alpha_uses_same_image:
            opacity_image, opacity_rel_path = find_texture_for_socket('Alpha', 'BC4_UNORM')

    specular_image, specular_rel_path = None, None
    specular_socket = principled_node.inputs.get('Specular')
    if specular_socket and specular_socket.is_linked:
        specular_image, specular_rel_path = find_texture_for_socket('Specular', 'BC4_UNORM')

    if texture_tasks and texture_processor:
        background_processor = core_utils.get_background_processor()
        background_processor.start_background_job(texture_tasks)

    if material_type == "AperturePBR_Translucent":
        if diffuse_rel_path:
            shader_prim.CreateAttribute("inputs:transmittance_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(diffuse_rel_path))
            shader_prim.CreateAttribute("inputs:transmittance_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))
        else:
            transmittance_socket = principled_node.inputs.get('Transmittance/Diffuse Albedo')
            if transmittance_socket and hasattr(transmittance_socket.default_value, '__len__'):
                tc = transmittance_socket.default_value
                shader_prim.CreateAttribute("inputs:transmittance_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(tc[0], tc[1], tc[2]))
        
        translucent_params = [
            ('IOR', 'inputs:ior_constant', 'Float'),
            ('Thin Walled', 'inputs:thin_walled', 'Bool'),
            ('Thin Wall Thickness', 'inputs:thin_wall_thickness', 'Float'),
            ('Use Diffuse Layer', 'inputs:use_diffuse_layer', 'Bool'),
            ('Transmittance Measurement Distance', 'inputs:transmittance_measurement_distance', 'Float'),
            ('Enable Emission', 'inputs:enable_emission', 'Bool'),
            ('Emissive Intensity', 'inputs:emissive_intensity', 'Float'),
        ]
        for socket_name, usd_attr, value_type in translucent_params:
            socket = principled_node.inputs.get(socket_name)
            if socket:
                if value_type == 'Bool':
                    shader_prim.CreateAttribute(usd_attr, Sdf.ValueTypeNames.Bool).Set(bool(socket.default_value))
                else:
                    shader_prim.CreateAttribute(usd_attr, Sdf.ValueTypeNames.Float).Set(float(socket.default_value))
        
        if normal_rel_path:
            shader_prim.CreateAttribute("inputs:normalmap_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(normal_rel_path))
            shader_prim.CreateAttribute("inputs:encoding", Sdf.ValueTypeNames.Int).Set(2)
    else:
        if diffuse_rel_path:
            shader_prim.CreateAttribute("inputs:diffuse_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(diffuse_rel_path))
            shader_prim.CreateAttribute("inputs:diffuse_color_constant", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))
        else:
            base_color_socket = principled_node.inputs.get('Albedo Color') or principled_node.inputs.get('Base Color')
            base_color = base_color_socket.default_value if base_color_socket else (0.8, 0.8, 0.8, 1.0)
            shader_prim.CreateAttribute("inputs:diffuse_color_constant", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(base_color[0], base_color[1], base_color[2]))

        if metallic_rel_path:
            shader_prim.CreateAttribute("inputs:metallic_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(metallic_rel_path))
        else:
            metallic_socket = principled_node.inputs.get('Metallic')
            shader_prim.CreateAttribute("inputs:metallic_constant", Sdf.ValueTypeNames.Float).Set(metallic_socket.default_value if metallic_socket else 0.0)

        if roughness_rel_path:
            shader_prim.CreateAttribute("inputs:reflectionroughness_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(roughness_rel_path))
        else:
            roughness_socket = principled_node.inputs.get('Roughness')
            shader_prim.CreateAttribute("inputs:reflection_roughness_constant", Sdf.ValueTypeNames.Float).Set(roughness_socket.default_value if roughness_socket else 0.5)

        if normal_rel_path:
            shader_prim.CreateAttribute("inputs:normalmap_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(normal_rel_path))
            shader_prim.CreateAttribute("inputs:encoding", Sdf.ValueTypeNames.Int).Set(2)

        if enable_emission:
            shader_prim.CreateAttribute("inputs:enable_emission", Sdf.ValueTypeNames.Bool).Set(True)
            emissive_intensity = 1.0
            if principled_node.type == 'GROUP':
                socket = principled_node.inputs.get('Emissive Intensity')
                if socket: emissive_intensity = socket.default_value
            else:
                socket = principled_node.inputs.get('Emission Strength')
                if socket: emissive_intensity = socket.default_value
            
            if emissive_rel_path:
                shader_prim.CreateAttribute("inputs:emissive_mask_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(emissive_rel_path))
            elif emissive_socket and len(emissive_socket.default_value) >= 3:
                ec = emissive_socket.default_value
                shader_prim.CreateAttribute("inputs:emissive_color_constant", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(ec[0], ec[1], ec[2]))
            
            shader_prim.CreateAttribute("inputs:emissive_intensity", Sdf.ValueTypeNames.Float).Set(float(emissive_intensity))

        alpha_socket = principled_node.inputs.get('Alpha')
        has_alpha = False
        if opacity_rel_path:
            shader_prim.CreateAttribute("inputs:opacity_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(opacity_rel_path))
            has_alpha = True
        elif alpha_socket and alpha_socket.is_linked and diffuse_image:
            has_alpha = True
        elif alpha_socket and alpha_socket.default_value < 1.0:
            shader_prim.CreateAttribute("inputs:opacity_constant", Sdf.ValueTypeNames.Float).Set(float(alpha_socket.default_value))
            has_alpha = True
        
        if has_alpha:
            shader_prim.CreateAttribute("inputs:blend_enabled", Sdf.ValueTypeNames.Bool).Set(True)
            shader_prim.CreateAttribute("inputs:blend_type", Sdf.ValueTypeNames.Int).Set(0)
            shader_prim.CreateAttribute("inputs:use_legacy_alpha_state", Sdf.ValueTypeNames.Bool).Set(False)

        if specular_rel_path:
            shader_prim.CreateAttribute("inputs:specular_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(specular_rel_path))
        else:
            specular_socket = principled_node.inputs.get('Specular')
            if specular_socket and specular_socket.default_value != 0.5:
                shader_prim.CreateAttribute("inputs:specular_constant", Sdf.ValueTypeNames.Float).Set(float(specular_socket.default_value))

    return mat_path

# --- Light Export Helper ---

def export_light(operator, context, obj, sublayer_stage, project_root, target_sublayer_path):
    if not obj or obj.type != 'LIGHT' or not USD_AVAILABLE:
        return False

    print(f"\n--- Exporting Light: {obj.name} ---")
    bl_light = obj.data
    remix_export_scale = context.scene.remix_export_scale

    usd_light_type = None
    light_attrs = {}
    shaping_attrs = {}
    extent = None

    light_attrs["inputs:color"] = Gf.Vec3f(bl_light.color[:])
    light_attrs["inputs:intensity"] = float(bl_light.energy)

    if bl_light.type == 'POINT':
        usd_light_type = "SphereLight"
        radius = bl_light.shadow_soft_size
        if radius <= 0 or radius < 0.001 or radius > 10:
            radius = 0.07
        light_attrs["inputs:radius"] = float(radius)
        shaping_attrs["shaping:cone:angle"] = 180.0
        shaping_attrs["shaping:cone:softness"] = 0.0
        shaping_attrs["shaping:focus"] = 0.0
        default_extent = 5.0
        extent = Vt.Vec3fArray([Gf.Vec3f(-default_extent), Gf.Vec3f(default_extent)])

    elif bl_light.type == 'SUN':
        usd_light_type = "DistantLight"
        light_attrs["inputs:angle"] = float(bl_light.angle * 0.5 * 180.0 / 3.14159265)
    elif bl_light.type == 'SPOT':
        usd_light_type = "SphereLight"
        radius = bl_light.shadow_soft_size
        if radius <= 0 or radius < 0.001 or radius > 10:
            radius = 0.07
        light_attrs["inputs:radius"] = float(radius)
        shaping_attrs["shaping:cone:angle"] = float(bl_light.spot_size * 0.5 * 180.0 / 3.14159265)
        shaping_attrs["shaping:cone:softness"] = float(bl_light.spot_blend)
        default_extent = 5.0
        extent = Vt.Vec3fArray([Gf.Vec3f(-default_extent), Gf.Vec3f(default_extent)])
    elif bl_light.type == 'AREA':
        if bl_light.shape in ['SQUARE', 'RECTANGLE']:
            usd_light_type = "RectLight"
            light_attrs["inputs:width"] = float(bl_light.size)
            light_attrs["inputs:height"] = float(bl_light.size_y if bl_light.shape == 'RECTANGLE' else bl_light.size)
            half_w, half_h = light_attrs["inputs:width"]/2.0, light_attrs["inputs:height"]/2.0
            extent = Vt.Vec3fArray([Gf.Vec3f(-half_w, -half_h, 0), Gf.Vec3f(half_w, half_h, 0)])
        elif bl_light.shape in ['DISK', 'ELLIPSE']:
            usd_light_type = "DiskLight"
            light_attrs["inputs:radius"] = float(bl_light.size * 0.5)
            r = light_attrs["inputs:radius"]
            extent = Vt.Vec3fArray([Gf.Vec3f(-r, -r, 0), Gf.Vec3f(r, r, 0)])
        else:
            operator.report({'WARNING'}, f"Unsupported AREA light shape '{bl_light.shape}' for {obj.name}. Skipping.")
            return False
    else:
        operator.report({'WARNING'}, f"Unsupported light type '{bl_light.type}' for {obj.name}. Skipping.")
        return False

    stored_light_path = obj.get("usd_light_path", None)
    if stored_light_path and isinstance(stored_light_path, str) and stored_light_path:
        stored_name = stored_light_path.split('/')[-1]
        if stored_name.startswith("light_"):
            light_name_sanitized = stored_name
        else:
            import uuid
            light_name_sanitized = f"light_{uuid.uuid4().hex[:8]}"
    else:
        import uuid
        light_name_sanitized = f"light_{uuid.uuid4().hex[:8]}"
    
    anchor_obj = context.scene.remix_anchor_object_target
    parent_prim_path = None
    if anchor_obj:
        anchor_path_str = resolve_anchor_prim_path(anchor_obj)
        if anchor_path_str:
            try:
                anchor_path = Sdf.Path(anchor_path_str)
                over_prim = sublayer_stage.OverridePrim(anchor_path)
                p = over_prim
                while p and p.GetPath() != Sdf.Path("/"):
                    p.SetSpecifier(Sdf.SpecifierOver)
                    p = p.GetParent()
                parent_prim_path = anchor_path
            except Exception as e:
                operator.report({'WARNING'}, f"Invalid anchor path '{anchor_path_str}': {e}. Using default path.")
                parent_prim_path = None
    
    if not parent_prim_path:
        remix_assets_path = Sdf.Path("/RootNode/remix_assets")
        sublayer_stage.DefinePrim(remix_assets_path, "Xform")
        parent_prim_path = remix_assets_path

    light_prim_path = parent_prim_path.AppendPath(light_name_sanitized)
    light_prim = sublayer_stage.OverridePrim(light_prim_path)
    if not light_prim:
        operator.report({'ERROR'}, f"Failed to define light prim {light_prim_path}")
        return False

    if not UsdGeom.Xform(light_prim).GetPrim().IsDefined():
        UsdGeom.Xform.Define(sublayer_stage, light_prim_path)
    light_prim.SetTypeName(usd_light_type)

    if shaping_attrs or usd_light_type == "SphereLight":
        UsdLux.ShapingAPI.Apply(light_prim)

    for attr_name, value in light_attrs.items():
        try:
            value_type_name = None
            if isinstance(value, Gf.Vec3f):
                value_type_name = Sdf.ValueTypeNames.Float3
            elif isinstance(value, float):
                value_type_name = Sdf.ValueTypeNames.Float
            elif isinstance(value, int):
                value_type_name = Sdf.ValueTypeNames.Int
            
            if value_type_name:
                usd_attr = light_prim.CreateAttribute(attr_name, value_type_name)
                if usd_attr: usd_attr.Set(value)
        except Exception as e:
            print(f"    ERROR setting attribute {attr_name}: {e}")

    if shaping_attrs:
        for attr_name, value in shaping_attrs.items():
            try:
                usd_attr = light_prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.Float)
                if usd_attr: usd_attr.Set(float(value))
            except Exception as e:
                print(f"    ERROR setting shaping attribute {attr_name}: {e}")
                
    if extent:
        try:
            extent_attr = light_prim.CreateAttribute("extent", Sdf.ValueTypeNames.Float3Array)
            if extent_attr: extent_attr.Set(extent)
        except Exception as e:
            print(f"    ERROR setting extent: {e}")

    if anchor_obj:
        try:
            inv_anchor = get_safe_matrix_inverse(anchor_obj.matrix_world)
            relative_matrix = inv_anchor @ obj.matrix_world
            loc, rot, scale_vec = relative_matrix.decompose()
        except Exception as e:
            print(f"  WARNING: Relative light transform fallback: {e}")
            loc = obj.location - anchor_obj.location
            rot = obj.rotation_quaternion if obj.rotation_mode == 'QUATERNION' else obj.rotation_euler.to_quaternion()
            scale_vec = obj.scale
    else:
        loc, rot, scale_vec = obj.matrix_world.decompose()

    effective_translate = Gf.Vec3d(
        loc[0] * remix_export_scale,
        loc[1] * remix_export_scale,
        loc[2] * remix_export_scale
    )
    effective_scale = Gf.Vec3f(scale_vec[0], scale_vec[1], scale_vec[2])

    xform_api = UsdGeom.XformCommonAPI(light_prim)
    xform_api.SetTranslate(effective_translate)
    rotation_degrees_xyz = tuple(math.degrees(a) for a in rot.to_euler('XYZ'))
    xform_api.SetRotate(Gf.Vec3f(rotation_degrees_xyz[0], rotation_degrees_xyz[1], rotation_degrees_xyz[2]), UsdGeom.XformCommonAPI.RotationOrderXYZ) 
    xform_api.SetScale(effective_scale)

    if obj.material_slots and obj.material_slots[0].material:
        bl_mat = obj.material_slots[0].material
        material_path = export_material(bl_mat, sublayer_stage, project_root, target_sublayer_path, parent_mesh_path=None, obj=obj)
        if material_path:
            UsdShade.MaterialBindingAPI.Apply(light_prim)
            b_rel = light_prim.CreateRelationship("material:binding")
            b_rel.SetTargets([material_path])
            light_prim.CreateAttribute("material:binding:bindMaterialAs", Sdf.ValueTypeNames.Token).Set("strongerThanDescendants")

    obj["usd_light_path"] = str(light_prim_path)
    print(f"--- Finished Exporting Light: {obj.name} ---")
    return True

# --- Core Export Logic Helper ---

def _export_remix_objects(operator, context, target_filepath, selected_objects, material_replacement_mode=False):
    if not USD_AVAILABLE:
        operator.report({'ERROR'}, "USD Python libraries (pxr) not available.")
        return False, 0, 0

    if not selected_objects:
        operator.report({'WARNING'}, "No objects selected for export.")
        return False, 0, 0

    mod_file_path = bpy.path.abspath(context.scene.remix_mod_file_path)
    if not mod_file_path or not os.path.exists(os.path.dirname(mod_file_path)):
        operator.report({'ERROR'}, "Project path (from mod file) not valid.")
        return False, 0, 0

    project_root_dir = os.path.dirname(mod_file_path)
    ensure_mdl_files(project_root_dir)

    target_filepath_abs = os.path.abspath(target_filepath)
    stage = operator.get_or_create_stage(target_filepath_abs)
    if not stage:
        return False, 0, 0

    success_count = 0
    fail_count = 0

    for obj in selected_objects:
        export_successful = False
        
        if material_replacement_mode:
            if obj.type == 'MESH':
                export_successful = export_material_replacement(operator, context, obj, stage, project_root_dir, target_filepath_abs)
            else:
                continue
        else:
            if obj.type == 'MESH':
                export_successful = operator.export_mesh(context, obj, stage, project_root_dir, target_filepath_abs)
            elif obj.type == 'LIGHT':
                export_successful = export_light(operator, context, obj, stage, project_root_dir, target_filepath_abs)
            else:
                continue

        if export_successful:
            success_count += 1
        else:
            fail_count += 1

    if stage.GetRootLayer().dirty:
        try:
            stage.GetRootLayer().Save()
        except Exception as e:
            operator.report({'ERROR'}, f"Failed to save stage {target_filepath_abs}: {e}")
            return False, success_count, fail_count

    print(f"--- Export Finished: {success_count} succeeded, {fail_count} failed ---")
    return True, success_count, fail_count

# --- Export Operator (Sublayer) ---

class ExportRemixAsset(Operator):
    """Export selected Blender object(s) as RTX Remix assets to a USD sublayer"""
    bl_idname = "export_scene.rtx_remix_asset"
    bl_label = "Export Remix Asset"
    bl_options = {'REGISTER', 'UNDO'}

    material_replacement_mode: bpy.props.BoolProperty(
        name="Material Replacement Mode",
        description="Only replace materials on existing meshes, without exporting mesh data",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        if not USD_AVAILABLE:
            cls.poll_message_set("USD libraries (pxr) not found.")
            return False
            
        mod_file_path_raw = context.scene.remix_mod_file_path
        mod_file_path_abs = bpy.path.abspath(mod_file_path_raw) if mod_file_path_raw else ""

        if not mod_file_path_abs or not os.path.exists(mod_file_path_abs):
            cls.poll_message_set("Load a Remix project mod file first (ensure path is valid).")
            return False
        if not context.scene.remix_active_sublayer_path:
            cls.poll_message_set("Select a target sublayer in the Remix Project panel.")
            return False
        if not context.selected_objects:
            cls.poll_message_set("No objects selected for export.")
            return False
        return True

    def get_or_create_stage(self, file_path):
        try:
            if os.path.exists(file_path):
                stage = Usd.Stage.Open(file_path)
                if not stage:
                    self.report({'ERROR'}, f"Failed to open existing stage: {file_path}")
                    return None
                return stage 
            else:
                stage = Usd.Stage.CreateNew(file_path)
                if not stage:
                    self.report({'ERROR'}, f"Failed to create new stage: {file_path}")
                    return None

                root_layer = stage.GetRootLayer()
                UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
                root_layer.comment = "Generated by RTX Remix Toolkit"
                UsdGeom.SetStageMetersPerUnit(stage, 1.0)
                root_layer.startTimeCode = 0
                root_layer.endTimeCode = 100
                root_layer.timeCodesPerSecond = 24
                
                is_mesh_data_file = "assets/ingested" in file_path.replace('\\', '/')
                if is_mesh_data_file:
                    ref_target_path = Sdf.Path("/ReferenceTarget")
                    ref_target_prim = stage.DefinePrim(ref_target_path, "Xform")
                    ref_target_prim.SetMetadata(Sdf.PrimSpec.KindKey, "group")
                    stage.SetDefaultPrim(ref_target_prim)
                else:
                    root_prim_path = Sdf.Path("/RootNode") 
                    root_prim = stage.OverridePrim(root_prim_path)
                    root_prim.SetSpecifier(Sdf.SpecifierOver)

                UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
                
                layer_type = "data" if is_mesh_data_file else "replacement"
                custom_data = {
                    'lightspeed_game_name': bpy.context.scene.remix_game_name,
                    'lightspeed_layer_type': layer_type,
                    'renderSettings': {},
                }
                root_layer.customLayerData = custom_data
                root_layer.timeCodesPerSecond = 24

                if not is_mesh_data_file and root_layer.HasDefaultPrim():
                    root_layer.ClearDefaultPrim()
                
                root_layer.comment = "Generated by RTX Remix Toolkit"
                return stage
        except Exception as e:
            self.report({'ERROR'}, f"Error opening/creating stage {file_path}: {e}")
            return None

    def export_mesh(self, context, obj, sublayer_stage, project_root, target_sublayer_path):
        print(f"\n--- Exporting Mesh: {obj.name} ---")
        remix_export_scale = context.scene.remix_export_scale or 1.0
        anchor_obj = context.scene.remix_anchor_object_target
        hide_original = getattr(context.scene, "remix_hide_original_mesh", True)
        inherit_material = getattr(context.scene, "remix_inherit_parent_material", False)

        if obj.type == 'MESH' and context.scene.remix_auto_apply_transforms:
            original_selection = context.selected_objects[:]
            original_active = context.view_layer.objects.active

            try:
                bpy.ops.object.select_all(action='DESELECT')
                obj.select_set(True)
                context.view_layer.objects.active = obj

                needs_rotation_apply = any(abs(r) > 0.001 for r in obj.rotation_euler) or \
                                     any(abs(r) > 0.001 for r in obj.rotation_quaternion[1:]) or \
                                     abs(obj.rotation_quaternion[0] - 1.0) > 0.001
                needs_scale_apply = any(abs(s - 1.0) > 0.001 for s in obj.scale)

                if needs_rotation_apply or needs_scale_apply:
                    if obj.data.users > 1:
                        obj.data = obj.data.copy()

                    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

                    if "remix_processed" in obj:
                        del obj["remix_processed"]
            except Exception as e:
                print(f"  WARNING: Failed to apply rotation/scale: {e}")
            finally:
                bpy.ops.object.select_all(action='DESELECT')
                for selected_obj in original_selection:
                    if selected_obj.name in bpy.data.objects:
                        bpy.data.objects[selected_obj.name].select_set(True)
                if original_active and original_active.name in bpy.data.objects:
                    context.view_layer.objects.active = bpy.data.objects[original_active.name]

        custom_mesh_dir = context.scene.remix_custom_mesh_dir or "assets/ingested"
        custom_texture_dir = context.scene.remix_custom_texture_dir or "rtx-remix/textures"
        ingested_dir = os.path.join(project_root, custom_mesh_dir)
        textures_dir = os.path.join(project_root, custom_texture_dir)
        os.makedirs(ingested_dir, exist_ok=True)
        os.makedirs(textures_dir, exist_ok=True)

        mesh_name_to_use = get_mesh_name_from_object(obj)
        mesh_name_sanitized = sanitize_prim_name(mesh_name_to_use)
        mesh_file_name = f"{mesh_name_sanitized}.usda"
        mesh_file_path = os.path.normpath(os.path.join(ingested_dir, mesh_file_name))

        bl_mat = None
        if obj.material_slots and obj.material_slots[0].material:
            bl_mat = obj.material_slots[0].material

        # --- Which captured Remix mesh does this object replace? ---------------
        game_stage, target_mesh_path, target_instance_world = resolve_replacement_target(
            context, obj, anchor_obj
        )

        # Geometry is authored in game units and, for a replacement, in the local
        # space of the mesh prototype being replaced. The prototype is instanced,
        # so its own space is the only one shared by every instance of it; the
        # `ref_` holder then stays at identity.
        points_matrix = mathutils.Matrix.Scale(1.0 / remix_export_scale, 4) @ obj.matrix_world
        if target_instance_world is not None:
            points_matrix = get_safe_matrix_inverse(target_instance_world) @ points_matrix

        # --- Ingested mesh asset (geometry + material) -------------------------
        mesh_stage = self.get_or_create_stage(mesh_file_path)
        if not mesh_stage:
            return False

        up_axis = UsdGeom.GetStageUpAxis(game_stage) if game_stage else UsdGeom.Tokens.z
        written = write_ingested_mesh_asset(
            mesh_stage,
            obj,
            context,
            mesh_name_sanitized,
            points_matrix,
            bl_mat,
            inherit_material,
            project_root,
            mesh_file_path,
            up_axis,
            self.report,
        )
        if not written:
            return False
        asset_mesh_prim_path, material_path = written

        obj["remix_processed"] = True
        obj["remix_mesh_file_path"] = mesh_file_path
        obj["remix_material_path"] = str(material_path) if material_path else ""

        # --- Author the replacement in the target layer ------------------------
        #
        # Remix mesh replacement is two layer edits on the *captured* prim path
        # (see lightspeed.trex.asset_replacements.core.shared.Setup.add_new_reference):
        #
        #   over "mesh_<HASH>" ( references = None )
        #   {
        #       def Xform "ref_<uuid4hex>" ( prepend references = @./assets/ingested/x.usda@ )
        #       {
        #           custom bool IsRemixRef = 1
        #           <identity translate/rotateXYZ/scale + xformOpOrder>
        #       }
        #   }
        #
        # The holder is a child of the captured mesh prim, so it inherits every
        # instance transform and needs no transform of its own.
        relative_mesh_path = get_relative_path(target_sublayer_path, mesh_file_path)
        if not relative_mesh_path.startswith(("./", "../")):
            relative_mesh_path = "./" + relative_mesh_path

        if target_mesh_path:
            reference_path = author_mesh_replacement(
                sublayer_stage,
                target_mesh_path,
                relative_mesh_path,
                self.report,
                hide_original=hide_original,
            )
            if not reference_path:
                return False
            obj["remix_reference_prim_path"] = str(reference_path)
            print(f"  Replacement of {target_mesh_path} authored (asset: {asset_mesh_prim_path})")
        else:
            self.report(
                {'WARNING'},
                f"No Target Mesh selected - '{obj.name}' is added as a new Remix mesh and instanced.",
            )
            new_mesh_path = author_new_remix_mesh(sublayer_stage, relative_mesh_path, self.report)
            if not new_mesh_path:
                return False
            obj["remix_mesh_prim_path"] = str(new_mesh_path)

        print(f"--- Finished Exporting Mesh: {obj.name} ---")
        return True

    def execute(self, context):
        selected_objects = context.selected_objects
        target_sublayer_path = context.scene.remix_active_sublayer_path
        if not target_sublayer_path:
             self.report({'ERROR'}, "No active sublayer path set.")
             return {'CANCELLED'}

        target_sublayer_path_abs = os.path.abspath(target_sublayer_path)

        success, success_count, fail_count = _export_remix_objects(
            self, 
            context, 
            target_sublayer_path_abs, 
            selected_objects,
            material_replacement_mode=self.material_replacement_mode
        )

        if not success and fail_count == 0 and success_count == 0:
             return {'CANCELLED'} 
        elif fail_count > 0:
            self.report({'WARNING'}, f"Export to sublayer finished with {success_count} successes and {fail_count} failures.")
            return {'FINISHED'}
        elif success_count > 0:
             self.report({'INFO'}, f"Successfully exported {success_count} objects to sublayer.")
             return {'FINISHED'}
        else:
             self.report({'WARNING'}, "No supported objects were exported to sublayer.")
             return {'CANCELLED'}

# --- Export Operator (mod.usda, no sublayers) ---

class ExportRemixModFile(Operator):
    """Export selected Blender object(s) directly to the main mod.usda file"""
    bl_idname = "export_scene.rtx_remix_mod_file"
    bl_label = "Export Selected to mod.usda"
    bl_options = {'REGISTER', 'UNDO'}

    material_replacement_mode: bpy.props.BoolProperty(
        name="Material Replacement Mode",
        description="Only replace materials on existing meshes, without exporting mesh data",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        if not USD_AVAILABLE:
            cls.poll_message_set("USD libraries (pxr) not found.")
            return False
            
        mod_file_path_raw = context.scene.remix_mod_file_path
        mod_file_path_abs = bpy.path.abspath(mod_file_path_raw) if mod_file_path_raw else ""

        if not mod_file_path_abs or not os.path.exists(mod_file_path_abs):
            cls.poll_message_set("Load a Remix project mod file first.")
            return False
        if not context.selected_objects:
            cls.poll_message_set("No objects selected for export.")
            return False
        return True

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "material_replacement_mode")

    get_or_create_stage = ExportRemixAsset.get_or_create_stage 
    export_mesh = ExportRemixAsset.export_mesh

    def execute(self, context):
        selected_objects = context.selected_objects
        mod_file_path = bpy.path.abspath(context.scene.remix_mod_file_path)
        if not mod_file_path:
             self.report({'ERROR'}, "Mod file path is not set.")
             return {'CANCELLED'}

        success, success_count, fail_count = _export_remix_objects(
            self, 
            context, 
            mod_file_path,
            selected_objects,
            material_replacement_mode=self.material_replacement_mode
        )

        if not success and fail_count == 0 and success_count == 0:
             return {'CANCELLED'} 
        elif fail_count > 0:
            self.report({'WARNING'}, f"Export to mod.usda finished with {success_count} successes and {fail_count} failures.")
            return {'FINISHED'}
        elif success_count > 0:
             self.report({'INFO'}, f"Successfully exported {success_count} objects to mod.usda.")
             return {'FINISHED'}
        else:
             self.report({'WARNING'}, "No supported objects were exported to mod.usda.")
             return {'CANCELLED'}

# --- Invalidate Asset Processing State ---
class InvalidateRemixAssets(Operator):
    """Force reprocessing of selected assets on next export"""
    bl_idname = "object.rtx_remix_invalidate_assets"
    bl_label = "Invalidate Remix Assets"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return context.selected_objects and USD_AVAILABLE
    
    def execute(self, context):
        invalidated_count = 0
        for obj in context.selected_objects:
            if "remix_processed" in obj:
                obj["remix_processed"] = False
                if "remix_material_path" in obj:
                    del obj["remix_material_path"]
                invalidated_count += 1
                
        self.report({'INFO'}, f"Invalidated {invalidated_count} assets for reprocessing.")
        return {'FINISHED'}

# --- Test Operator for Manual Transform Application ---
class TestApplyAllTransforms(Operator):
    """Test operator to manually apply all transforms to selected objects"""
    bl_idname = "object.test_apply_all_transforms"
    bl_label = "Test: Apply All Transforms"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return context.selected_objects and any(obj.type == 'MESH' for obj in context.selected_objects)
    
    def execute(self, context):
        mesh_objects = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not mesh_objects:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}
        
        original_selection = context.selected_objects[:]
        original_active = context.view_layer.objects.active
        applied_count = 0
        
        try:
            for obj in mesh_objects:
                bpy.ops.object.select_all(action='DESELECT')
                obj.select_set(True)
                context.view_layer.objects.active = obj
                
                needs_location_apply = any(abs(l) > 0.001 for l in obj.location)
                needs_rotation_apply = any(abs(r) > 0.001 for r in obj.rotation_euler) or \
                                     any(abs(r) > 0.001 for r in obj.rotation_quaternion[1:]) or \
                                     abs(obj.rotation_quaternion[0] - 1.0) > 0.001
                needs_scale_apply = any(abs(s - 1.0) > 0.001 for s in obj.scale)
                
                if needs_location_apply or needs_rotation_apply or needs_scale_apply:
                    if obj.data.users > 1:
                        obj.data = obj.data.copy()
                    
                    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
                    applied_count += 1
                    
        except Exception as e:
            self.report({'ERROR'}, f"Failed to apply transforms: {e}")
            traceback.print_exc()
            return {'CANCELLED'}
            
        finally:
            bpy.ops.object.select_all(action='DESELECT')
            for selected_obj in original_selection:
                if selected_obj.name in bpy.data.objects:
                    bpy.data.objects[selected_obj.name].select_set(True)
            if original_active and original_active.name in bpy.data.objects:
                context.view_layer.objects.active = bpy.data.objects[original_active.name]
        
        if applied_count > 0:
            self.report({'INFO'}, f"Applied transforms to {applied_count} objects.")
        else:
            self.report({'INFO'}, "All selected objects already have identity transforms.")
            
        return {'FINISHED'}

# --- Material Replacement Export Helper ---

def export_material_replacement(operator, context, obj, sublayer_stage, project_root, target_sublayer_path):
    """Exports material replacement for an existing mesh object."""
    if not obj or obj.type != 'MESH':
        return False

    print(f"\n--- Material Replacement for Mesh: {obj.name} ---")
    usd_prim_path = resolve_anchor_prim_path(obj)
    if not usd_prim_path:
        operator.report({'WARNING'}, f"Object '{obj.name}' has no stored USD prim path. Cannot perform material replacement.")
        return False

    print(f"  Using stored USD prim path: {usd_prim_path}")
    bl_mat = None
    if obj.material_slots:
        bl_mat = obj.material_slots[0].material
        if not bl_mat:
            operator.report({'WARNING'}, f"Object '{obj.name}' has no material to replace.")
            return False
    else:
        operator.report({'WARNING'}, f"Object '{obj.name}' has no material slots.")
        return False

    try:
        prim_path = Sdf.Path(usd_prim_path)
        material_path = export_material(bl_mat, sublayer_stage, project_root, target_sublayer_path, parent_mesh_path=None, obj=obj)
        if material_path:
            target_prim = sublayer_stage.OverridePrim(prim_path)
            # `bindMaterialAs` is metadata on the binding relationship, so it has
            # to be authored through MaterialBindingAPI - a
            # `material:binding:bindMaterialAs` attribute is inert.
            bind_material_on_prim(
                target_prim,
                sublayer_stage.GetPrimAtPath(material_path),
                "strongerThanDescendants",
            )
            print(f"  Successfully replaced material for {obj.name} at {prim_path}")
            obj["remix_material_path"] = str(material_path)
            return True
        else:
            operator.report({'ERROR'}, f"Failed to export replacement material for {obj.name}")
            return False
            
    except Exception as e:
        operator.report({'ERROR'}, f"Error during material replacement for {obj.name}: {e}")
        traceback.print_exc()
        return False

# --- Registration ---

classes = (
    ExportRemixAsset,
    ExportRemixModFile,
    InvalidateRemixAssets,
    TestApplyAllTransforms,
)

def register():
    # Регистрация глобального свойства сцены
    if not hasattr(bpy.types.Scene, "remix_inherit_parent_material"):
        bpy.types.Scene.remix_inherit_parent_material = bpy.props.BoolProperty(
            name="Inherit Parent Material",
            description="Inherit material from the target anchor mesh instead of exporting a new material",
            default=False
        )
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, "remix_inherit_parent_material"):
        del bpy.types.Scene.remix_inherit_parent_material