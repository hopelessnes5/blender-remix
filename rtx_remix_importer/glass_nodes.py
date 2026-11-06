"""Blender-native node representation for RTX Remix AperturePBR_Translucent.

Unlike the opaque MDL family, translucent uses a Glass BSDF for refraction.
A ShaderNodeEmission + Add Shader pair is added only when the MDL actually
uses emission. No Omniverse Kit or REST API is required.
"""

import bpy

_EMISSION_LABEL = "RTX Remix Translucent Emission"


def glass_node(material):
    """Find a Glass BSDF in *material* (including Glass + Emission graphs)."""
    if not material or not material.node_tree:
        return None
    return next((node for node in material.node_tree.nodes if node.type == 'BSDF_GLASS'), None)


def emission_node(material):
    """Find only the emission node managed by the translucent adapter."""
    if not material or not material.node_tree:
        return None
    return next((node for node in material.node_tree.nodes
                 if node.type == 'EMISSION' and node.label == _EMISSION_LABEL), None)


def reset_glass_nodes(mat):
    """Replace a stale shader tree in an existing material datablock."""
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    output = nodes.new(type='ShaderNodeOutputMaterial')
    output.location = (380, 0)
    glass = nodes.new(type='ShaderNodeBsdfGlass')
    glass.location = (0, 0)
    glass.inputs['Color'].default_value = (0.97, 0.97, 0.97, 1.0)
    glass.inputs['Roughness'].default_value = 0.0
    glass.inputs['IOR'].default_value = 1.3
    links.new(glass.outputs['BSDF'], output.inputs['Surface'])
    return glass


def create_glass_material(name):
    mat = bpy.data.materials.new(name=name)
    try:
        mat.id_properties_ensure()  # Blender 5.x compatibility
    except AttributeError:
        pass
    return mat, reset_glass_nodes(mat)


def ensure_emission(material):
    """Create an additive Emission branch for Glass, idempotently."""
    existing = emission_node(material)
    if existing:
        return existing
    glass = glass_node(material)
    if glass is None:
        raise ValueError('Translucent emission requires a Glass BSDF')
    nodes, links = material.node_tree.nodes, material.node_tree.links
    output = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
    if output is None:
        output = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None)
    if output is None:
        output = nodes.new(type='ShaderNodeOutputMaterial')
    emission = nodes.new(type='ShaderNodeEmission')
    emission.label = _EMISSION_LABEL
    emission.location = (0, -250)
    emission.inputs['Color'].default_value = (1.0, 0.1, 0.1, 1.0)
    emission.inputs['Strength'].default_value = 0.0
    add = nodes.new(type='ShaderNodeAddShader')
    add.label = 'RTX Remix Glass + Emission'
    add.location = (220, 0)
    links.new(glass.outputs['BSDF'], add.inputs[0])
    links.new(emission.outputs['Emission'], add.inputs[1])
    links.new(add.outputs[0], output.inputs['Surface'])
    return emission


def upgrade_legacy_translucent(material):
    """Convert an existing transmission Principled to Glass without losing maps.

    Used when a partial mod override inherits a material created by an older
    version of the add-on. Non-translucent Principled materials are untouched.
    """
    glass = glass_node(material)
    if glass:
        return glass
    if not material or not material.node_tree:
        return None
    nodes, links = material.node_tree.nodes, material.node_tree.links
    principled = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if principled is None:
        return None
    trans = principled.inputs.get('Transmission Weight') or principled.inputs.get('Transmission')
    if trans is None or trans.is_linked or trans.default_value < 0.5:
        return None

    glass = nodes.new(type='ShaderNodeBsdfGlass')
    glass.location = principled.location.copy()
    for src_names, target in ((('Base Color',), 'Color'),
                              (('Roughness',), 'Roughness'),
                              (('IOR',), 'IOR'),
                              (('Normal',), 'Normal')):
        source = next((principled.inputs.get(name) for name in src_names if principled.inputs.get(name)), None)
        destination = glass.inputs.get(target)
        if source is None or destination is None:
            continue
        # Older add-on versions forced Principled Roughness=1.0 even for
        # transmissive materials. That value was a fallback, not an MDL input;
        # clear Glass defaults to Roughness=0 unless the socket was modified.
        if target == 'Roughness' and not source.is_linked and source.default_value == 1.0:
            destination.default_value = 0.0
        else:
            destination.default_value = source.default_value
        for link in list(source.links):
            if link.to_socket == source:
                links.new(link.from_socket, destination)
    for link in list(principled.outputs['BSDF'].links):
        links.new(glass.outputs['BSDF'], link.to_socket)

    # Preserve visual emission from old translucent Principled materials.
    emission_color = principled.inputs.get('Emission Color')
    emission_strength = principled.inputs.get('Emission Strength')
    old_emission = bool(
        emission_color and emission_strength
        and (emission_strength.is_linked or emission_strength.default_value > 0)
        and (emission_color.is_linked or any(v > 0.001 for v in emission_color.default_value[:3]))
    )
    emission = ensure_emission(material) if old_emission else None
    if emission:
        for source, target in ((emission_color, emission.inputs['Color']),
                               (emission_strength, emission.inputs['Strength'])):
            if source is None:
                continue
            target.default_value = source.default_value
            for link in list(source.links):
                if link.to_socket == source:
                    links.new(link.from_socket, target)
    nodes.remove(principled)
    return glass
