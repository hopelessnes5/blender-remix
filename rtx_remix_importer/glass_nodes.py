"""Blender-native node representation for RTX Remix AperturePBR_Translucent.

Unlike the opaque MDL family, translucent uses a Glass BSDF for refraction.
A ShaderNodeEmission + Add Shader pair is added only when the MDL actually
uses emission; the MDL's absorption-only volume becomes a labeled Volume
Absorption node on the output. No Omniverse Kit or REST API is required.
"""

import bpy
import math

_EMISSION_LABEL = "RTX Remix Translucent Emission"
_VOLUME_LABEL = "RTX Remix Translucent Volume Absorption"

# Beer-Lambert floor: exp(-sigma * distance) below this reads back as the
# floor on re-import, so sigma greater than ln(1/floor)/distance is not
# representable and is clamped instead of producing -log(0) = inf.
_MIN_TRANSMITTANCE = 1e-4
_MIN_MEASUREMENT_DISTANCE = 1e-4


def absorption_coefficients(transmittance_color, measurement_distance):
    """MDL volume absorption sigma = -ln(transmittance) / measurement_distance.

    Matches the AperturePBR_Translucent body
    (``volume_absorption_coefficient``), clamped so hostile inputs (a zero
    transmittance channel or a zero distance) stay finite.
    """
    distance = max(float(measurement_distance), _MIN_MEASUREMENT_DISTANCE)
    sigma = []
    for index in range(3):
        channel = min(max(float(transmittance_color[index]), _MIN_TRANSMITTANCE), 1.0)
        sigma.append(-math.log(channel) / distance)
    return sigma


def _write_absorption(volume_node, transmittance_color, measurement_distance):
    """Encode sigma into a Volume Absorption node (sigma = density * (1 - color)).

    Density is scaled to the strongest channel (at least 1.0) so every encoded
    color channel lands in [0, 1]; density * (1 - color) reconstructs each
    sigma exactly, keeping import -> export round trips lossless.
    """
    sigma = absorption_coefficients(transmittance_color, measurement_distance)
    density = max(1.0, max(sigma))
    color = tuple(1.0 - channel / density for channel in sigma) + (1.0,)
    density_socket = volume_node.inputs.get('Density')
    color_socket = volume_node.inputs.get('Color')
    if density_socket is not None:
        density_socket.default_value = density
    if color_socket is not None:
        color_socket.default_value = color


def active_material_output(material):
    """The active (or only) Material Output node of *material*."""
    if not material or not material.node_tree:
        return None
    return (next((node for node in material.node_tree.nodes
                  if node.type == 'OUTPUT_MATERIAL' and node.is_active_output), None) or
            next((node for node in material.node_tree.nodes if node.type == 'OUTPUT_MATERIAL'), None))


def ensure_volume_absorption(material, transmittance_color=None, measurement_distance=None):
    """Create or update the add-on's Volume Absorption branch on the output.

    MDL translucent materials always carry an absorption-only volume, so the
    Blender representation gets one too whenever transmittance data lands.
    The output's already-connected volume stays in charge: the adapter only
    updates its own labeled node (compared by ``as_pointer`` - node wrappers
    have no Python identity) and never steals the slot from a user-wired one.
    Returns the volume node or None.
    """
    if not material or not material.node_tree:
        return None
    nodes, links = material.node_tree.nodes, material.node_tree.links
    output = active_material_output(material)
    if output is None or 'Volume' not in output.inputs:
        return None
    volume = next((node for node in nodes if node.type == 'VOLUME_ABSORPTION' and node.label == _VOLUME_LABEL), None)
    for link in output.inputs['Volume'].links:
        if volume is None or link.from_node.as_pointer() != volume.as_pointer():
            # The user rewired (or fully authored) the volume at the output.
            return link.from_node
    if volume is None:
        volume = nodes.new(type='ShaderNodeVolumeAbsorption')
        volume.label = _VOLUME_LABEL
        volume.location = (0, -420)
        links.new(volume.outputs['Volume'], output.inputs['Volume'])
    elif not volume.outputs['Volume'].is_linked:
        links.new(volume.outputs['Volume'], output.inputs['Volume'])
    if transmittance_color is not None:
        if measurement_distance is None or float(measurement_distance) <= 0:
            measurement_distance = 1.0
        _write_absorption(volume, transmittance_color, measurement_distance)
    return volume


def remove_volume_absorption(material):
    """Delete the add-on's labeled volume branch; user volumes are kept."""
    if not material or not material.node_tree:
        return False
    nodes, links = material.node_tree.nodes, material.node_tree.links
    volume = next((node for node in nodes if node.type == 'VOLUME_ABSORPTION' and node.label == _VOLUME_LABEL), None)
    if volume is None:
        return False
    for link in list(volume.outputs['Volume'].links):
        links.remove(link)
    nodes.remove(volume)
    return True


def volume_transmittance(material, measurement_distance=None):
    """Read the material's connected volume as a transmittance color.

    Returns ``(transmittance_rgb, note)``: for a Volume Absorption node
    sigma = density * (1 - color); for a vanilla Principled Volume with no
    scattering color, sigma = density * (1 - sqrt(Absorption Color)) per the
    Cycles OSL shader. ``(None, note)`` marks an unsupported graph; the note
    explains which game semantics cannot be exported.
    """
    if not material or not material.node_tree:
        return None, None
    output = active_material_output(material)
    if output is None or 'Volume' not in output.inputs or not output.inputs['Volume'].is_linked:
        return None, None
    # The most recent link wins, matching how a UI reroute replaces one link
    # with another (and how a user volume would supersede the labeled one).
    node = output.inputs['Volume'].links[-1].from_node
    distance = _MIN_MEASUREMENT_DISTANCE
    if measurement_distance is not None and float(measurement_distance) > 0:
        distance = float(measurement_distance)

    def socket_number(node, name, default=0.0):
        socket = node.inputs.get(name)
        if socket is None:
            return None
        try:
            return max(float(socket.default_value), 0.0)
        except (TypeError, ValueError):
            return default

    def socket_color(node, name):
        socket = node.inputs.get(name)
        if socket is None or not hasattr(socket.default_value, "__len__") or len(socket.default_value) < 3:
            return None
        return [min(max(float(socket.default_value[i]), 0.0), 1.0) for i in range(3)]

    if node.type == 'VOLUME_ABSORPTION':
        density = socket_number(node, 'Density')
        color = socket_color(node, 'Color')
        if density is None or color is None:
            return None, f"Volume node '{node.name}' misses Density/Color sockets; absorption skipped"
        sigma = [density * (1.0 - channel) for channel in color]
    elif node.type == 'PRINCIPLED_VOLUME':
        density = socket_number(node, 'Density')
        scatter = socket_color(node, 'Color')
        absorption = socket_color(node, 'Absorption Color')
        if density is None or scatter is None or absorption is None:
            return None, f"Volume node '{node.name}' misses expected sockets; volume skipped"
        if any(channel > 1e-6 for channel in scatter):
            return None, (f"Principled Volume '{node.name}' scatters light (Color socket); "
                          "AperturePBR_Translucent supports absorption only - volume skipped")
        sigma = [density * (1.0 - math.sqrt(channel)) for channel in absorption]
    else:
        return None, f"Unsupported volume node '{node.name}' ({node.type}); volume skipped"
    return tuple(max(math.exp(-channel * distance), _MIN_TRANSMITTANCE) for channel in sigma), None


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
    # Fresh translucent graphs carry the MDL volume defaults (transmittance
    # 0.97 over 1 unit) in the same labeled node import builds.
    ensure_volume_absorption(mat, (0.97, 0.97, 0.97), 1.0)
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
    # Mirror the MDL surface+volume split in the migrated graph: the carried
    # tint doubles as the absorption source at the stored measurement
    # distance (the MDL default of 1.0 when the property is absent).
    glass_color = glass.inputs.get('Color')
    if glass_color is not None:
        distance = material.get('rtx_transmittance_measurement_distance')
        if not isinstance(distance, (int, float)) or distance <= 0:
            distance = 1.0
        ensure_volume_absorption(material, glass_color.default_value, distance)
    nodes.remove(principled)
    return glass


def restore_opaque_principled(material):
    """Rebuild a standard opaque Principled graph from a wrongly-glass graph.

    mod.usda overrides classifying the shader family as AperturePBR_Opacity
    must not land on a base material whose functional graph is Glass from an
    older import. The upgrade's inverse: transfer surface inputs and emission
    into Principled, drop the Glass node, the translucent-only volume branch
    and the now-obsolete Add-shader emission adapter. Returns the Principled
    node, or None when the material holds no glass and no Principled graph.
    """
    if not material or not material.node_tree:
        return None
    nodes, links = material.node_tree.nodes, material.node_tree.links
    principled = next((node for node in nodes if node.type == 'BSDF_PRINCIPLED'), None)
    glass = next((node for node in nodes if node.type == 'BSDF_GLASS'), None)
    if principled is not None:
        remove_volume_absorption(material)
        return principled
    if glass is None:
        return None

    surface_sockets = []
    for link in list(glass.outputs['BSDF'].links):
        surface_sockets.append(link.to_socket)
        links.remove(link)

    principled = nodes.new(type='ShaderNodeBsdfPrincipled')
    principled.location = glass.location.copy()
    for source_name, target_name in (('Color', 'Base Color'),
                                     ('Roughness', 'Roughness'),
                                     ('Normal', 'Normal')):
        source = glass.inputs.get(source_name)
        target = principled.inputs.get(target_name)
        if source is None or target is None:
            continue
        target.default_value = source.default_value
        for link in list(source.links):
            if link.to_socket == source:
                links.new(link.from_socket, target)

    emission = emission_node(material)
    if emission is not None:
        for source, target in ((emission.inputs.get('Color'), principled.inputs.get('Emission Color')),
                               (emission.inputs.get('Strength'), principled.inputs.get('Emission Strength'))):
            if source is None or target is None:
                continue
            if not source.is_linked:
                target.default_value = source.default_value
            for link in list(source.links):
                if link.to_socket == source:
                    links.new(link.from_socket, target)

    # The Glass+Add(…, Emission) endpoint composition collapses: Principled
    # carries emission internally, so the Add node is obsolete glass glue.
    outputs = active_material_output(material)
    for to_socket in surface_sockets:
        to_node = to_socket.node
        if to_node.type == 'ADD_SHADER':
            for link in list(to_node.outputs[0].links):
                links.remove(link)
            nodes.remove(to_node)
    if outputs is not None:
        links.new(principled.outputs['BSDF'], outputs.inputs['Surface'])
    nodes.remove(glass)
    if emission is not None:
        nodes.remove(emission)
    remove_volume_absorption(material)
    return principled
