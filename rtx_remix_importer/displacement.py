"""Read a supported Blender displacement chain for RTX Remix export."""

import math
import os
import subprocess
import tempfile

import bpy


def load_height_image(path):
    """Load a linear height image; expand BC4's red channel for Blender."""
    if not path or not os.path.isfile(path):
        return None
    if os.path.splitext(path)[1].lower() != '.dds':
        from .texture_utils import load_texture
        return load_texture(path, is_non_color=True)
    from .core_utils import get_texture_processor
    processor = get_texture_processor()
    if not processor.is_available():
        print(f'Cannot load height DDS without texconv: {path}')
        return None
    import numpy as np
    try:
        with tempfile.TemporaryDirectory(prefix='remix_height_import_') as directory:
            result = subprocess.run([processor.texconv_path, path, '-o', directory, '-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-m', '1', '-y', '-nologo'], capture_output=True, text=True, check=False, shell=False, timeout=120)
            png = os.path.join(directory, os.path.splitext(os.path.basename(path))[0] + '.png')
            if result.returncode != 0 or not os.path.isfile(png):
                print(f'Cannot decode height DDS {path}: {result.stderr}')
                return None
            decoded = bpy.data.images.load(png, check_existing=False)
            try:
                decoded.colorspace_settings.name = 'Non-Color'
                width, height = decoded.size
                if not width or not height:
                    return None
                pixels = np.empty(width * height * 4, dtype=np.float32)
                decoded.pixels.foreach_get(pixels)
                pixels = pixels.reshape(-1, 4)
                pixels[:, 1] = pixels[:, 0]
                pixels[:, 2] = pixels[:, 0]
                pixels[:, 3] = 1.0
                image = bpy.data.images.new(os.path.basename(path), width=width, height=height, alpha=True, float_buffer=True)
                image.colorspace_settings.name = 'Non-Color'
                image.pixels.foreach_set(pixels.ravel())
                image.pack()
                return image
            finally:
                bpy.data.images.remove(decoded)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'Cannot load height DDS {path}: {error}')
        return None


def apply_imported_displacement(material, inward=None, outward=None, image=None):
    """Create/update the connected displacement; None keeps the prior value."""
    if inward is None and outward is None and image is None:
        return None
    if not material.use_nodes or not material.node_tree:
        raise ValueError('Material has no node tree')
    nodes, links = material.node_tree.nodes, material.node_tree.links
    output = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
    if output is None:
        raise ValueError('No active Material Output')
    socket = output.inputs['Displacement']
    node, _ = _upstream(socket)
    if node is not None and node.type != 'DISPLACEMENT':
        raise ValueError('Existing displacement uses an unsupported node graph')
    if node is None:
        old_in, old_out = .05, 0.0  # AperturePBR_Opacity MDL defaults.
    else:
        if node.inputs['Scale'].is_linked or node.inputs['Midlevel'].is_linked:
            raise ValueError('Existing displacement Scale or Midlevel is linked')
        old_scale = float(node.inputs['Scale'].default_value)
        old_mid = float(node.inputs['Midlevel'].default_value)
        old_in, old_out = old_scale * old_mid, old_scale * (1.0 - old_mid)
    in_value = old_in if inward is None else float(inward)
    out_value = old_out if outward is None else float(outward)
    if not math.isfinite(in_value) or not math.isfinite(out_value) or in_value < 0 or out_value < 0:
        raise ValueError('Remix displacement values must be finite and nonnegative')
    old_node = None
    if image is not None and node is not None:
        old_node, _ = _upstream(node.inputs['Height'])
        if old_node and old_node.type != 'TEX_IMAGE':
            raise ValueError('Existing displacement Height uses an unsupported node graph')
    if node is None:
        node = nodes.new('ShaderNodeDisplacement')
        node.location = (output.location.x - 250, output.location.y - 220)
        links.new(node.outputs['Displacement'], socket)
    scale = in_value + out_value
    node.inputs['Scale'].default_value = scale
    node.inputs['Midlevel'].default_value = in_value / scale if scale else .5
    if image is not None:
        height_socket = node.inputs['Height']
        texture = old_node if old_node else nodes.new('ShaderNodeTexImage')
        texture.image = image
        texture.location = (node.location.x - 300, node.location.y)
        links.new(texture.outputs['Color'], height_socket)
    return node


def _upstream(socket):
    """Follow reroutes only; arbitrary image processing cannot be exported as-is."""
    visited = set()
    while socket and socket.is_linked:
        link = socket.links[0]
        node = link.from_node
        key = node.as_pointer()
        if key in visited:
            raise ValueError('Cycle in material displacement nodes')
        visited.add(key)
        if node.type != 'REROUTE':
            return node, link.from_socket
        socket = node.inputs[0]
    return None, None


def _scalar(node, name):
    socket = node.inputs[name]
    if socket.is_linked:
        source, output = _upstream(socket)
        if not source or source.type != 'VALUE':
            raise ValueError(f'Displacement {name} must be a constant or Value node')
        value = float(output.default_value)
    else:
        value = float(socket.default_value)
    if not math.isfinite(value):
        raise ValueError(f'Displacement {name} must be finite')
    return value


def read_displacement(material):
    """Return (image, channel, inward, outward) for the active output, or None.

    Blender: offset = (height - midlevel) * scale. Remix stores inward and
    outward magnitudes; its units are relative to texture width, so matching
    the numbers alone does not guarantee equal world-space displacement.
    """
    if not material.use_nodes or not material.node_tree:
        return None
    outputs = [n for n in material.node_tree.nodes if n.type == 'OUTPUT_MATERIAL']
    output = next((n for n in outputs if n.is_active_output), None)
    if output is None:
        return None
    socket = output.inputs.get('Displacement')
    if not socket or not socket.is_linked:
        return None
    node, _ = _upstream(socket)
    if not node or node.type != 'DISPLACEMENT':
        raise ValueError('Material Output displacement must be connected to a Displacement node')
    if getattr(node, 'space', 'OBJECT') != 'OBJECT':
        raise ValueError('Only Object Space displacement is supported')
    height = node.inputs.get('Height')
    source, output_socket = _upstream(height)
    if not source or source.type != 'TEX_IMAGE' or not source.image:
        raise ValueError('Displacement Height must be connected directly to an image texture')
    if output_socket.name not in ('Color', 'Alpha'):
        raise ValueError('Unsupported height texture output')
    mid, scale = _scalar(node, 'Midlevel'), _scalar(node, 'Scale')
    if not 0.0 <= mid <= 1.0 or scale < 0.0:
        raise ValueError('Displacement requires Midlevel in [0, 1] and nonnegative Scale')
    return source.image, output_socket.name, mid * scale, (1.0 - mid) * scale
