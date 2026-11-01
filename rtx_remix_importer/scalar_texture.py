"""Scalar texture channels shared by import and export."""


def connect_scalar_red(nodes, links, texture, target):
    """Connect the red channel of a Remix scalar texture to a Blender input."""
    separate = nodes.new('ShaderNodeSeparateColor')
    separate.location = (texture.location.x + 220, texture.location.y)
    links.new(texture.outputs['Color'], separate.inputs['Color'])
    links.new(separate.outputs['Red'], target)
    return separate


def read_scalar_source(socket):
    """Resolve a supported scalar graph to (image node, channel).

    Color represents Blender's Color-to-Value conversion; a Separate Color
    node preserves its selected channel. Unsupported processing is rejected.
    """
    if not socket or not socket.is_linked:
        return None, None
    visited = set()
    link = socket.links[0]
    channel = None
    while True:
        node = link.from_node
        pointer = node.as_pointer()
        if pointer in visited:
            raise ValueError('Cycle in scalar texture nodes')
        visited.add(pointer)
        if node.type == 'REROUTE':
            if not node.inputs[0].is_linked:
                raise ValueError('Disconnected scalar texture reroute')
            link = node.inputs[0].links[0]
            continue
        if node.type in ('SEPARATE_COLOR', 'SEPRGB'):
            if channel is not None or link.from_socket.name not in ('Red', 'Green', 'Blue', 'R', 'G', 'B'):
                raise ValueError('Unsupported scalar color channel')
            channel = {'Red': 'R', 'Green': 'G', 'Blue': 'B', 'R': 'R', 'G': 'G', 'B': 'B'}[link.from_socket.name]
            input_socket = node.inputs.get('Color') or node.inputs.get('Image')
            if not input_socket or not input_socket.is_linked:
                raise ValueError('Disconnected scalar color node')
            link = input_socket.links[0]
            continue
        if node.type != 'TEX_IMAGE' or not node.image:
            raise ValueError(f'Unsupported scalar texture node: {node.name}')
        source = link.from_socket.name
        if channel is not None:
            if source != 'Color':
                raise ValueError('Scalar color node must use image Color')
            return node, channel
        if source not in ('Color', 'Alpha'):
            raise ValueError(f'Unsupported image output: {source}')
        return node, source
