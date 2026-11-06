"""USD light-shaping and shared Blender datablock provenance.

No bpy or Omniverse dependencies. Instances retain separate transforms/objects,
while identical prototype light parameters can share one Blender Light ID.
"""

from __future__ import annotations

import math
import re


_INSTANCE = re.compile(r"^/RootNode/instances/inst_([A-Fa-f0-9]{16})_\d+(/.*)?$")
_PROTOTYPE = re.compile(r"^/RootNode/meshes/mesh_[A-Fa-f0-9]{16}(/.*)?$")


def _authored_value(prim, name: str, time_code=None):
    try:
        attr = prim.GetAttribute(name)
        if attr and attr.IsValid() and attr.HasAuthoredValue():
            value = attr.Get(time_code) if time_code is not None else attr.Get()
            if value is not None:
                return value
    except (AttributeError, RuntimeError, TypeError):
        return None
    return None


def sphere_cone_parameters(prim, time_code=None):
    """Return ``(has_authored_cone, half_angle_deg, softness)``.

    RTX Remix can author shaping attributes on a SphereLight without calling
    ``UsdLux.ShapingAPI.Apply``. A schema declaration by itself does NOT imply
    that a cone exists. USD cone angle is a *half-angle*; Blender's spot_size is
    a full opening angle. An authored cone softness without an angle still
    indicates an explicitly shaped light (default half-angle: 45 degrees).
    """
    angle = None
    softness = None
    for prefix in ("inputs:shaping:cone:", "shaping:cone:"):
        if angle is None:
            angle = _authored_value(prim, prefix + "angle", time_code)
        if softness is None:
            softness = _authored_value(prim, prefix + "softness", time_code)
    has_cone = angle is not None or softness is not None
    return has_cone, float(angle if angle is not None else 45.0), float(softness if softness is not None else 0.0)


def cone_spot_size(half_angle: float) -> float:
    """Convert USD half-angle (degrees) to Blender full-angle (radians)."""
    return min(math.pi, max(0.001, math.radians(2.0 * float(half_angle))))


def _freeze(value):
    """Stable equality key for Gf vectors, USD scalars and asset paths."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if hasattr(value, 'path') and hasattr(value, 'resolvedPath'):
        return ('asset', str(value.path), str(value.resolvedPath))
    try:
        return tuple(_freeze(item) for item in value)
    except TypeError:
        return repr(value)


def light_parameter_signature(prim, time_code=None):
    """Identity of the composed *light parameters*, excluding its placement.

    Two mapped instances with different USD light overrides must not share a
    Blender Light datablock: sharing would destroy the instance-specific values.
    We intentionally ignore xformOps, visibility, and object hierarchy here.
    """
    attributes = []
    for attr in prim.GetAttributes():
        name = str(attr.GetName())
        if not (name.startswith('inputs:') or name.startswith('shaping:')):
            continue
        try:
            value = attr.Get(time_code) if time_code is not None else attr.Get()
        except (RuntimeError, TypeError):
            continue
        if value is not None:
            attributes.append((name, _freeze(value)))
    return (str(prim.GetTypeName()), tuple(sorted(attributes)))


def prototype_light_path(prim):
    """Map a capture instance light to its reference-prototype prim path."""
    path = str(prim.GetPath())
    match = _INSTANCE.fullmatch(path)
    if not match:
        return path
    prototype = '/RootNode/meshes/mesh_' + match.group(1) + (match.group(2) or '')
    try:
        resolved = prim.GetStage().GetPrimAtPath(prototype)
        if resolved and resolved.IsValid():
            return prototype
    except (AttributeError, RuntimeError):
        pass
    return path


def is_prototype_light_path(path: str) -> bool:
    return bool(_PROTOTYPE.fullmatch(str(path)))
