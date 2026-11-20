"""Light export rewritten around one USD prim per Blender light object.

The old exporter took the prim name from ``obj["usd_light_path"]``. Copied or
imported lights carry the same tag, so several objects wrote into the same
``OverridePrim`` and only the last one survived. Here every object gets a
stable, unique name; a stored path is reused only when it belongs to exactly
one light in the scene.

Color temperature follows the Remix UsdLux schema:
``inputs:enableColorTemperature`` (bool) enables ``inputs:colorTemperature``
(float, Kelvin). The renderer multiplies the blackbody RGB by ``inputs:color``;
the plugin authors both values and never folds the temperature into color.
"""
from __future__ import annotations

import math
import os
import uuid

import bpy
import mathutils

try:
    from pxr import Gf, Sdf, UsdGeom, UsdLux, UsdShade, Vt
except ImportError:  # pxr comes from Blender
    Gf = Sdf = UsdGeom = UsdLux = UsdShade = Vt = None

from . import write_guard
from .nvidia_core.composition import _layer_metadata
from .nvidia_core.capture import get_capture_layer
from .nvidia_core.geometry import is_trs_representable


def _anchor_prim_path(anchor_obj):
    """Normalize an anchor object to the prim that carries the mesh hash."""
    if not anchor_obj:
        return None
    raw = (anchor_obj.get("usd_prim_path") or anchor_obj.get("usd_instance_path")
           or anchor_obj.get("usd_path"))
    if not raw or not isinstance(raw, str) or not raw.startswith("/"):
        return None
    parts = raw.split("/")
    for index, part in enumerate(parts):
        if part.startswith("mesh_"):
            return "/".join(parts[:index + 1])
    return raw


def light_name_for(obj):
    """Stable unique light name for one Blender object.

    A stored path is reused only when no other light in the scene claims it.
    Duplicates get a deterministic name derived from the Blender object name,
    so a re-export writes the same prim again instead of creating a new one.
    """
    stored = obj.get("usd_light_path", None)
    if stored and isinstance(stored, str):
        name = stored.rsplit("/", 1)[-1]
        if name.startswith("light_"):
            users = sum(
                1 for other in bpy.data.objects
                if other.type == "LIGHT"
                and str(other.get("usd_light_path") or "").rsplit("/", 1)[-1] == name
            )
            if users == 1:
                return name
    return "light_" + uuid.uuid5(uuid.NAMESPACE_DNS, f"remix-light:{obj.name}").hex[:16].upper()


def temperature_attrs(bl_light):
    """Write Remix temperature opinions from Blender's native light settings."""
    attrs = {}
    use_temperature = bool(getattr(bl_light, "use_temperature", False))
    kelvin = float(getattr(bl_light, "temperature", 6500.0) or 6500.0)
    attrs["inputs:enableColorTemperature"] = use_temperature
    if use_temperature:
        # Remix schema: documented range 1000..10000, default 6500.
        attrs["inputs:colorTemperature"] = min(max(kelvin, 1000.0), 10000.0)
    return attrs


def _parent_path(context, stage):
    anchor = getattr(context.scene, "remix_anchor_object_target", None)
    path = _anchor_prim_path(anchor)
    if path:
        return Sdf.Path(path)
    return Sdf.Path("/RootNode/remix_assets")


def _placement(context, obj, anchor):
    """Local placement matrix and TRS validity for a light object."""
    placement = obj.matrix_world.copy()
    if anchor is not None:
        try:
            placement = anchor.matrix_world.inverted() @ placement
        except ValueError as exc:
            return None, f"anchor '{anchor.name}' has a singular world matrix: {exc}"
    if not is_trs_representable(placement):
        return None, "parent chain contains shear or a reflection; apply parent transforms first"
    return placement, None


def export_light(context, obj, sublayer_stage, project_root, target_sublayer_path,
                 report, export_material_fn=None, texture_transaction=None):
    """Export one Blender light into the active mod layer as a distinct prim."""
    if obj is None or obj.type != "LIGHT" or sublayer_stage is None:
        return False

    bl_light = obj.data
    scale = context.scene.remix_export_scale or 1.0
    use_temperature = bool(getattr(bl_light, "use_temperature", False))
    report({"INFO"}, f"Exporting Light: {obj.name}")

    attrs = {
        # When Blender temperature mode is on, the color picker is not the
        # light's color: Remix multiplies `inputs:color` by the blackbody of
        # `inputs:colorTemperature` (see Toolkit compute_luminance), so a
        # non-white base would double the tint. Write white and let the
        # temperature define the color — the color picker is then unnecessary.
        "inputs:color": Gf.Vec3f((1.0, 1.0, 1.0) if use_temperature
                                  else bl_light.color[:]),
        # Blender light energy maps 1:1 to Remix intensity: the game shows
        # the value from the light settings. The scene-scale knob only
        # affects geometry sizes, not intensity.
        "inputs:intensity": float(bl_light.energy),
        "inputs:exposure": 0.0,
    }
    attrs.update(temperature_attrs(bl_light))

    shaping = {}
    extent = None
    if bl_light.type == "POINT":
        light_type = "SphereLight"
        attrs["inputs:radius"] = float(bl_light.shadow_soft_size / scale)
        extent = Vt.Vec3fArray([Gf.Vec3f(-5.0), Gf.Vec3f(5.0)])
    elif bl_light.type == "SUN":
        light_type = "DistantLight"
        attrs["inputs:angle"] = math.degrees(bl_light.angle)
    elif bl_light.type == "SPOT":
        light_type = "SphereLight"
        attrs["inputs:radius"] = float(bl_light.shadow_soft_size / scale)
        shaping["inputs:shaping:cone:angle"] = math.degrees(bl_light.spot_size) * 0.5
        shaping["inputs:shaping:cone:softness"] = float(bl_light.spot_blend)
        extent = Vt.Vec3fArray([Gf.Vec3f(-5.0), Gf.Vec3f(5.0)])
    elif bl_light.type == "AREA" and bl_light.shape in ("SQUARE", "RECTANGLE"):
        light_type = "RectLight"
        width = float(bl_light.size / scale)
        height = float((bl_light.size_y if bl_light.shape == "RECTANGLE" else bl_light.size) / scale)
        attrs["inputs:width"] = width
        attrs["inputs:height"] = height
        extent = Vt.Vec3fArray([
            Gf.Vec3f(-width / 2, -height / 2, 0), Gf.Vec3f(width / 2, height / 2, 0)])
    elif bl_light.type == "AREA" and bl_light.shape in ("DISK", "ELLIPSE"):
        light_type = "DiskLight"
        if bl_light.shape == "ELLIPSE" and abs(bl_light.size_y - bl_light.size) > 1e-6:
            report({"WARNING"}, f"{obj.name}: ELLIPSE exported as a circular DiskLight; y diameter dropped")
        radius = float(bl_light.size * 0.5 / scale)
        attrs["inputs:radius"] = radius
        extent = Vt.Vec3fArray([
            Gf.Vec3f(-radius, -radius, 0), Gf.Vec3f(radius, radius, 0)])
    else:
        report({"WARNING"}, f"Unsupported light '{bl_light.type}' / '{getattr(bl_light, 'shape', '')}' for {obj.name}")
        return False

    anchor = getattr(context.scene, "remix_anchor_object_target", None)
    placement, error = _placement(context, obj, anchor)
    if error:
        report({"ERROR"}, f"Cannot export light {obj.name}: {error}")
        return False

    parent_path = _parent_path(context, sublayer_stage)
    light_path = parent_path.AppendPath(light_name_for(obj))
    edit_layer = sublayer_stage.GetEditTarget().GetLayer()
    write_guard.ensure_writable_layer(edit_layer)
    light_prim = sublayer_stage.DefinePrim(light_path, light_type)
    if not light_prim:
        report({"ERROR"}, f"Failed to define light prim {light_path}")
        return False
    light_prim.SetTypeName(light_type)
    if shaping:
        UsdLux.ShapingAPI.Apply(light_prim)
    elif bl_light.type == "POINT":
        for name in ("inputs:shaping:cone:angle", "inputs:shaping:cone:softness"):
            light_prim.CreateAttribute(name, Sdf.ValueTypeNames.Float).Block()

    value_types = {
        "inputs:color": Sdf.ValueTypeNames.Float3,
        "inputs:intensity": Sdf.ValueTypeNames.Float,
        "inputs:exposure": Sdf.ValueTypeNames.Float,
        "inputs:radius": Sdf.ValueTypeNames.Float,
        "inputs:width": Sdf.ValueTypeNames.Float,
        "inputs:height": Sdf.ValueTypeNames.Float,
        "inputs:angle": Sdf.ValueTypeNames.Float,
        "inputs:colorTemperature": Sdf.ValueTypeNames.Float,
        "inputs:enableColorTemperature": Sdf.ValueTypeNames.Bool,
    }
    for name, value in attrs.items():
        type_name = value_types.get(name, Sdf.ValueTypeNames.Float)
        attribute = light_prim.GetAttribute(name)
        if attribute is None or not attribute.IsValid():
            attribute = light_prim.CreateAttribute(name, type_name)
        attribute.Set(value)
    for name, value in shaping.items():
        attribute = light_prim.GetAttribute(name)
        if attribute is None or not attribute.IsValid():
            attribute = light_prim.CreateAttribute(name, Sdf.ValueTypeNames.Float)
        attribute.Set(float(value))
    if extent is not None:
        attribute = light_prim.GetAttribute("extent")
        if attribute is None or not attribute.IsValid():
            attribute = light_prim.CreateAttribute("extent", Sdf.ValueTypeNames.Float3Array)
        attribute.Set(extent)

    loc, rot, scale_vec = placement.decompose()
    if anchor is not None and (anchor.get("usd_instance_path") or anchor.get("usd_prim_path")):
        import_scale = float(getattr(context.scene, "remix_import_unit_scale", None)
                             or context.scene.get("remix_import_unit_scale", 1.0)
                             or 1.0)
        if import_scale > 0:
            loc *= import_scale
            scale_vec *= import_scale
    else:
        game_stage = None
        try:
            import bpy as _bpy
            stage = None
            mod_path = _bpy.path.abspath(context.scene.remix_mod_file_path or "")
            if mod_path and os.path.exists(mod_path):
                stage = Usd.Stage.Open(mod_path) if Usd else None
            game_stage = stage
        except Exception:
            game_stage = None
        axis_stage = game_stage or sublayer_stage
        capture_axis = _layer_metadata(get_capture_layer(axis_stage), "upAxis")
        if (capture_axis or UsdGeom.GetStageUpAxis(axis_stage)) == UsdGeom.Tokens.y:
            basis = mathutils.Quaternion((1, 0, 0), -math.pi / 2)
            loc = basis @ loc
            rot = basis @ rot

    xform = UsdGeom.XformCommonAPI(light_prim)
    xform.SetTranslate(Gf.Vec3d(loc[0] / scale, loc[1] / scale, loc[2] / scale))
    euler = tuple(math.degrees(angle) for angle in rot.to_euler("XYZ"))
    xform.SetRotate(Gf.Vec3f(*euler), UsdGeom.XformCommonAPI.RotationOrderXYZ)
    xform.SetScale(Gf.Vec3f(*scale_vec))

    if export_material_fn and obj.material_slots and obj.material_slots[0].material:
        material_path = export_material_fn(
            obj.material_slots[0].material, sublayer_stage, project_root,
            target_sublayer_path, obj=obj, _texture_transaction=texture_transaction)
        if material_path:
            UsdShade.MaterialBindingAPI.Apply(light_prim)
            binding = light_prim.CreateRelationship("material:binding")
            binding.SetTargets([material_path])
            light_prim.CreateAttribute(
                "material:binding:bindMaterialAs", Sdf.ValueTypeNames.Token
            ).Set("strongerThanDescendants")

    obj["usd_light_path"] = str(light_path)
    edit_layer.Save()
    report({"INFO"}, f"Finished Exporting Light: {obj.name} -> {light_path}")
    return True
