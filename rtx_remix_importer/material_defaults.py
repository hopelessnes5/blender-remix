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


def remove_default_inputs(shader, material_type):
    """Omit MDL defaults, unless a weaker layer supplies a different value."""
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
