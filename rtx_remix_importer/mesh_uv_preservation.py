"""Non-destructive UV preservation when a Remix prototype Mesh is rebuilt.

No bpy import here: this logic can be tested independently of Blender.
"""

from array import array


def matching_topology(source_mesh, target_mesh):
    """True only if a face-corner UV layout has an identical destination.

    Matching only vertex/face counts is unsafe: loop order and corner vertex
    indexes must match as well, or copied face-varying coordinates will shift.
    """
    if (len(source_mesh.vertices) != len(target_mesh.vertices) or
            len(source_mesh.polygons) != len(target_mesh.polygons) or
            len(source_mesh.loops) != len(target_mesh.loops)):
        return False
    return all(tuple(src.vertices) == tuple(dst.vertices)
               for src, dst in zip(source_mesh.polygons, target_mesh.polygons))


def _layer_uvs(layer, loop_count):
    values = array('f', [0.0]) * (loop_count * 2)
    layer.data.foreach_get('uv', values)
    return values


def same_uv_layout(source_mesh, other_mesh):
    """Do two source meshes have identical UV layouts for all layers?"""
    if not matching_topology(source_mesh, other_mesh):
        return False
    if len(source_mesh.uv_layers) != len(other_mesh.uv_layers):
        return False
    count = len(source_mesh.loops)
    for left, right in zip(source_mesh.uv_layers, other_mesh.uv_layers):
        if left.name != right.name or _layer_uvs(left, count) != _layer_uvs(right, count):
            return False
    return True


def copy_uv_layers_if_compatible(source_mesh, target_mesh):
    """Copy all UV maps only when topology and corner order match exactly.

    Returns False without modifying the target if topology is incompatible.
    """
    if not matching_topology(source_mesh, target_mesh):
        return False
    if len(target_mesh.uv_layers) or not len(source_mesh.uv_layers):
        return False

    count = len(source_mesh.loops)
    for source_layer in source_mesh.uv_layers:
        layer = target_mesh.uv_layers.new(name=source_layer.name)
        layer.data.foreach_set('uv', _layer_uvs(source_layer, count))
    try:
        target_mesh.uv_layers.active_index = source_mesh.uv_layers.active_index
    except (AttributeError, IndexError, TypeError, ValueError):
        pass
    for source_layer, target_layer in zip(source_mesh.uv_layers, target_mesh.uv_layers):
        try:
            target_layer.active_render = source_layer.active_render
        except (AttributeError, TypeError):
            pass
    return True
