"""Defaults from the bundled AperturePBR MDL declarations."""

import math

DEFAULTS = {
    'AperturePBR_Opacity': {
        'diffuse_color_constant': (0.2, 0.2, 0.2),
        'opacity_constant': 1.0,
        'reflection_roughness_constant': 0.5,
        'metallic_constant': 0.0,
        'displace_in': 0.05,
        'displace_out': 0.0,
        'enable_emission': False,
        'emissive_color_constant': (1.0, 0.1, 0.1),
        'emissive_intensity': 40.0,
        'use_legacy_alpha_state': True,
        'blend_enabled': False,
        'blend_type': 0,
    },
    'AperturePBR_Translucent': {
        'ior_constant': 1.3,
        'thin_walled': False,
        'thin_wall_thickness': 1.0,
        'use_diffuse_layer': False,
        'transmittance_color': (0.97, 0.97, 0.97),
        'transmittance_measurement_distance': 1.0,
        'enable_emission': False,
        'emissive_color': (1.0, 0.1, 0.1),
        'emissive_intensity': 40.0,
    },
}


def _matches(value, default):
    if value is None:
        return False
    if isinstance(default, tuple):
        return len(value) == len(default) and all(math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-6) for a, b in zip(value, default))
    if isinstance(default, bool):
        return value == default
    return math.isclose(float(value), float(default), rel_tol=1e-6, abs_tol=1e-6)


# Managed inputs per MDL family, grouped by the kind of neutral value that
# stands for "the user's graph does not use this input":
#   * assets are masked with an empty asset path,
#   * booleans are masked with False.
# Shared inputs (normalmap_texture, emissing flags) belong to both families and
# must never be treated as "dead" when the family switches.
TEXTURE_INPUTS = {
    'AperturePBR_Opacity': (
        'diffuse_texture', 'metallic_texture', 'reflectionroughness_texture',
        'opacity_texture', 'specular_texture', 'height_texture',
    ),
    'AperturePBR_Translucent': ('transmittance_texture',),
}
FLAG_INPUTS = {
    'AperturePBR_Opacity': ('enable_opacity', 'blend_enabled'),
    'AperturePBR_Translucent': ('thin_walled', 'use_diffuse_layer'),
}
# Inputs present in both families (kept out of the dead-family sets above).
SHARED_TEXTURE_INPUTS = ('normalmap_texture', 'emissive_mask_texture')
SHARED_FLAG_INPUTS = ('enable_emission',)


def weaker_layer_authors_prim(stage, prim_path):
    """Whether any layer weaker than the edit target authors *prim_path*.

    A replacement exporter may only omit MDL defaults when the weaker
    composition is visible to the stage: otherwise the exported layer would
    fall back to the MDL default while a weaker capture value, invisible here,
    could win at runtime.
    """
    if stage is None:
        return False
    target = stage.GetEditTarget().GetLayer()
    target_id = target.identifier if target else None
    for layer in stage.GetLayerStack(includeSessionLayers=False):
        if target_id is not None and layer.identifier == target_id:
            continue
        if layer.GetPrimAtPath(prim_path):
            return True
    return False


def dead_family_inputs(old_family, new_family):
    """Managed inputs the old MDL family names but the new one does not."""
    if old_family not in TEXTURE_INPUTS or new_family not in TEXTURE_INPUTS:
        return set()
    alive = (set(TEXTURE_INPUTS[new_family]) | set(FLAG_INPUTS[new_family])
             | set(SHARED_TEXTURE_INPUTS) | set(SHARED_FLAG_INPUTS))
    dead = set(TEXTURE_INPUTS[old_family]) | set(FLAG_INPUTS[old_family])
    return dead - alive


def remove_default_inputs(shader, material_type, preserve_authored=False):
    """Omit MDL defaults only when the weaker composition is known."""
    if preserve_authored:
        return
    for name, default in DEFAULTS.get(material_type, {}).items():
        attribute = shader.GetAttribute('inputs:' + name)
        if not attribute or not _matches(attribute.Get(), default):
            continue
        value = attribute.Get()
        type_name = attribute.GetTypeName()
        custom = attribute.IsCustom()
        shader.RemoveProperty(attribute.GetName())
        inherited = shader.GetAttribute('inputs:' + name)
        if inherited and inherited.HasValue() and not _matches(inherited.Get(), default):
            shader.CreateAttribute('inputs:' + name, type_name, custom=custom).Set(value)
