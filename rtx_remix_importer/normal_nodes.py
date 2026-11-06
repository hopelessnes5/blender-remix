"""Shared, Blender-native node layout for Remix XY normal textures.

The source R and G channels are forwarded unchanged, while B is fixed at 1.0:
Texture -> Separate Color -> Combine Color -> Normal Map -> shader.
This controls the preview graph only. USD normal encoding/decoding is handled
separately by :mod:`normal_import`.
"""


def _source_node(socket):
    if socket is not None and socket.is_linked:
        return socket.links[0].from_node
    return None


def _find_existing_xy_chain(normal_map_node):
    """Return (image, separate, combine) for an existing matching node chain."""
    combine = _source_node(normal_map_node.inputs.get('Color'))
    if combine is None or combine.type != 'COMBINE_COLOR':
        return None

    red = combine.inputs.get('Red')
    green = combine.inputs.get('Green')
    if red is None or green is None:
        return None
    sep_r = _source_node(red)
    sep_g = _source_node(green)
    if sep_r is None or sep_r != sep_g or sep_r.type != 'SEPARATE_COLOR':
        return None
    if (red.links[0].from_socket.name != 'Red' or
            green.links[0].from_socket.name != 'Green'):
        return None

    image = _source_node(sep_r.inputs.get('Color'))
    if image is None or image.type != 'TEX_IMAGE':
        return None
    return image, sep_r, combine


def find_normal_texture_node(normal_map_node):
    """Find Image Texture through either legacy or new XY normal-map wiring."""
    color_source = _source_node(normal_map_node.inputs.get('Color'))
    if color_source is not None and color_source.type == 'TEX_IMAGE':
        return color_source
    chain = _find_existing_xy_chain(normal_map_node)
    return chain[0] if chain is not None else None


def _ensure_link(links, source_socket, target_socket):
    if (target_socket.is_linked and len(target_socket.links) == 1 and
            target_socket.links[0].from_socket == source_socket):
        return
    links.new(source_socket, target_socket)


def connect_xy_normal(nodes, links, texture_node, target_socket, *, normal_map_node=None,
                      target_x=0.0, node_y=0.0):
    """Create/reuse the exact R/G + constant-blue normal graph.

    Repeated calls on an existing graph reuse its nodes; a legacy direct
    Texture -> Normal Map connection is upgraded without replacing the normal
    map node. Blender 4.5+ supports Separate/Combine Color in RGB mode.
    """
    if normal_map_node is None:
        normal_map_node = nodes.new(type='ShaderNodeNormalMap')

    existing = _find_existing_xy_chain(normal_map_node)
    if existing is not None and existing[0] == texture_node:
        _, separate, combine = existing
    else:
        separate = nodes.new(type='ShaderNodeSeparateColor')
        combine = nodes.new(type='ShaderNodeCombineColor')

    separate.mode = 'RGB'
    combine.mode = 'RGB'
    separate.location = (target_x - 625, node_y)
    combine.location = (target_x - 420, node_y)
    texture_node.location = (target_x - 865, node_y)
    normal_map_node.location = (target_x - 205, node_y)

    # Recreate the links, even when reusing a chain, to ensure the layout
    # matches the requested R/G forwarding exactly.
    _ensure_link(links, texture_node.outputs['Color'], separate.inputs['Color'])
    _ensure_link(links, separate.outputs['Red'], combine.inputs['Red'])
    _ensure_link(links, separate.outputs['Green'], combine.inputs['Green'])
    # The blue socket must be unlinked for its default 1.0 to take effect.
    for link in tuple(combine.inputs['Blue'].links):
        links.remove(link)
    combine.inputs['Blue'].default_value = 1.0
    _ensure_link(links, combine.outputs['Color'], normal_map_node.inputs['Color'])
    _ensure_link(links, normal_map_node.outputs['Normal'], target_socket)
    return normal_map_node
