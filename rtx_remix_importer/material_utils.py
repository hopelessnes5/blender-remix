import bpy
import os
import math
import hashlib
import json
try:
    from pxr import Usd, UsdShade, Sdf, Gf
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False

if USD_AVAILABLE:
    from .usd_utils import get_shader_from_material, get_input_value
    from .texture_utils import load_texture, resolve_material_asset_path, has_meaningful_alpha
    from . import constants
    from .core_utils import set_material_blend_method_compatible

# Cache for Blender materials to avoid redundant creation
# Key: unique identifier (e.g., base_path + metadata_hash)
# Value: bpy.types.Material
_material_cache = {}

# Global material cache for cross-import reuse
# Key: (usd_material_path, texture_context_hash)
# Value: bpy.types.Material
_global_material_cache = {}

def clear_material_cache():
    """Clear the global material cache (and the texture loader cache, since
    factory-settings resets invalidate its cached image datablocks)."""
    global _material_cache, _global_material_cache
    _material_cache.clear()
    _global_material_cache.clear()
    try:
        from .texture_loader import clear_texture_cache
        clear_texture_cache()
    except ImportError:
        pass

def _generate_material_cache_key(usd_material_path, usd_file_path_context):
    """Generate a cache key for materials based on USD path and texture context."""
    import hashlib
    
    # Create a hash of the texture context directory to handle different capture folders
    context_dir = os.path.dirname(usd_file_path_context) if usd_file_path_context else ""
    context_hash = hashlib.md5(context_dir.encode('utf-8')).hexdigest()[:8]
    
    return f"{usd_material_path}#{context_hash}"


def _cache_entry_alive(datablock):
    """Validate a cached bpy datablock reference.

    Blender purges datablocks between scene resets (e.g. factory-settings
    reloads between test cases); cached entries then hold dead StructRNA
    references, and reading any property on them raises ReferenceError.
    """
    try:
        return bool(datablock) and datablock.name in bpy.data.materials
    except ReferenceError:
        return False


def set_principled_transmission(node, value):
    """Set transmission value on Principled BSDF, handling different Blender versions."""
    if 'Transmission Weight' in node.inputs:
        node.inputs['Transmission Weight'].default_value = value
    elif 'Transmission' in node.inputs:
        node.inputs['Transmission'].default_value = value
    else:
        print(f"Warning: No transmission input found on {node.name}")

def get_principled_transmission(node):
    """Get transmission value from Principled BSDF, handling different Blender versions."""
    if 'Transmission Weight' in node.inputs:
        return node.inputs['Transmission Weight']
    elif 'Transmission' in node.inputs:
        return node.inputs['Transmission']
    return None


def _get_socket(node, *names):
    """Return the first existing input socket among *names* (version-tolerant lookup)."""
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    return None


def _create_principled_material(name, is_translucent=False):
    """
    Creates a Blender material built around a standard Principled BSDF node.

    Translucent materials are represented by Principled's transmission layer
    (Transmission Weight = 1.0, IOR = 1.3) instead of a custom node group, so the
    resulting node graph is the plain Blender-native setup every other tool
    understands.

    Returns:
        tuple: (bpy.types.Material, bpy.types.Node) - the material and its
        Principled BSDF node.
    """
    mat = bpy.data.materials.new(name=name)
    try:
        mat.id_properties_ensure()  # Blender 5.0+ requirement
    except AttributeError:
        pass  # Older Blender versions
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output_node = nodes.new(type='ShaderNodeOutputMaterial')
    output_node.location = (300, 0) # Output to the right

    bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
    bsdf.location = (0, 0)
    links.new(bsdf.outputs['BSDF'], output_node.inputs['Surface'])

    if is_translucent:
        set_principled_transmission(bsdf, 1.0)
        ior_socket = _get_socket(bsdf, 'IOR')
        if ior_socket is not None:
            ior_socket.default_value = 1.3

    return mat, bsdf


def create_default_blender_material(name):
    """Creates a Blender material with a standard opaque Principled BSDF setup."""
    return _create_principled_material(name, is_translucent=False)


def create_translucent_blender_material(name):
    """Creates a Blender material with a transmission Principled BSDF setup."""
    return _create_principled_material(name, is_translucent=True)


def _is_translucent_shader(shader):
    """Detect an AperturePBR_Translucent shader from its MDL source asset or
    translucent-specific inputs (fallback for override-only shader blocks)."""
    mdl_source_asset = get_input_value(shader, "info:mdl:sourceAsset")
    if mdl_source_asset and "AperturePBR_Translucent" in str(mdl_source_asset):
        return True
    if mdl_source_asset is None:
        # No MDL attribute (override shader block): fall back to translucent
        # input names the AperturePBR_Translucent MDL requires.
        for name in ("transmittance_texture", "inputs:transmittance_texture",
                     "transmittance_color", "inputs:transmittance_color",
                     "thin_walled", "inputs:thin_walled"):
            if get_input_value(shader, name) is not None:
                return True
    return False


def detect_shader_material_kind(shader):
    """Map an AperturePBR shader to the Principled representation to build:
    'translucent' when the MDL is AperturePBR_Translucent, 'opaque' otherwise."""
    return "translucent" if _is_translucent_shader(shader) else "opaque"


# Create material from USD path
def create_material(usd_material_path, usd_stage, usd_file_path_context):
    """
    Creates a Blender material from a USD material path using a node setup.

    Args:
        usd_material_path: Path string to the material prim in the USD stage.
        usd_stage: The Usd.Stage containing the material.
        usd_file_path_context: Absolute path to the main imported USD file for resolving relative paths.

    Returns:
        bpy.types.Material: Created or existing Blender material, or None on failure.
    """
    if not USD_AVAILABLE:
        print("USD libraries not available, cannot create materials.")
        return None

    print(f"Processing material path: {usd_material_path}")
    
    # Generate cache key for this material + context combination
    cache_key = _generate_material_cache_key(usd_material_path, usd_file_path_context)
    
    # Check global cache first for cross-import reuse
    if cache_key in _global_material_cache:
        cached_material = _global_material_cache[cache_key]
        if _cache_entry_alive(cached_material):
            print(f"Reusing cached material: {cached_material.name} (key: {cache_key})")
            return cached_material
        else:
            # Remove invalid cache entry
            del _global_material_cache[cache_key]
    
    material_prim = usd_stage.GetPrimAtPath(usd_material_path)

    if not material_prim or not material_prim.IsA(UsdShade.Material):
        print(f"WARNING: Material prim not found or invalid at path: {usd_material_path}")
        error_mat_name = f"ERROR_{os.path.basename(usd_material_path)}"
        
        # Check if error material already exists
        if error_mat_name in bpy.data.materials:
            return bpy.data.materials[error_mat_name]
        
        error_mat, bsdf_node = create_default_blender_material(error_mat_name)
        if bsdf_node and bsdf_node.type == 'BSDF_PRINCIPLED':
            bsdf_node.inputs['Base Color'].default_value = (1.0, 0.0, 0.0, 1.0) # Red
        return error_mat

    # Use a Blender-safe name based on the prim name
    material_name = bpy.path.clean_name(material_prim.GetName())
    if not material_name:
        material_name = bpy.path.clean_name(os.path.basename(usd_material_path))

    # Generate unique material name to avoid conflicts across different contexts
    context_suffix = cache_key.split('#')[1] if '#' in cache_key else "default"
    unique_material_name = f"{material_name}_{context_suffix}"
    
    # Check if this specific material already exists
    if unique_material_name in bpy.data.materials:
        existing_material = bpy.data.materials[unique_material_name]
        print(f"Material '{unique_material_name}' already exists, reusing.")
        _global_material_cache[cache_key] = existing_material
        return existing_material

    # Find the actual shader connected to the material surface
    surface_shader = get_shader_from_material(material_prim)
    if not surface_shader:
        print(f"WARNING: No surface shader found for material: {unique_material_name}. Using default Principled BSDF.")
        bl_material, main_shader_node = create_default_blender_material(unique_material_name)
        _global_material_cache[cache_key] = bl_material
        return bl_material # Return the default material

    shader_prim = surface_shader.GetPrim()
    print(f"Found shader '{shader_prim.GetName()}' (type: {shader_prim.GetTypeName()}) for material '{unique_material_name}'")
    
    # Determine material kind based on shader MDL source asset
    is_translucent = _is_translucent_shader(surface_shader)
    if is_translucent:
        print("Material identified as AperturePBR_Translucent")

    # Create an appropriate standard Principled BSDF material
    if is_translucent:
        bl_material, main_shader_node = create_translucent_blender_material(unique_material_name)
    else:
        bl_material, main_shader_node = create_default_blender_material(unique_material_name)
    
    if not main_shader_node: # Check if the Principled BSDF node was created
        print(f"ERROR: Could not create main shader node in new material '{unique_material_name}'.")
        return bl_material # Return the basic material

    # --- DEBUG: Print shader inputs --- #
    print(f"      Available inputs on {shader_prim.GetPath()}:")
    for shader_input in surface_shader.GetInputs():
        print(f"        - {shader_input.GetBaseName()}")
    # --- END DEBUG --- #

    # --- Processing ---
    # Directly process using a standardized PBR approach for all shaders
    process_pbr(surface_shader, bl_material, main_shader_node, usd_file_path_context)
    # -- Processing ---

    # Cache the created material
    _global_material_cache[cache_key] = bl_material
    print(f"Successfully processed and cached material: {unique_material_name} (key: {cache_key})")
    return bl_material


# Input Processor
def process_input(usd_input_value, input_type, nodes, links, target_node, target_socket_name,
                  usd_file_path_context, node_pos=(-400, 0), is_normal=False, is_non_color=False, normal_encoding=None):
    """
    Processes a USD input value and connects it to a Blender node socket with simple layout.
    Places textures to the left of the target node.
    """
    if usd_input_value is None:
        return None # No value to process

    print(f"      Processing input '{input_type}' with value: {usd_input_value}") # LOGGING
    target_socket = target_node.inputs.get(target_socket_name)
    if not target_socket:
        print(f"ERROR: Target socket '{target_socket_name}' not found on node '{target_node.name}'.")
        return None

    created_node = None
    texture_node_x = target_node.location.x - 400 # X position for texture nodes
    normal_map_node_x = target_node.location.x - 150 # X position for normal map node

    # Check if the value is a texture path
    # Relaxed check: assume string value starting with '../' or containing 'assets/' is a texture path
    is_likely_path = False
    if isinstance(usd_input_value, (str, Sdf.AssetPath)):
        path_str = str(usd_input_value)
        # Example USD path: @../assets/models/....dds@ -> remove @ symbols
        if path_str.startswith('@') and path_str.endswith('@'):
            path_str = path_str[1:-1]

        is_likely_path = '../' in path_str or 'assets/' in path_str or \
                         any(path_str.lower().endswith(ext) for ext in ['.dds', '.png', '.jpg', '.jpeg', '.tga', '.bmp', '.tiff'])

    print(f"        is_likely_path check result: {is_likely_path}") # LOGGING

    if is_likely_path:
        texture_path = str(usd_input_value)
        # Remove potential wrapper characters like '@' if present
        if texture_path.startswith('@') and texture_path.endswith('@'):
            texture_path = texture_path[1:-1]

        print(f"  Processing '{input_type}' as texture: {texture_path}")
        resolved_path = resolve_material_asset_path(texture_path, usd_file_path_context)

        if resolved_path and os.path.exists(resolved_path):
            # Use existing texture loading function
            image = load_texture(resolved_path, is_normal=is_normal, is_non_color=is_non_color)
            if image:
                if is_normal:
                    from .normal_import import decode_to_opengl
                    image = decode_to_opengl(image, normal_encoding)
                tex_node = nodes.new(type='ShaderNodeTexImage')
                tex_node.image = image
                tex_node.label = f"{input_type.replace('_', ' ').title()} Texture"
                # Position texture node to the left, using the provided y offset
                tex_node.location = (texture_node_x, node_pos[1])

                output_socket_name = 'Color'
                if is_non_color or is_normal:
                    # Ensure correct color space for non-color data
                    image.colorspace_settings.name = 'Non-Color'

                # Special handling for Normal Maps
                if is_normal:
                    normal_map_node = nodes.new(type='ShaderNodeNormalMap')
                    normal_map_node.location = (normal_map_node_x, node_pos[1])
                    # Connect Texture -> Normal Map Node -> Target Socket
                    links.new(tex_node.outputs[output_socket_name], normal_map_node.inputs['Color'])
                    links.new(normal_map_node.outputs['Normal'], target_socket)
                    created_node = normal_map_node # Return the normal map node
                elif is_non_color and target_socket.type == 'VALUE':
                    from .scalar_texture import connect_scalar_red
                    connect_scalar_red(nodes, links, tex_node, target_socket)
                    created_node = tex_node
                else:
                    # Direct connection for other textures
                    links.new(tex_node.outputs[output_socket_name], target_socket)
                    created_node = tex_node # Return the image texture node
            else:
                print(f"  Warning: Failed to load texture for '{input_type}' from resolved path: {resolved_path}")
        else:
            print(f"  Warning: Texture path not found or invalid for '{input_type}': {resolved_path} (Original: {texture_path})")

    # Handle constant values (Color, Float, Int, Bool)
    elif isinstance(usd_input_value, Gf.Vec3f) and target_socket.type == 'RGBA':
        color = usd_input_value
        target_socket.default_value = (color[0], color[1], color[2], 1.0)
        print(f"  Set '{target_socket_name}' to color value: {target_socket.default_value[:3]}")
    elif isinstance(usd_input_value, Gf.Vec4f) and target_socket.type == 'RGBA':
         color = usd_input_value
         target_socket.default_value = tuple(color)
         print(f"  Set '{target_socket_name}' to color value: {target_socket.default_value}")
    elif isinstance(usd_input_value, (int, float)) and target_socket.type == 'VALUE':
        target_socket.default_value = float(usd_input_value)
        print(f"  Set '{target_socket_name}' to scalar value: {target_socket.default_value}")
    elif isinstance(usd_input_value, bool) and target_socket.type == 'VALUE':
        target_socket.default_value = 1.0 if usd_input_value else 0.0
        print(f"  Set '{target_socket_name}' to boolean value: {target_socket.default_value}")
    else:
        # Type mismatch or unhandled type - attempt conversion for basic types if possible
        try:
            if target_socket.type == 'VALUE' and isinstance(usd_input_value, (int, float, bool)):
                 target_socket.default_value = float(usd_input_value)
                 print(f"  Set '{target_socket_name}' to converted scalar value: {target_socket.default_value}")
            # Add other potential conversions if needed
            else:
                 print(f"  Notice: Input '{input_type}' has value '{usd_input_value}' (type: {type(usd_input_value)}), "
                       f"but target socket '{target_socket_name}' expects type '{target_socket.type}'. Skipping direct set.")
        except Exception as e:
             print(f"  Notice: Could not convert input '{input_type}' value '{usd_input_value}' for socket '{target_socket_name}'. Error: {e}. Skipping.")


    return created_node # Return the image/normal node if created


# PBR Processor
def process_pbr(shader, bl_material, shader_node, usd_file_path_context):
    """Processes AperturePBR shader inputs onto a standard Principled BSDF node.

    The material is represented either as an opaque Principled BSDF or a
    transmission Principled BSDF (AperturePBR_Translucent): transmission
    materials read their diffuse layer from transmittance inputs and drive
    Principled's IOR directly.
    """
    nodes = bl_material.node_tree.nodes
    links = bl_material.node_tree.links
    print(f"    Processing PBR inputs for shader: {shader.GetPath()} onto node: {shader_node.name}") # LOGGING

    # Determine if this is a translucent material based on the shader MDL
    is_translucent = _is_translucent_shader(shader)
    
    if is_translucent:
        # Principled input map for AperturePBR_Translucent -> transmission BSDF
        input_map = {
            "Base Color": ["inputs:transmittance_texture", "transmittance_texture", "inputs:transmittance_color", "transmittance_color"],
            "IOR": ["inputs:ior_constant", "ior_constant"],
            "Normal": ["inputs:normalmap_texture", "normalmap_texture"],
            "Emission Color": ["inputs:emissive_color", "emissive_color"],
            "Emission Strength": ["inputs:emissive_intensity", "emissive_intensity"],
        }
    else:
        # Principled input map for AperturePBR (opaque family)
        input_map = {
            "Base Color": ["inputs:diffuse_texture", "diffuse_texture", "diffuse_color_constant"],
            "Roughness": ["inputs:reflectionroughness_texture", "reflectionroughness_texture", "reflection_roughness_constant"],
            "Metallic": ["inputs:metallic_texture", "metallic_texture", "metallic_constant"],
            "Normal": ["inputs:normalmap_texture", "normalmap_texture"],

            # Emission (matching export.json)
            "Emission Color": ["inputs:emissive_mask_texture", "emissive_mask_texture", "emissive_color_constant"],
            "Emission Strength": ["inputs:emissive_intensity", "emissive_intensity"],
        }

    # Y position for texture nodes will be relative to the shader_node
    base_y_pos = shader_node.location.y

    y_pos_offset = 200 # Initial Y offset from shader_node for the first texture
    texture_node_spacing = 250 # Vertical spacing between texture nodes

    # Process each PBR input
    for principled_socket_name, usd_input_names in input_map.items():
        target_socket = _get_socket(shader_node, principled_socket_name)
        if not target_socket:
            # print(f" Socket '{principled_socket_name}' not found on '{shader_node.name}', skipping.")
            continue # Skip if socket doesn't exist

        print(f"      Checking input for socket: '{principled_socket_name}'") # LOGGING
        input_value = None
        found_name = None
        for name in usd_input_names:
            input_value = get_input_value(shader, name)
            if input_value is not None:
                found_name = name
                # Special case: if enable_emission is false, ignore emission inputs
                if principled_socket_name in ["Emission Color", "Emission Strength"]:
                    # Reliably check the original USD 'inputs:enable_emission'
                    usd_enable_emission_val = get_input_value(shader, "inputs:enable_emission")
                    if isinstance(usd_enable_emission_val, bool) and not usd_enable_emission_val:
                        print("  Emission disabled via USD 'inputs:enable_emission', skipping emission inputs.")
                        input_value = None # Force skip this input
                        break # Don't check other names for this socket
                
                if input_value is not None:
                    break # Use the first value found


        if input_value is not None:
            is_normal = (principled_socket_name == "Normal") # -> connect via Normal Map node
            # Identify non-color data sockets based on common PBR conventions
            is_non_color = principled_socket_name in ["Metallic", "Roughness", "Emission Strength"] # Add others if needed

            print(f"        Found value for '{found_name}': {input_value} (Type: {type(input_value)}) for socket '{principled_socket_name}'") # LOGGING
            # Calculate node position for textures relative to the main shader_node
            node_y_pos = base_y_pos + y_pos_offset

            from .normal_import import effective_encoding
            created_node = process_input(
                input_value, found_name, nodes, links, shader_node, principled_socket_name,
                usd_file_path_context, node_pos=(-400, node_y_pos), # X is hardcoded, Y is dynamic
                is_normal=is_normal, 
                is_non_color=is_non_color,
                normal_encoding=effective_encoding(shader) if is_normal else None,
            )

            # If a texture or normal map node was created, decrease y_pos_offset for the next one
            if created_node:
                y_pos_offset -= texture_node_spacing


    # --- Opacity / Alpha Handling ---
    # UsdPreviewSurface-like 'inputs:opacity' / opacity textures also land on
    # Principled's Alpha socket for AperturePBR_Opacity materials.
    alpha_socket = _get_socket(shader_node, "Alpha")
    usd_enable_opacity_val = get_input_value(shader, "inputs:enable_opacity")
    if alpha_socket is not None and usd_enable_opacity_val is True:
        opacity_input_map = ["inputs:opacity_texture", "opacity_texture", "opacity_constant", "inputs:opacity", "opacity"]
        input_value, found_name = None, None
        for name in opacity_input_map:
            input_value = get_input_value(shader, name)
            if input_value is not None:
                found_name = name
                break
        if input_value is not None:
            print(f"      Found opacity value for '{found_name}': {input_value} -> Alpha socket") # LOGGING
            node_y_pos = base_y_pos + y_pos_offset
            created_node = process_input(
                input_value, found_name, nodes, links, shader_node, "Alpha",
                usd_file_path_context, node_pos=(-400, node_y_pos),
                is_normal=False, is_non_color=True
            )
            if created_node:
                y_pos_offset -= texture_node_spacing

    # FIX (v2): earlier attempt gated this on per-instance _remix_metadata
    # (alphaBlendEnabled/alphaTestEnabled), which isn't always present, causing
    # real transparency to go missing. The actual, reliable signal is simpler and
    # lives right on the material's shader: RTX Remix captures author
    # `inputs:enable_opacity` (bool). Use that as the source of truth.
    albedo_socket = _get_socket(shader_node, "Base Color")
    # FIX (v3): "alpha on everything" regression. Two things rolled back here:
    # 1) force_opacity (AperturePBR_Opacity.mdl detection) is removed - per the
    #    real .mdl source, AperturePBR_Opacity is an authoring/ingestion-time
    #    convenience shader (used by the material converter), not something
    #    captured game materials reference; captures use AperturePBR_Model
    #    directly with enable_opacity as a real per-material bool. Gating on the
    #    mdl asset string was producing false positives.
    # 2) the enable_opacity check is now strict (`is True`) rather than merely
    #    truthy, in case an unauthored input resolves to some non-bool truthy
    #    sentinel via NDR/MDL schema defaults rather than a real Python bool -
    #    logged below so this can be confirmed precisely if it still misfires.
    if usd_enable_opacity_val is not None and not isinstance(usd_enable_opacity_val, bool):
        print(f"  [diagnostic] inputs:enable_opacity on '{shader.GetPrim().GetPath()}' resolved to non-bool value: {usd_enable_opacity_val!r} (type={type(usd_enable_opacity_val).__name__})")
    should_use_alpha_opacity = usd_enable_opacity_val is True

    if should_use_alpha_opacity and alpha_socket and not alpha_socket.is_linked and albedo_socket and albedo_socket.is_linked:
        albedo_node = albedo_socket.links[0].from_node
        if albedo_node.type == 'TEX_IMAGE' and 'Alpha' in albedo_node.outputs and albedo_node.image:
            if has_meaningful_alpha(albedo_node.image):
                print(f"  Connecting Alpha from Base Color texture ('{albedo_node.image.name}') to Alpha socket (enable_opacity={usd_enable_opacity_val})")
                links.new(albedo_node.outputs['Alpha'], alpha_socket)
            else:
                print(f"  Skipping alpha connection for '{albedo_node.image.name}' - enable_opacity=True but texture alpha is uniform")
    elif alpha_socket and not alpha_socket.is_linked and not should_use_alpha_opacity:
        print(f"  Not connecting alpha to Alpha socket - inputs:enable_opacity is not True on this material ({usd_enable_opacity_val!r})")

    # --- Emission Strength (if enable_emission is true on the USD shader) ---
    # Principled exposes "Emission Color" and "Emission Strength". If the
    # AperturePBR shader enables emission but only authors a color/mask while
    # leaving the intensity unset, default the strength to 1.0 so the emission
    # layer is actually visible.
    emissive_color_socket = _get_socket(shader_node, "Emission Color", "Emissive Color")
    emissive_intensity_socket = _get_socket(shader_node, "Emission Strength", "Emissive Intensity")
    enable_emission_socket = _get_socket(shader_node, "Enable Emission") # Legacy group sockets may still carry this

    # If Emission Color is linked, and Strength isn't, and (Enable Emission is true OR not present)
    if emissive_color_socket and emissive_intensity_socket and \
       (emissive_color_socket.is_linked or get_input_value(shader, "inputs:enable_emission") is True) and not emissive_intensity_socket.is_linked:
        
        emission_is_enabled_by_group_input = True # Assume enabled if socket doesn't exist or is not 0
        if enable_emission_socket:
            # Check the default_value of the "Enable Emission" socket on the node
            if isinstance(enable_emission_socket.default_value, (float, int)) and enable_emission_socket.default_value == 0:
                emission_is_enabled_by_group_input = False
            elif isinstance(enable_emission_socket.default_value, bool) and not enable_emission_socket.default_value:
                 emission_is_enabled_by_group_input = False

        # Also check the original USD 'inputs:enable_emission' as a primary source of truth
        usd_enable_emission_val = get_input_value(shader, "inputs:enable_emission")
        explicitly_disabled_by_usd = isinstance(usd_enable_emission_val, bool) and not usd_enable_emission_val

        if not explicitly_disabled_by_usd and emission_is_enabled_by_group_input:
            if emissive_intensity_socket.default_value == 0.0:
                 emissive_intensity_socket.default_value = 1.0 # Default to 1.0
                 print("  Set Emission Strength to 1.0 as Emission Color is present and emission is not explicitly disabled.")

    if is_translucent:
        for name in ('thin_walled', 'thin_wall_thickness', 'transmittance_measurement_distance'):
            value = get_input_value(shader, 'inputs:' + name)
            if isinstance(value, (bool, int, float)):
                bl_material['rtx_' + name] = value

    if not is_translucent:
        from .displacement import apply_imported_displacement, load_height_image
        height = get_input_value(shader, 'inputs:height_texture')
        inward = get_input_value(shader, 'inputs:displace_in')
        outward = get_input_value(shader, 'inputs:displace_out')
        image = None
        if height:
            path = (height.resolvedPath or height.path) if isinstance(height, Sdf.AssetPath) else str(height).strip('@')
            source_context = usd_file_path_context
            height_input = shader.GetInput('height_texture')
            if height_input:
                for spec in height_input.GetAttr().GetPropertyStack():
                    if spec.HasInfo('default') and spec.layer.realPath:
                        source_context = spec.layer.realPath
                        break
            resolved = resolve_material_asset_path(path, source_context)
            image = load_height_image(resolved)
            if image is None:
                print(f'  Warning: could not load height texture {path}')
        try:
            apply_imported_displacement(bl_material, inward, outward, image)
        except ValueError as error:
            print(f'  Warning: could not apply displacement: {error}')


# --- New Main Function ---
def get_or_create_instance_material(base_material_path, instance_metadata, usd_stage, usd_file_path_context, material_cache):
    """
    Gets an existing Blender material or creates a new one based on the base USD material
    path and instance-specific metadata overrides.

    Args:
        base_material_path (str): USD path to the base material prim.
        instance_metadata (dict): Dictionary of _remix_metadata overrides for this instance.
        usd_stage (Usd.Stage): The USD stage.
        usd_file_path_context (str): Absolute path of the main imported USD file.
        material_cache (dict): Cache dictionary to store/retrieve created materials.

    Returns:
        bpy.types.Material: The resulting Blender material, or None.
    """
    if not USD_AVAILABLE:
        return None

    # --- Generate Unique Key/Name ---
    metadata_hash = ""
    if instance_metadata: # Only hash if metadata is present and non-empty
        # Sort the dictionary by key for consistent hashing
        sorted_meta_string = json.dumps(instance_metadata, sort_keys=True)
        metadata_hash = hashlib.md5(sorted_meta_string.encode('utf-8')).hexdigest()[:8] # Short hash

    # Generate base cache key
    base_cache_key = _generate_material_cache_key(base_material_path, usd_file_path_context)
    unique_key = f"{base_cache_key}_{metadata_hash}" if metadata_hash else base_cache_key

    # --- Check Cache ---
    if unique_key in material_cache:
        print(f"  Found cached material for key: {unique_key} -> '{material_cache[unique_key].name}'")
        return material_cache[unique_key]
    
    # Also check global cache
    if unique_key in _global_material_cache:
        cached_material = _global_material_cache[unique_key]
        if _cache_entry_alive(cached_material):
            print(f"  Found globally cached material for key: {unique_key} -> '{cached_material.name}'")
            material_cache[unique_key] = cached_material  # Add to local cache too
            return cached_material
        else:
            # Remove invalid cache entry
            del _global_material_cache[unique_key]

    print(f"  Processing material for key: {unique_key}")

    # --- Get or Create Base Material --- #
    base_bl_material = create_material(base_material_path, usd_stage, usd_file_path_context)
    if not base_bl_material:
        print(f"    ERROR: Failed to create base material for {base_material_path}")
        return None

    # Find the Principled BSDF node in the base material
    base_shader_node = _find_principled_node(base_bl_material)

    if not base_shader_node:
        print(f"    ERROR: Could not find shader node in base material '{base_bl_material.name}'")
        return base_bl_material

    # --- Apply Overrides (if metadata exists) ---
    final_bl_material = base_bl_material
    if metadata_hash: # Needs override
        print(f"    Applying metadata overrides (hash: {metadata_hash})")
        # Generate unique material name
        unique_material_name = f"{base_bl_material.name}_{metadata_hash}"

        # Check if this specific override already exists
        if unique_material_name in bpy.data.materials:
            print(f"    Found existing overridden material: {unique_material_name}")
            final_bl_material = bpy.data.materials[unique_material_name]
        else:
            print(f"    Duplicating base '{base_bl_material.name}' to '{unique_material_name}'")
            final_bl_material = base_bl_material.copy()
            final_bl_material.name = unique_material_name

            # Find the Principled BSDF node in the duplicated material
            duplicated_shader_node = _find_principled_node(final_bl_material)
            
            if duplicated_shader_node:
                apply_metadata_overrides(instance_metadata, final_bl_material, duplicated_shader_node)
            else:
                print(f"    ERROR: Could not find shader node in duplicated material '{unique_material_name}'")
                final_bl_material = base_bl_material # Fallback

    # --- Cache and Return ---
    material_cache[unique_key] = final_bl_material
    _global_material_cache[unique_key] = final_bl_material  # Also cache globally
    print(f"  Material finalized and cached: '{final_bl_material.name}'")
    return final_bl_material


def _find_principled_node(bl_material):
    """Return the main Principled BSDF node of a material (or None).

    Legacy materials built with the old Aperture node groups fall back to the
    group node so per-instance metadata can still land somewhere sensible.
    """
    if not bl_material.node_tree:
        return None

    principled = None
    group = None
    for node in bl_material.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            principled = node
            break
        elif node.type == 'GROUP' and node.node_tree and group is None:
            if "Aperture" in node.node_tree.name:
                group = node
    if principled is None:
        principled = group
    return principled


# --- Base Material Creation ---
def create_base_material_nodes(usd_material_path, usd_stage, usd_file_path_context):
    """
    Creates a Blender material with nodes based *only* on the USD material prim,
    without applying instance metadata.

    Returns:
        tuple: (bpy.types.Material, shader_node) or None on failure.
    """
    material_prim = usd_stage.GetPrimAtPath(usd_material_path)
    if not material_prim or not material_prim.IsA(UsdShade.Material):
        print(f"    WARNING: Base material prim not found or invalid at path: {usd_material_path}")
        return None # Cannot create base

    material_name = bpy.path.clean_name(material_prim.GetName())
    if not material_name:
        material_name = bpy.path.clean_name(os.path.basename(usd_material_path))

    # Get existing or create new Blender material
    if material_name in bpy.data.materials:
        bl_material = bpy.data.materials[material_name]
        # Ensure it has nodes and a Principled BSDF (or rebuild the tree)
        if not bl_material.use_nodes:
            bl_material.use_nodes = True
        if not bl_material.node_tree:
            bl_material, shader_node = create_default_blender_material(material_name) # Rebuild if no tree
        else:
            shader_node = _find_principled_node(bl_material)
            if not shader_node: # Still not found, create default setup
                 bl_material, shader_node = create_default_blender_material(material_name)

        print(f"    Reusing existing material: '{material_name}' with shader node '{shader_node.name if shader_node else 'None'}'")

    else:
        # Create new Blender material if it doesn't exist
        bl_material, shader_node = create_default_blender_material(material_name)
        if not shader_node: # Should be guaranteed by create_default_blender_material
            print(f"    ERROR: Could not create main shader node in new material '{material_name}'.")
            return None
        print(f"    Created new material: '{material_name}' with shader node '{shader_node.name}'")

    # Find the USD surface shader
    surface_shader = get_shader_from_material(material_prim)
    if not surface_shader:
        print(f"    WARNING: No surface shader found for material: {material_name}. Using default setup.")
        return bl_material, shader_node # Return default setup

    shader_prim = surface_shader.GetPrim()
    print(f"    Found shader '{shader_prim.GetName()}' for base material '{material_name}'")

    # --- DEBUG: Print shader inputs --- #
    print(f"      Available inputs on {shader_prim.GetPath()}:")
    for shader_input in surface_shader.GetInputs():
        print(f"        - {shader_input.GetBaseName()}")
    # --- END DEBUG --- #

    # Process PBR inputs (this populates the node tree)
    process_pbr(surface_shader, bl_material, shader_node, usd_file_path_context)

    return bl_material, shader_node # Return the Principled BSDF node

# --- New Metadata Application Function ---
def apply_metadata_overrides(metadata, bl_material, shader_node):
    """
    Modifies a Blender material based on Remix metadata overrides.
    Operates on the provided Principled BSDF node.
    """
    nodes = bl_material.node_tree.nodes
    links = bl_material.node_tree.links
    print(f"    Applying overrides to {bl_material.name} using shader node {shader_node.name}...")

    # --- Alpha Blending --- #
    alpha_blend_enabled = metadata.get('alphaBlendEnabled', 0) == 1
    alpha_test_enabled = metadata.get('alphaTestEnabled', 0) == 1

    if alpha_blend_enabled:
        set_material_blend_method_compatible(bl_material, 'BLEND', 'HASHED')
        print(f"      Set blend_method=BLEND, shadow_method=HASHED")
    elif alpha_test_enabled:
        alpha_threshold = metadata.get('alphaTestReferenceValue', 0) / 255.0
        set_material_blend_method_compatible(bl_material, 'CLIP', 'CLIP', alpha_threshold)
        print(f"      Set blend_method=CLIP, shadow_method=CLIP, threshold={alpha_threshold:.3f}")
    else:
        set_material_blend_method_compatible(bl_material, 'OPAQUE', 'OPAQUE')


    tex_color_op = metadata.get('textureColorOperation')
    tex_alpha_op = metadata.get('textureAlphaOperation')

    if tex_color_op == 4:
        color_socket = shader_node.inputs.get("Base Color")

        if color_socket and color_socket.is_linked:
            tex_node = color_socket.links[0].from_node
            if tex_node.type == 'TEX_IMAGE':
                print(f"      TextureColorOperation: MODULATE detected for 'Base Color' - using direct texture connection (no multiply node)")

    # --- Handle Alpha Operation --- #
    # FIX: this is now gated primarily by the CAPTURED render state
    # (alphaBlendEnabled / alphaTestEnabled - the game's actual fixed-function alpha
    # test/blend flags at capture time), not just by textureAlphaOperation being
    # exactly D3DTOP_SELECTARG1. Many captures set blend/test enabled without
    # populating textureAlphaOperation at all; requiring == 1 silently skipped those.
    # has_meaningful_alpha() is still used as a secondary sanity check so we don't
    # connect a flat/constant alpha channel even when blending is nominally on.
    uses_alpha_transparency = alpha_blend_enabled or alpha_test_enabled
    alpha_op_selects_texture = tex_alpha_op is None or tex_alpha_op == 1  # 1 = D3DTOP_SELECTARG1 (use texture alpha)

    if uses_alpha_transparency and alpha_op_selects_texture:
        alpha_socket = shader_node.inputs.get("Alpha")
        color_socket = shader_node.inputs.get("Base Color")

        if alpha_socket and not alpha_socket.is_linked and color_socket and color_socket.is_linked:
            incoming_node = color_socket.links[0].from_node

            if incoming_node.type == 'TEX_IMAGE' and 'Alpha' in incoming_node.outputs and incoming_node.image:
                # Check if the texture actually has meaningful alpha data
                if has_meaningful_alpha(incoming_node.image):
                    print(f"      Connecting texture alpha to 'Alpha' - capture render state uses alpha transparency (blend={alpha_blend_enabled}, test={alpha_test_enabled})")
                    links.new(incoming_node.outputs['Alpha'], alpha_socket)
                else:
                    print(f"      Skipping alpha connection for 'Alpha' - texture alpha is uniform despite blend/test being enabled")
    elif shader_node.inputs.get("Alpha") is not None:
        print(f"      Not connecting alpha - capture render state doesn't use alpha transparency here (blend={alpha_blend_enabled}, test={alpha_test_enabled}, textureAlphaOp={tex_alpha_op})")


    print(f"      TODO: Implement handling for textureColorOp={tex_color_op}, textureAlphaOp={tex_alpha_op}")
