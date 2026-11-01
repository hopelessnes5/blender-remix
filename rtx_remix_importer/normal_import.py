"""Decode Remix tangent-space normal textures for Blender's Normal Map node.

The octahedral inverse follows AperturePBR_Normal.mdl in RTX Remix Toolkit.
SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES
SPDX-License-Identifier: Apache-2.0
"""

import bpy
import numpy as np


def decode_to_opengl(image, encoding):
    """Create a packed RGB OpenGL image while keeping the source untouched.

    Only explicitly known encodings are decoded. Unknown encoding is retained
    as-is; callers must not guess from the blue channel.
    """
    if encoding in (None, 1):
        return image
    if encoding not in (0, 2):
        raise ValueError(f'Unsupported normal map encoding: {encoding}')
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError(f'Normal texture has no pixels: {image.name}')
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(-1, 4)
    if encoding == 0:
        # Inverse of NVIDIA's rotated hemispherical octahedron mapping.
        p = pixels[:, 0] * 2 - 1
        q = pixels[:, 1] * 2 - 1
        x = (p + q) * .5
        y = (p - q) * .5
        z = 1 - np.abs(x) - np.abs(y)
        length = np.sqrt(x*x + y*y + z*z)
        length = np.where(length > 0, length, 1)
        pixels[:, 0] = (x / length + 1) * .5
        pixels[:, 1] = (-y / length + 1) * .5
        pixels[:, 2] = (z / length + 1) * .5
    else:
        pixels[:, 1] = 1 - pixels[:, 1]
    decoded = bpy.data.images.new(image.name + '_decoded', width=width, height=height, alpha=True, float_buffer=True)
    try:
        decoded.colorspace_settings.name = 'Non-Color'
        decoded.alpha_mode = 'CHANNEL_PACKED'
        decoded.pixels.foreach_set(pixels.ravel())
        decoded['remix_normal_decoded_opengl'] = True
        decoded['remix_normal_original_encoding'] = int(encoding)
        source_name = image.name
        if image.get('remix_dds_fallback_name') == image.name:
            source_name = image.get('remix_dds_original_name', source_name)
        elif image.get('remix_normal_decoded_name') == image.name:
            source_name = image.get('remix_normal_source_name', source_name)
        decoded['remix_normal_source_name'] = source_name
        decoded['remix_normal_source_path'] = image.get('remix_normal_source_path', image.filepath)
        decoded['remix_normal_decoded_name'] = decoded.name
        decoded.pack()
        return decoded
    except Exception:
        bpy.data.images.remove(decoded)
        raise


def effective_encoding(shader):
    """Use an authored USD encoding or the known Aperture MDL default."""
    from .usd_utils import get_input_value
    from pxr import UsdShade
    wrapper = shader if hasattr(shader, 'GetInput') else UsdShade.Shader(shader)
    value = get_input_value(wrapper, 'inputs:encoding')
    if value is not None:
        return int(value)
    prim = shader.GetPrim() if hasattr(shader, 'GetPrim') else shader
    source = prim.GetAttribute('info:mdl:sourceAsset') if prim else None
    value = source.Get() if source and source.HasValue() else None
    path = getattr(value, 'path', str(value or '')).lower()
    if 'aperturepbr_opacity' in path or 'aperturepbr_translucent' in path:
        return 0
    return None
