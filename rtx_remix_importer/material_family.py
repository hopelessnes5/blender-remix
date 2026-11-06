"""Identify RTX Remix MDL shader families from *exact* USD attribute names.

The family of an override-only Shader prim must not be guessed from arbitrary
substrings.  ``subsurface_transmittance_texture`` belongs to AperturePBR_Opacity
and is NOT an AperturePBR_Translucent ``transmittance_texture``.

This module intentionally has no bpy, pxr, or Omniverse dependencies so the
rules can be regression-tested outside Blender.
"""

_TRANSLUCENT_INPUTS = frozenset({
    'transmittance_texture',
    'transmittance_color',
    'transmittance_measurement_distance',
    'thin_walled',
    'thin_wall_thickness',
    'use_diffuse_layer',
})
_OPAQUE_INPUTS = frozenset({
    'diffuse_texture',
    'diffuse_color_constant',
    'metallic_texture',
    'metallic_constant',
    'subsurface_single_scattering_albedo',
})
_MDL_FAMILY_ATTRIBUTES = (
    'info:mdl:sourceAsset:subIdentifier',
    'info:mdl:sourceAsset',
)


def detect_nvidia_shader_family(shader):
    """Return ``'opaque'``, ``'translucent'``, or ``None`` for a partial override.

    MDL identity has priority over input-name hints.  Without an identity,
    explicit ``subsurface_*`` inputs (or opaque diffuse/metallic inputs) imply
    an opaque Aperture material; exact translucent-only names imply Glass.
    A normal/roughness-only patch has no family and must inherit its original.
    """
    prim = shader.GetPrim() if hasattr(shader, 'GetPrim') else shader
    if prim is None or (hasattr(prim, 'IsValid') and not prim.IsValid()):
        return None

    for name in _MDL_FAMILY_ATTRIBUTES:
        attr = prim.GetAttribute(name)
        if not attr or not attr.HasValue():
            continue
        mdl_id = str(attr.Get()).lower()
        if 'aperturepbr_translucent' in mdl_id:
            return 'translucent'
        if 'aperturepbr_opacity' in mdl_id or 'aperturepbr_model' in mdl_id:
            return 'opaque'

    authored = {
        attr.GetName().removeprefix('inputs:').lower()
        for attr in prim.GetAuthoredAttributes()
    }
    # The official AperturePBR_Opacity.mdl defines these subsurface inputs.
    # Check them FIRST: 'subsurface_transmittance_texture' includes the word
    # 'transmittance', but it is not a translucent/glass shader property.
    if (any(name.startswith('subsurface_') for name in authored)
            or authored.intersection(_OPAQUE_INPUTS)):
        return 'opaque'
    if authored.intersection(_TRANSLUCENT_INPUTS):
        return 'translucent'
    return None
