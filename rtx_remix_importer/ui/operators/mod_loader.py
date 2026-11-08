"""
Improved mod.usda loading system based on NVIDIA RTX Remix toolkit approach.

This module provides a more efficient and reliable way to load mod file changes
by using USD's layer composition system properly and only processing authored changes.
"""

import bpy
import mathutils
import os
import re
import math
from typing import Dict, List, Optional, Set, Tuple
from ... import mod_apply_utils
from ...mesh_uv_preservation import (
    copy_uv_layers_if_compatible, same_uv_layout,
)
from ...core_utils import (
    calc_normals_split_compatible,
    set_mesh_auto_smooth_compatible,
    set_custom_normals_compatible
)
from ...nvidia_core import constants
from ...light_sharing import (cone_spot_size, light_parameter_signature,
                              sphere_cone_parameters, prototype_light_path)

try:
    from pxr import Usd, Sdf, UsdGeom, UsdShade, Vt, UsdLux, Gf
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False
    Usd = Sdf = UsdGeom = UsdShade = Vt = UsdLux = Gf = None


def _hash_tokens(text) -> Set[str]:
    """16-character capture-hash tokens in *text* (RTX Remix game asset namespace).

    Replacements, capture instances and asset files share this hash format
    (``mesh_<16 HEX>``, ``inst_<16 HEX>[_N]``). Toolkit-authored names such as
    ``ref_<32 lowercase hex>`` or ``mat_<32 lowercase hex>`` are a different
    namespace and must not match.
    """
    if not text:
        return set()
    return {
        token for token in re.findall(r'[A-F0-9]{16}', str(text))
        if any(char.isdigit() for char in token)
    }


_SKY_MESH_NAME = re.compile(r'^sky_([0-9a-fA-F]{16})(?=$|[_.])', re.IGNORECASE)
_PROTOTYPE_PATH = re.compile(
    r'^/RootNode/(?:meshes|sky|skies)/(?:mesh|sky)_[0-9A-Fa-f]{16}(?:_[0-9]+)?$'
)


def _sky_object_hashes(obj) -> Set[str]:
    """Capture sky objects are often named ``sky_<HASH>``, not ``inst_<HASH>``.

    These names are an explicit fallback for old capture imports lacking USD
    instance/prototype tags. Only MESH objects with a strict sky-prefix/hash
    are accepted; arbitrary 16-hex strings in user object names are ignored.
    """
    if getattr(obj, 'type', None) != 'MESH':
        return set()
    result = set()
    for datablock in (obj, getattr(obj, 'data', None)):
        match = _SKY_MESH_NAME.match(str(getattr(datablock, 'name', '') or ''))
        if match:
            result.add(match.group(1).upper())
    return result


def _prototype_container_path(path: str) -> bool:
    """Toolkit mesh prototypes, plus captured sky-prefixed mesh containers."""
    return bool(_PROTOTYPE_PATH.fullmatch(str(path)))


def _ancestor_prototype_path(path: str) -> Optional[str]:
    """Prototype path enclosing a USD child, without treating arbitrary assets as prototypes."""
    components = str(path).split('/')
    for length in range(len(components), 3, -1):
        candidate = '/'.join(components[:length])
        if _prototype_container_path(candidate):
            return candidate
    return None


class ModFileLoader:
    """
    Loads and applies changes from mod.usda files using USD's layer composition.
    
    Based on NVIDIA's approach:
    1. Opens mod.usda as a stage (USD handles sublayers automatically)
    2. Uses USD's composition to get the final resolved values
    3. Only processes prims with authored overrides
    4. Efficiently matches existing Blender objects
    """
    
    def __init__(self, context: bpy.types.Context, operator: bpy.types.Operator):
        self.context = context
        self.operator = operator
        self.stage = None
        self.time_code = Usd.TimeCode.Default()
        self.xform_cache = None
        self.up_axis_is_y = False
        
        # Caches
        self.blender_object_map: Dict[str, bpy.types.Object] = {}
        self.material_cache: Dict[str, bpy.types.Material] = {}
        self.base_material_node_cache: Dict[str, any] = {}

        # Mod-relative linking state for one Apply run.
        self.mod_collection: Optional[bpy.types.Collection] = None
        self._routed_container_paths: Set[str] = set()
        self._replaced_prototype_hashes: Set[str] = set()
        # ``mesh_<HASH>``-paths the mod authored content on (defs or authored
        # ``references`` opinions). Set in ``_get_changed_prims``.
        self._mod_authored_prototype_paths: Set[str] = set()
        self.scene_related_materials: Set[str] = set()
        # Prim paths the material-binding scan pulled into the change set
        # only because a bound material changed. Their processing is
        # material-only: applying transforms there once collapsed bound
        # placements to (0, 0, 0).
        self._material_scan_prim_paths: Set[str] = set()
        # Prototype lights are authored once but must be displayed for every
        # captured inst_<HASH> placement. Keep this state local to an Apply run.
        self._routed_light_instances: Set[Tuple[str, str]] = set()
        self._light_stage = None
        self._light_stage_root = None  # Keep anonymous composition layer alive.
        self._light_xform_cache = None
        self._capture_light_stage = None
        # The same composed prototype Mesh is one Blender datablock even when
        # the mod adds several placements for it.
        self._new_placement_mesh_data = {}
        
        # Statistics
        self.stats = {
            'prims_processed': 0,
            'objects_updated': 0,
            'objects_created': 0,
            'materials_applied': 0,
            'lights_updated': 0
        }
    
    def report(self, level: set, message: str):
        """Helper to report messages through the operator."""
        self.operator.report(level, message)
    
    def load_mod_file(self, mod_file_path: str) -> bool:
        """
        Load a mod.usda file and apply all changes to the current scene.
        
        Args:
            mod_file_path: Absolute path to the mod.usda file
            
        Returns:
            True if successful, False otherwise
        """
        if not USD_AVAILABLE:
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return False
        
        if not os.path.exists(mod_file_path):
            self.report({'ERROR'}, f"Mod file not found: {mod_file_path}")
            return False
        
        # Store for later use
        self.mod_file_path = mod_file_path
        self._light_stage = None
        self._light_stage_root = None
        self._light_xform_cache = None
        self._capture_light_stage = None
        self._routed_light_instances = set()
        self._new_placement_mesh_data = {}

        try:
            # Step 1: Open the mod file stage (USD loads all sublayers automatically)
            self.report({'INFO'}, f"Opening mod file: {os.path.basename(mod_file_path)}")
            self.stage = Usd.Stage.Open(mod_file_path, Usd.Stage.LoadAll)
            
            if not self.stage:
                self.report({'ERROR'}, f"Failed to open USD stage: {mod_file_path}")
                return False
            
            # Step 2: Setup transform cache and detect up-axis
            self.xform_cache = UsdGeom.XformCache(self.time_code)
            mod_up_axis = UsdGeom.GetStageUpAxis(self.stage)
            self.up_axis_is_y = (mod_up_axis == UsdGeom.Tokens.y)
            
            if self.up_axis_is_y:
                self.report({'INFO'}, "Mod file is Y-Up, will convert to Z-Up")
            
            # Step 3: Build map of existing Blender objects
            self._build_object_map()
            self.report({'INFO'}, f"Found {len(self.blender_object_map)} existing objects")
            
            # Step 4: Clear caches
            mod_apply_utils.clear_mod_apply_caches()
            
            # Step 5: Get layers with authored changes (only process these)
            authored_layers = self._get_layers_with_changes()
            self.report({'INFO'}, f"Processing {len(authored_layers)} layer(s) with changes")
            
            # Step 6: Process only prims that have authored changes
            changed_prims, changed_materials = self._get_changed_prims(authored_layers)
            self.report({'INFO'}, f"Found {len(changed_prims)} prim(s) with changes")
            
            # Step 7: Pre-load texture data for materials the scene actually
            # uses (library materials of other areas are not warmed).
            texture_paths = self._collect_texture_paths(self.scene_related_materials)
            if texture_paths:
                self.report({'INFO'}, f"Pre-loading {len(texture_paths)} unique textures...")
                self._preload_textures_parallel(texture_paths)
            
            self._disable_viewport_updates()
            
            try:
                for prim_path in sorted(changed_prims):
                    prim = self.stage.GetPrimAtPath(prim_path)
                    if prim and prim.IsValid():
                        self._process_prim(prim)
                    else:
                        # Prim doesn't exist in mod.usda (e.g., mesh added due to material change)
                        # Check if we have a Blender object for it and update materials only
                        bl_object = self.blender_object_map.get(prim_path)
                        if bl_object:
                            self.report({'INFO'}, f"Updating material for {bl_object.name} (prim not in mod.usda)")
                            # Pass the changed_materials from the earlier detection
                            self._update_material_only(prim_path, bl_object, changed_materials)
            finally:
                # Always re-enable viewport updates
                self._enable_viewport_updates()
            
            # Step 9: Report statistics
            self._report_statistics()
            
            # Step 10: Refresh viewport
            self._refresh_viewport()
            
            return True
            
        except Exception as e:
            self.report({'ERROR'}, f"Error loading mod file: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _build_object_map(self):
        """Build a map of existing Blender objects by their USD paths."""
        self.blender_object_map.clear()
        
        for obj in bpy.data.objects:
            # Check for usd_instance_path (instanced objects)
            if "usd_instance_path" in obj:
                path = obj["usd_instance_path"]
                self.blender_object_map[path] = obj
            
            # Check for usd_light_path (lights)
            elif "usd_light_path" in obj:
                path = obj["usd_light_path"]
                self.blender_object_map[path] = obj
            
            # Check for usd_prim_path (other direct prims like cameras)
            elif "usd_prim_path" in obj:
                path = obj["usd_prim_path"]
                self.blender_object_map[path] = obj
    
    def _collect_texture_paths(self, changed_materials: set) -> list:
        """Collect all texture file paths from changed materials."""
        texture_paths = set()
        
        for mat_path in changed_materials:
            mat_prim = self.stage.GetPrimAtPath(mat_path)
            if not mat_prim or not mat_prim.IsValid():
                continue
            
            # Find shader prim
            shader_prim = None
            if mat_prim.IsA(UsdShade.Material):
                surf_out = UsdShade.Material(mat_prim).GetSurfaceOutput()
                if surf_out and surf_out.HasConnectedSource():
                    shader_prim = surf_out.GetConnectedSource()[0].GetPrim()
            
            if not shader_prim:
                shader_child = mat_prim.GetChild("Shader")
                if shader_child and shader_child.IsValid():
                    shader_prim = shader_child
            
            if not shader_prim:
                continue
            
            # Collect texture attributes
            for attr in shader_prim.GetAuthoredAttributes():
                attr_name = attr.GetName()
                if 'texture' in attr_name.lower():
                    value = attr.Get()
                    if value and isinstance(value, (str, Sdf.AssetPath)):
                        path_str = str(value).strip('@')
                        if any(path_str.lower().endswith(ext) for ext in ['.dds', '.png', '.jpg', '.jpeg', '.tga']):
                            # Resolve relative paths
                            if not os.path.isabs(path_str):
                                # Find authoring layer
                                layer_path = self.stage.GetRootLayer().realPath
                                for layer in self.stage.GetLayerStack():
                                    if layer.GetPrimAtPath(shader_prim.GetPath()):
                                        prim_spec = layer.GetPrimAtPath(shader_prim.GetPath())
                                        if prim_spec and attr_name in [spec.name for spec in prim_spec.attributes]:
                                            layer_path = layer.realPath
                                            break
                                
                                layer_dir = os.path.dirname(layer_path)
                                resolved = os.path.normpath(os.path.join(layer_dir, path_str))
                                if os.path.exists(resolved):
                                    texture_paths.add(resolved)
                            elif os.path.exists(path_str):
                                texture_paths.add(path_str)
        
        return list(texture_paths)
    
    def _preload_textures_parallel(self, texture_paths: list):
        """Warm the file cache in parallel, then load images.

        Blender may only load images from its main thread (audit A03), so the
        parallel phase only reads file bytes to warm the OS cache;
        ``bpy.data.images.load`` runs sequentially on the calling thread.
        """
        from concurrent.futures import ThreadPoolExecutor
        import time
        from ...core_utils import _require_blender_main_thread

        _require_blender_main_thread()

        start_time = time.time()

        def read_single_texture(tex_path):
            """Read texture bytes to warm the OS file cache."""
            try:
                with open(tex_path, 'rb') as file:
                    while file.read(1024 * 1024):
                        pass
            except (OSError, ValueError):
                # Silently fail for problematic textures (e.g., BC7);
                # they fail again in bpy.data.images.load below.
                return

        # Pre-read textures in parallel (I/O bound operation)
        with ThreadPoolExecutor(max_workers=4) as executor:
            executor.map(read_single_texture, texture_paths)

        loaded_images = {}
        for tex_path in texture_paths:
            try:
                # Load image into Blender (Blender handles caching internally)
                # Use check_existing to avoid duplicates
                img = bpy.data.images.load(tex_path, check_existing=True)
            except Exception:
                # Silently fail for problematic textures (e.g., BC7)
                img = None
            if img:
                loaded_images[tex_path] = img

        elapsed = time.time() - start_time
        self.report({'INFO'}, f"Pre-loaded {len(loaded_images)}/{len(texture_paths)} textures in {elapsed:.2f}s")
    
    def _capture_layers(self) -> list:
        """Stack layers that only define captured game content.

        The Toolkit records ``lightspeed_layer_type`` in ``customLayerData``:
        captures are the pre-mod state, never the mod's changes.
        """
        excluded = (
            constants.LAYER_TYPE_CAPTURE,
            constants.LAYER_TYPE_CAPTURE_BAKER,
            'data',
        )
        return [
            layer for layer in self.stage.GetLayerStack(includeSessionLayers=False)
            if layer.customLayerData.get(constants.LAYER_TYPE_KEY) in excluded
        ]

    def _get_layers_with_changes(self) -> list:
        """Get all edit layers in the mod file that have authored changes.

        Captured content is excluded: scanning it re-applied every recorded
        asset and material, and unmatched capture prims spilled into scene
        creation (whole-capture reloads). Asset ``data`` layers define
        prototypes that replacements reference, not scene placements.
        """
        capture = set(map(id, self._capture_layers()))
        layers = []
        for layer in self.stage.GetLayerStack(includeSessionLayers=False):
            if not layer.rootPrims:
                continue
            if id(layer) in capture:
                continue
            layers.append(layer)
        return layers

    def _defined_prim_paths(self, layers) -> Set[str]:
        """Absolute prim paths that *layers* define (``def`` specs).

        Only definitions count as content a layer creates. ``over`` specs are
        overrides on content defined elsewhere (the capture, or an asset file
        pulled in by a reference) and are covered by the changed-prims scan.
        """
        defined_paths = set()

        def visit(prim_spec):
            if prim_spec.specifier == Sdf.SpecifierDef:
                defined_paths.add(str(prim_spec.path))
            for child_spec in prim_spec.nameChildren:
                visit(child_spec)

        for layer in layers:
            for root_spec in layer.pseudoRoot.nameChildren:
                visit(root_spec)
        return defined_paths
    
    def _get_changed_prims(self, layers: list) -> Tuple[Set[str], Set[str]]:
        """
        Get all prim paths that have authored changes in the given layers.
        
        This is more efficient than traversing everything - we only look at
        prims that actually have changes authored in the mod layers.
        
        Returns:
            Tuple of (changed_prims, changed_materials)
        """
        changed_prims = set()
        changed_materials = set()  # Track materials that have changes

        capture_layers = self._capture_layers()
        capture_defined = self._defined_prim_paths(capture_layers)
        related_hashes = set()
        for layer in capture_layers:
            related_hashes |= _hash_tokens(layer.identifier)
            for prim_path in self._defined_prim_paths([layer]):
                related_hashes |= _hash_tokens(prim_path)
        related_hashes |= self._scene_relation_hashes()
        self._routed_container_paths = set()
        self._replaced_prototype_hashes = set()
        self.scene_related_materials = set()
        self._material_scan_prim_paths = set()
        self._routed_light_instances = set()

        def traverse_layer_prims(layer: Sdf.Layer, current_path: str = '/'):
            """Recursively traverse prim specs in a layer."""
            prim_spec = layer.GetPrimAtPath(current_path)
            if not prim_spec:
                return
            
            # Check if this prim has any authored attributes/relationships.
            # Attribute edits on prims that no scene object or capture
            # def knows about are library edits (other areas of the mod) and
            # stay out of the change set; children keep being traversed
            # because parent paths never carry a child's hash.
            if prim_spec.attributes or prim_spec.relationships:
                # Look scopes hold the mod's own material records: their
                # relation runs through scene bindings, not mesh hashes, so
                # they bypass the hash gate below (the pairing scans after
                # traversal bind Look changes to scene objects).
                if '/Looks/' in current_path or _hash_tokens(current_path) & related_hashes:
                    changed_prims.add(current_path)

                    # Check if this is a material or shader change
                    # Note: In override-only files (using 'over'), type info may be missing
                    # so we use both type checking AND path heuristics
                    prim = self.stage.GetPrimAtPath(current_path)
                    if prim.IsValid():
                        prim_type = prim.GetTypeName()
                        is_material = prim.IsA(UsdShade.Material)
                        is_shader = prim.IsA(UsdShade.Shader)

                        # Heuristic: paths like /RootNode/Looks/mat_XXX are materials
                        # paths like /RootNode/Looks/mat_XXX/Shader are shaders
                        path_looks_like_material = '/Looks/' in current_path and current_path.count('/') == 3
                        path_looks_like_shader = '/Looks/' in current_path and current_path.endswith('/Shader')

                        self.report({'INFO'}, f"Changed prim: {current_path} (type: {prim_type}, is_material: {is_material or path_looks_like_material}, is_shader: {is_shader or path_looks_like_shader})")

                        if is_material or path_looks_like_material:
                            changed_materials.add(current_path)
                            self.report({'INFO'}, f"  → Added material: {current_path}")
                        elif is_shader or path_looks_like_shader:
                            # If a shader changed, mark its parent material
                            parent_prim = prim.GetParent()
                            parent_path = str(parent_prim.GetPath()) if parent_prim else None

                            # Also use path heuristic for parent
                            if parent_path and ('/Looks/' in parent_path):
                                changed_materials.add(parent_path)
                                self.report({'INFO'}, f"  → Added parent material: {parent_path}")
                            elif parent_prim and parent_prim.IsA(UsdShade.Material):
                                changed_materials.add(parent_path)
                                self.report({'INFO'}, f"  → Added parent material: {parent_path}")
                            else:
                                self.report({'INFO'}, f"  → Could not identify parent material for shader: {current_path}")
            
            # Recursively traverse children
            for child_spec in prim_spec.nameChildren:
                child_name = child_spec.name
                child_path = current_path.rstrip('/') + '/' + child_name
                traverse_layer_prims(layer, child_path)
        
        # Traverse each layer to find prims with authored changes
        for layer in layers:
            # Start from root prims
            if layer.rootPrims:
                for root_prim_name in layer.rootPrims.keys():
                    root_path = '/' + root_prim_name
                    traverse_layer_prims(layer, root_path)
        
        # Find all meshes that use the changed materials
        if changed_materials:
            self.report({'INFO'}, f"Found {len(changed_materials)} changed material(s): {', '.join(changed_materials)}")
            meshes_with_changed_materials = 0
            
            # First, check meshes in the stage
            for prim in self.stage.TraverseAll():
                if prim.IsA(UsdGeom.Imageable):
                    binding_api = UsdShade.MaterialBindingAPI(prim)
                    if binding_api.GetDirectBindingRel().GetTargets():
                        material_path = str(binding_api.GetDirectBindingRel().GetTargets()[0])
                        if material_path in changed_materials:
                            prim_path = str(prim.GetPath())
                            # Only stage prims that belong to the current scene
                            # or capture count: library prototypes for other
                            # areas must never enter scene creation, and prims
                            # defined by the capture itself are candidates for
                            # creation only when the scene already tracks them.
                            if not (_hash_tokens(prim_path) & related_hashes):
                                continue
                            if prim_path in capture_defined and prim_path not in self.blender_object_map:
                                continue
                            if prim_path not in changed_prims:
                                changed_prims.add(prim_path)
                                # Scan-sourced: material-only processing
                                # must not re-apply the composed transform.
                                self._material_scan_prim_paths.add(prim_path)
                            self.scene_related_materials.add(material_path)
                            meshes_with_changed_materials += 1
            
            # Also check existing Blender objects - match by material name
            # Since mod.usda doesn't contain mesh geometry, we can't query material bindings from it
            # Instead, we check if the Blender object's material matches any changed material
            objects_checked = set()
            
            # First, check objects in the map (those with USD properties)
            for prim_path, bl_obj in self.blender_object_map.items():
                objects_checked.add(bl_obj)
                if hasattr(bl_obj, 'data') and hasattr(bl_obj.data, 'materials'):
                    for mat_slot in (slot.material for slot in bl_obj.material_slots):
                        if mat_slot:
                            # Check if this material matches any changed material
                            # Material names in Blender might have suffixes, so check if changed material path contains the base name
                            for changed_mat_path in changed_materials:
                                mat_base_name = changed_mat_path.split('/')[-1]  # Get last part: "mat_A1EB3F7335EE09C7"
                                # Check if Blender material name contains the USD material name
                                if mat_base_name in mat_slot.name or changed_mat_path in mat_slot.get('usd_material_path', ''):
                                    self.report({'INFO'}, f"Object {bl_obj.name} uses changed material {changed_mat_path} (Blender mat: {mat_slot.name})")
                                    self.scene_related_materials.add(changed_mat_path)
                                    if prim_path not in changed_prims:
                                        changed_prims.add(prim_path)
                                        # Scan-sourced: material-only
                                        # processing must not re-apply the
                                        # composed transform.
                                        self._material_scan_prim_paths.add(prim_path)
                                        meshes_with_changed_materials += 1
                                    break
            
            # Then, check ALL scene objects as fallback (for objects that might not have USD properties)
            for bl_obj in bpy.data.objects:
                if bl_obj in objects_checked:
                    continue  # Already checked
                
                if hasattr(bl_obj, 'data') and hasattr(bl_obj.data, 'materials'):
                    for mat_slot in (slot.material for slot in bl_obj.material_slots):
                        if mat_slot:
                            for changed_mat_path in changed_materials:
                                mat_base_name = changed_mat_path.split('/')[-1]
                                if mat_base_name in mat_slot.name or changed_mat_path in mat_slot.get('usd_material_path', ''):
                                    self.report({'INFO'}, f"Object {bl_obj.name} uses changed material {changed_mat_path} (Blender mat: {mat_slot.name}) [no USD path]")
                                    self.scene_related_materials.add(changed_mat_path)
                                    # Create a synthetic prim path for this object
                                    synthetic_path = f"/Unmapped/{bl_obj.name}"
                                    if synthetic_path not in changed_prims:
                                        changed_prims.add(synthetic_path)
                                        # Add to blender_object_map so it can be processed
                                        self.blender_object_map[synthetic_path] = bl_obj
                                        meshes_with_changed_materials += 1
                                    break
            
            self.report({'INFO'}, f"Found {meshes_with_changed_materials} mesh(es) using changed materials")
        
        # Content the mod ADDS, limited to the current scene and capture:
        # prim paths defined or reference-reset in the edit layers whose own
        # path (or a parent path for nested lights/overrides) carries a hash
        # the scene objects or capture layers know about. Library prototypes
        # for other game areas stay out of the set entirely, replacing the
        # previous behavior that appended every defined stage prim.
        layers_defined, reference_authored = self._layer_spec_paths(layers)
        # Prototype containers whose content the mod defines itself (def specs
        # or authored ``references`` opinions) vs over-only attribute edits on
        # capture-owned records: only the former may swap scene geometry.
        self._mod_authored_prototype_paths = layers_defined | reference_authored
        # Official Remix replacements are often ``over mesh_<HASH>`` with a
        # newly defined ``ref_<UUID>`` child. The container has no own def or
        # references opinion, yet its referenced mesh AND lights must be routed.
        # Do not promote arbitrary over-only material/visibility children.
        for authored_path in layers_defined | reference_authored:
            container = _ancestor_prototype_path(authored_path)
            if container and authored_path != container:
                remainder = authored_path[len(container):].lstrip('/')
                if remainder.split('/', 1)[0].startswith('ref_'):
                    self._mod_authored_prototype_paths.add(container)
        for prim_path in layers_defined | reference_authored:
            if prim_path in capture_defined and prim_path not in reference_authored:
                continue  # pure def on captured content: handled by the scans above
            if prim_path in self.blender_object_map:
                continue
            if not (_hash_tokens(prim_path) & related_hashes):
                continue  # unrelated to this scene and capture
            changed_prims.add(prim_path)

        return changed_prims, changed_materials
    
    def _process_prim(self, prim: Usd.Prim):
        """Process a single prim - update existing object or create new one."""
        prim_path = str(prim.GetPath())
        self.stats['prims_processed'] += 1

        # Toolkit replacement records live on ``mesh_<HASH>`` container paths
        # and the object map shares scene objects with them through the
        # ``usd_prim_path`` tag (the capture's prototype path), so a plain map
        # lookup would run the update path with the container's own L2W and
        # zero the placement transforms. Route the record instead.
        leaf = prim_path.rsplit('/', 1)[-1]
        if self._is_usd_light_prim(prim):
            prototype = self._light_prototype_container(prim)
            if prototype is not None:
                self._sync_prototype_light(prim, prototype)
                return
        if _prototype_container_path(prim_path):
            if prim_path in self._mod_authored_prototype_paths:
                self._route_mesh_prototype_addition(prim)
            else:
                # ``over`` edits (prototype flags, rebinding deep inside the
                # prototype reference) never own scene transforms, but their
                # material edits still reach the objects of the same hash.
                self._apply_material_to_hash_objects(prim)
            return

        # Check if we have an existing Blender object for this prim
        bl_object = self.blender_object_map.get(prim_path)

        if bl_object and prim_path in self._material_scan_prim_paths:
            # The material-binding scan pulled this path in solely because a
            # bound Look changed - no transform is authored here (the path may
            # not even belong to a prim of the mod stage). Applying transforms
            # for it once collapsed bound placements to (0, 0, 0).
            self._update_material_only(prim_path, bl_object, self.scene_related_materials)
        elif bl_object:
            # Update existing object
            self._update_existing_object(prim, bl_object)
        else:
            # Create new object if this is a valid type
            self._create_new_object(prim)
    
    def _update_existing_object(self, prim: Usd.Prim, bl_object: bpy.types.Object):
        """Update an existing Blender object with changes from USD prim."""
        self.stats['objects_updated'] += 1
        prim_path = str(prim.GetPath())
        
        # Update transform
        if prim.IsA(UsdGeom.Xformable):
            self._apply_transform(prim, bl_object)
        
        # Update visibility
        if prim.IsA(UsdGeom.Imageable):
            self._apply_visibility(prim, bl_object)
        
        # Update material binding
        if prim.IsA(UsdGeom.Boundable) and bl_object.type == 'MESH':
            self._apply_material(prim, bl_object)
        
        # Update light properties
        if bl_object.type == 'LIGHT' and self._is_usd_light_prim(prim):
            self._apply_light_properties(prim, bl_object)
    
    def _update_material_only(self, prim_path: str, bl_object: bpy.types.Object, changed_materials: Set[str]):
        """
        Update material for an object whose USD prim doesn't exist in mod.usda.
        This happens when a material changes but the geometry is in another file.
        """
        if bl_object.type != 'MESH' or not hasattr(bl_object.data, 'materials'):
            return
        
        self.stats['objects_updated'] += 1
        
        # Get the current material from the Blender object
        current_mat = bl_object.material_slots[0].material if bl_object.material_slots else None
        if not current_mat:
            return
        
        # Find which changed material matches the Blender object's current material
        # The material name in Blender contains the USD material name
        # e.g., "mat_14B193F6317D7B9E_88264f06_cbc2" -> "mat_14B193F6317D7B9E"
        material_path = None
        for changed_mat_path in changed_materials:
            mat_base_name = changed_mat_path.split('/')[-1]  # Get "mat_14B193F6317D7B9E"
            if mat_base_name in current_mat.name:
                material_path = changed_mat_path
                self.report({'INFO'}, f"  Matched material: {material_path} (Blender mat: {current_mat.name})")
                break
        
        if not material_path:
            self.report({'INFO'}, f"  Could not match material for {bl_object.name} (has: {current_mat.name})")
            return
        
        # Create/update the material from mod.usda
        mod_file_absolute = self.stage.GetRootLayer().realPath
        mod_directory = os.path.dirname(mod_file_absolute)
        bl_material = mod_apply_utils.get_or_create_mod_instance_material_util(
            base_material_usd_path=material_path,
            instance_prim_for_metadata=None,
            current_mod_stage=self.stage,
            texture_res_context_path_p=mod_directory,
            mod_file_path_for_tex_p=mod_file_absolute,
            mod_base_material_node_cache_param=self.base_material_node_cache,
            local_material_cache_param=self.material_cache,
            report_fn=self.report,
            existing_bl_material_param=current_mat
        )
        
        if bl_material:
            # Replace the material
            self._assign_object_material(bl_object, bl_material)
            
            self.stats['materials_applied'] += 1
            self.report({'INFO'}, f"  Applied updated material {material_path} to {bl_object.name}")
    
    # --- Applied-mod routing helpers ---

    def _layer_spec_paths(self, layers: list) -> Tuple[Set[str], Set[str]]:
        """Paths the edit layers define (``def`` specs) or author references on.

        Reference authoring includes ``references = None`` resets: those are
        still opinions the mod owns, and a reset with no new content means
        "this prototype is gone" rather than "nothing was edited".
        """
        defined_paths: Set[str] = set()
        reference_paths: Set[str] = set()

        def visit(prim_spec):
            if prim_spec.specifier == Sdf.SpecifierDef:
                defined_paths.add(str(prim_spec.path))
            if self._prim_spec_authors_references(prim_spec):
                reference_paths.add(str(prim_spec.path))
            for child_spec in prim_spec.nameChildren:
                visit(child_spec)

        for layer in layers:
            for root_spec in layer.pseudoRoot.nameChildren:
                visit(root_spec)
        return defined_paths, reference_paths

    @staticmethod
    def _prim_spec_authors_references(prim_spec) -> bool:
        """Whether a prim spec carries its own ``references`` field opinion.

        Covers the ``references = None`` reset (an explicit empty list op) and
        asset links; the Sdf prim-spec Python bindings differ between USD
        builds, so detection falls back across ``hasReferences``, ``HasField``
        and the layer's raw field.
        """
        try:
            return bool(prim_spec.hasReferences)
        except AttributeError:
            pass
        try:
            if prim_spec.HasField("references"):
                return True
        except AttributeError:
            pass
        try:
            field = prim_spec.GetField("references")
        except Exception:
            return False
        return field is not None

    def _scene_relation_hashes(self) -> Set[str]:
        """Capture-hash tokens the current scene's objects belong to.

        Reads the provenance tags written by the capture import (instance,
        prototype, light and mesh-hash tags, on objects and their data), so
        "related to this scene" follows what is actually present, not
        whatever the mod file also happens to contain.
        """
        props = (
            constants.PROP_USD_INSTANCE_PATH,
            constants.PROP_USD_PRIM_PATH,
            constants.PROP_USD_PROTOTYPE_PATH,
            'usd_light_path',
            constants.PROP_REMIX_MESH_HASH,
        )
        hashes: Set[str] = set()
        for obj in bpy.data.objects:
            hashes |= _sky_object_hashes(obj)
            for holder in filter(None, (obj, getattr(obj, 'data', None))):
                for prop in props:
                    tokens = _hash_tokens(str(holder.get(prop, '')))
                    if tokens:
                        hashes |= tokens
        return hashes

    def _scene_objects_for_hashes(self, hash_tokens: Set[str]) -> List[bpy.types.Object]:
        """Scene objects whose provenance tags carry any of *hash_tokens*.

        Mirrors ``_scene_relation_hashes`` so routing and relation checks stay
        consistent.
        """
        if not hash_tokens:
            return []
        props = (
            constants.PROP_USD_INSTANCE_PATH,
            constants.PROP_USD_PRIM_PATH,
            constants.PROP_USD_PROTOTYPE_PATH,
            'usd_light_path',
            constants.PROP_REMIX_MESH_HASH,
        )
        targets: List[bpy.types.Object] = []
        seen = set()
        for obj in bpy.data.objects:
            if obj.name in seen:
                continue
            if _sky_object_hashes(obj) & hash_tokens:
                targets.append(obj)
                seen.add(obj.name)
                continue
            for holder in filter(None, (obj, getattr(obj, 'data', None))):
                for prop in props:
                    if _hash_tokens(str(holder.get(prop, ''))) & hash_tokens:
                        targets.append(obj)
                        seen.add(obj.name)
                        break
                if obj.name in seen:
                    break
        return targets

    def _mod_collection_label(self) -> str:
        """Label for the created-content collection: ``mod <name>``."""
        name = None
        for layer in self.stage.GetLayerStack(False):
            custom = layer.customLayerData or {}
            name = custom.get('lightspeed_mod_name')
            if name:
                break
        if not name:
            identifier = self.stage.GetRootLayer().realPath or self.stage.GetRootLayer().identifier
            stem = os.path.basename(identifier)
            if not stem or ':' in stem:
                # Anonymous layer identifiers have no path separator
                # ('anon:0x...:mod_modtest.usda'); strip to the file stem.
                stem = stem.rsplit(':', 1)[-1]
            name = os.path.splitext(stem)[0] or 'mod'
        return 'mod ' + bpy.path.clean_name(name or 'mod')

    def _ensure_mod_collection(self) -> Optional[bpy.types.Collection]:
        """Reuse (or create) the collection that holds operator-created content.

        Created placements and lights must not mix into capture-import
        collections, so they are linked into a dedicated ``mod <name>``
        collection as a direct child of the scene collection.
        """
        if self.mod_collection:
            return self.mod_collection
        label = self._mod_collection_label()
        collection = bpy.data.collections.get(label)
        if collection is None:
            collection = bpy.data.collections.new(label)
        scene_collection = getattr(self.context.scene, 'collection', None)
        if scene_collection is not None and collection.name not in scene_collection.children:
            try:
                scene_collection.children.link(collection)
            except RuntimeError:
                pass  # already linked somewhere in the scene tree
        self.mod_collection = collection
        return collection

    def _target_collection(self) -> bpy.types.Collection:
        """Collection for created objects: the mod collection, with fallback."""
        collection = self._ensure_mod_collection()
        if collection is None:
            collection = self.context.collection
        return collection

    # -- Prototype light placement (capture instances, not prototype origin) --

    @staticmethod
    def _light_prototype_container(prim: Usd.Prim):
        """Nearest captured mesh/sky prototype containing a mod light."""
        parent = prim.GetParent()
        while parent and str(parent.GetPath()) != '/':
            path = str(parent.GetPath())
            if _prototype_container_path(path):
                return parent
            parent = parent.GetParent()
        return None

    def _get_light_stage(self):
        """Read-only composed stage for lights; never write to the capture/mod.

        Apply Mod historically opens mod.usda by itself. If the active capture
        was not listed as its sublayer, mapped inst_<HASH> light paths do not
        exist. Build an anonymous [mod, capture] composition solely for light
        placement, while keeping self.stage and the edit-layer scan unchanged.
        """
        if self._light_stage is not None:
            return self._light_stage
        self._light_stage = self.stage
        self._light_xform_cache = self.xform_cache or UsdGeom.XformCache(self.time_code)
        scene = self.context.scene
        capture_path = bpy.path.abspath(scene.get(constants.PROP_SOURCE_CAPTURE, ''))
        mod_path = getattr(self, 'mod_file_path', '') or (self.stage.GetRootLayer().realPath or '')
        if not capture_path or not os.path.isfile(capture_path) or not os.path.isfile(mod_path):
            return self._light_stage
        if os.path.normcase(os.path.abspath(capture_path)) == os.path.normcase(os.path.abspath(mod_path)):
            return self._light_stage
        if self._capture_layers():
            return self._light_stage  # Already a complete [mod, capture] project.
        try:
            root = Sdf.Layer.CreateAnonymous('remix_blender_light_composition.usda')
            root.subLayerPaths = [
                os.path.abspath(mod_path).replace('\\', '/'),
                os.path.abspath(capture_path).replace('\\', '/'),
            ]
            composed = Usd.Stage.Open(root.identifier, Usd.Stage.LoadAll)
            if composed is not None:
                self._light_stage_root = root
                self._light_stage = composed
                self._light_xform_cache = UsdGeom.XformCache(self.time_code)
        except Exception as exc:
            self.report({'WARNING'}, f'Cannot compose capture for light placements: {exc}')
        return self._light_stage

    def _get_capture_light_stage(self):
        """Optional original-capture stage for nested mesh anchor correction."""
        if self._capture_light_stage is not None:
            return self._capture_light_stage
        path = bpy.path.abspath(self.context.scene.get(constants.PROP_SOURCE_CAPTURE, ''))
        if not path or not os.path.isfile(path):
            return None
        try:
            self._capture_light_stage = Usd.Stage.Open(path, Usd.Stage.LoadAll)
        except Exception:
            return None
        return self._capture_light_stage

    @staticmethod
    def _mapped_anchor_path(anchor, prototype_path: str, instance_path: str) -> str:
        """Locate the USD mesh prim whose world matrix the Blender anchor uses."""
        mesh_path = str(anchor.get(constants.PROP_USD_PRIM_PATH, ''))
        if mesh_path.startswith(prototype_path + '/'):
            return instance_path + mesh_path[len(prototype_path):]
        if mesh_path.startswith(instance_path + '/'):
            return mesh_path
        return instance_path

    def _instance_anchor_objects(self, prototype_path: str) -> dict:
        """One scene MESH object per captured instance of this prototype."""
        mesh_hash = next(iter(_hash_tokens(prototype_path.rsplit('/', 1)[-1])), None)
        if not mesh_hash:
            return {}
        anchors = {}
        for obj in self.context.scene.objects:
            if obj.type != 'MESH':
                continue
            instance_path = str(obj.get(constants.PROP_USD_INSTANCE_PATH, ''))
            leaf = instance_path.rsplit('/', 1)[-1]
            if not instance_path.startswith(constants.ROOTNODE_INSTANCES + '/'):
                continue
            if not re.fullmatch(constants.REGEX_INSTANCE_PATH, leaf):
                continue
            if mesh_hash not in _hash_tokens(leaf):
                continue
            provenance = str(obj.get(constants.PROP_USD_PROTOTYPE_PATH, '') or
                             obj.get(constants.PROP_USD_PRIM_PATH, ''))
            if provenance and _hash_tokens(provenance) and mesh_hash not in _hash_tokens(provenance):
                continue
            # Multi-part meshes share one placement; light is not duplicated
            # for each part. Prefer a direct instance-root anchor if present.
            previous = anchors.get(instance_path)
            if previous is None or self._mapped_anchor_path(
                obj, prototype_path, instance_path
            ) == instance_path:
                anchors[instance_path] = obj
        return anchors

    def _unanchored_distant_light_instances(self, light_prim, prototype_prim) -> dict:
        """Composed USD placements for a sky/directional light without Blender MESH anchors.

        Sky meshes are sometimes absent from the captured Blender viewport. A
        DistantLight nevertheless needs to appear as a SUN. Prefer instance
        paths in the composed USD stage so any instance-local rotation or
        light-property opinion is respected. When the capture defines no
        corresponding instance, display the authored prototype light once.
        Never create geometry or an artificial mesh anchor just for the sun.
        """
        prototype_path = str(prototype_prim.GetPath())
        source_path = str(light_prim.GetPath())
        stage = self._get_light_stage()
        instances = {}
        root = stage.GetPrimAtPath(constants.ROOTNODE_INSTANCES) if stage else None
        hash_values = _hash_tokens(prototype_path.rsplit('/', 1)[-1])
        if root and root.IsValid() and hash_values:
            for candidate in root.GetChildren():
                instance_path = str(candidate.GetPath())
                leaf = instance_path.rsplit('/', 1)[-1]
                if not re.fullmatch(constants.REGEX_INSTANCE_PATH, leaf):
                    continue
                if not (_hash_tokens(leaf) & hash_values):
                    continue
                mapped = stage.GetPrimAtPath(instance_path + source_path[len(prototype_path):])
                if mapped and mapped.IsValid() and mapped.GetTypeName() == 'DistantLight':
                    instances[instance_path] = None  # no Blender mesh parent
        return instances if instances else {'': None}

    def _refresh_view_layer(self) -> None:
        """Evaluate the depsgraph so freshly moved objects report fresh matrices.

        The mod pass often moves `usd_instance_path` anchors right before
        `_sync_prototype_light` reads them; Blender recomputes ``matrix_world``
        only during a view-layer update.
        """
        view_layer = getattr(self.context, 'view_layer', None) or bpy.context.view_layer
        if view_layer is not None:
            # Blender can leave freshly hidden or re-linked anchors at stale
            # matrices after a single update; the second update converges the
            # evaluation.
            view_layer.update()
            view_layer.update()

    def _composed_light_world_matrix(self, prim, y_up=None):
        """Blender world matrix for a light from its composed USD prim.

        Both standalone suns and anchored per-instance lights share this:
        position and orientation come from the composed prim with the stage's
        Y-up conversion, then the scene scale applies to location. Anchors
        only provide the Blender parenting, never the unit conversion. Anchored
        callers pass ``y_up`` because their anchors' Blender frames follow the
        loader's own up-axis flag, not the light stage's metadata.
        """
        stage = prim.GetStage()
        cache = self._light_xform_cache if stage == self._get_light_stage() else self.xform_cache
        if cache is None:
            cache = UsdGeom.XformCache(self.time_code)
        if y_up is None:
            y_up = UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y
        matrix = mod_apply_utils.get_blender_transform_matrix_from_mod(
            prim, cache, y_up, self.report
        )
        scene = self.context.scene
        scale = scene.get('remix_import_unit_scale')
        if scale is None:
            scale = getattr(scene, 'remix_export_scale', 1.0)
        matrix.translation *= float(scale)
        return matrix

    @staticmethod
    def _light_data_identity(data):
        return data.as_pointer() if hasattr(data, 'as_pointer') else id(data)

    @staticmethod
    def _desired_blender_light_type(prim):
        usd_type = str(prim.GetTypeName())
        if usd_type == 'SphereLight':
            is_spot, _, _ = sphere_cone_parameters(prim)
            return 'SPOT' if is_spot else 'POINT'
        if usd_type in {'RectLight', 'DiskLight', 'CylinderLight', 'PortalLight'}:
            return 'AREA'
        if usd_type in {'DistantLight', 'DomeLight'}:
            return 'SUN'
        if usd_type == 'SpotLight':
            return 'SPOT'
        return 'POINT'

    def _sync_prototype_light(self, light_prim, prototype_prim) -> None:
        """Each scene instance gets a separate Object but shared Light datablock.

        The group is (source prototype light, *composed light parameters*), not
        the USD instance path. An instance-authored color, intensity, radius or
        cone override splits its group, leaving its datablock independent.
        Existing objects from an older import are relinked on the next Apply.
        """
        source_path = str(light_prim.GetPath())
        prototype_path = str(prototype_prim.GetPath())
        # Anchors were moved earlier in this apply pass, but Blender refreshes
        # ``matrix_world`` only on a depsgraph update; placing against a stale
        # parent matrix drops prototype lights at their anchors' origins.
        self._refresh_view_layer()
        anchors = self._instance_anchor_objects(prototype_path)
        if not anchors and light_prim.GetTypeName() == 'DistantLight':
            # Sky meshes may have no Blender MESH object and sometimes no
            # inst_<HASH> in the capture. A directional light is scene-wide:
            # materialize it directly instead of silently dropping the sun.
            anchors = self._unanchored_distant_light_instances(light_prim, prototype_prim)
        if not anchors:
            self.report({'WARNING'}, f'No capture instance for prototype light {source_path}; skipping')
            return
        existing = {
            (obj.get('usd_light_path'), obj.get('usd_light_instance_path')): obj
            for obj in self.context.scene.objects
            if obj.type == 'LIGHT' and obj.get('usd_light_instance_path')
        }
        # Standalone sky suns have no capture instance path. Reuse a previous
        # result from this addon rather than accumulating a new SUN on Apply.
        for obj in self.context.scene.objects:
            if (obj.type == 'LIGHT' and obj.get('usd_light_unanchored')
                    and obj.get('usd_light_path') == source_path
                    and not obj.get('usd_light_instance_path')):
                existing[(source_path, '')] = obj
        canonical_by_signature = {}
        claimed_data = set()
        placed_objects = []
        created_any = False
        for instance_path, anchor in sorted(anchors.items()):
            key = (source_path, instance_path)
            if key in self._routed_light_instances:
                continue
            bl_light = existing.get(key)
            newly_created = False
            previous_data = None
            provisional = None
            try:
                mapped_path = (instance_path + source_path[len(prototype_path):]
                               if instance_path else source_path)
                mapped_light = self._get_light_stage().GetPrimAtPath(mapped_path)
                applied_light = (mapped_light if mapped_light and mapped_light.IsValid()
                                 else light_prim)
                world_matrix = self._composed_light_world_matrix(
                    applied_light, None if anchor is None else self.up_axis_is_y)
                signature = light_parameter_signature(applied_light, self.time_code)
                group = (source_path, signature)
                canonical = canonical_by_signature.get(group)
                if canonical is None:
                    desired_type = self._desired_blender_light_type(applied_light)
                    if (bl_light is not None and bl_light.data.type == desired_type
                            and self._light_data_identity(bl_light.data) not in claimed_data):
                        canonical = bl_light.data
                        self._apply_light_properties(applied_light, bl_light)
                    else:
                        provisional = self._create_light_object(applied_light)
                        if provisional is None:
                            continue
                        canonical = provisional.data
                    claimed_data.add(self._light_data_identity(canonical))
                if bl_light is None:
                    if provisional is not None:
                        bl_light = provisional
                        provisional = None
                    else:
                        # Share the Light ID; do not copy it or create a new ID.
                        name = bpy.path.clean_name(applied_light.GetName()) + '_mod_light'
                        bl_light = bpy.data.objects.new(name=name, object_data=canonical)
                    newly_created = True
                    self._target_collection().objects.link(bl_light)
                    self.stats['objects_created'] += 1
                    created_any = True
                elif bl_light.data != canonical:
                    previous_data = bl_light.data
                    bl_light.data = canonical

                bl_light['usd_light_path'] = source_path
                bl_light['usd_light_instance_path'] = instance_path
                bl_light[constants.PROP_USD_PROTOTYPE_PATH] = prototype_path
                bl_light['usd_light_source_path'] = source_path
                if anchor is None:
                    bl_light['usd_light_unanchored'] = True
                    bl_light.parent = None
                    bl_light.matrix_parent_inverse = mathutils.Matrix.Identity(4)
                    bl_light.matrix_world = world_matrix
                else:
                    if 'usd_light_unanchored' in bl_light:
                        del bl_light['usd_light_unanchored']
                    bl_light.parent = anchor
                    bl_light.matrix_parent_inverse = mathutils.Matrix.Identity(4)
                    bl_light.matrix_world = world_matrix
                self._apply_visibility(applied_light, bl_light)
                canonical_by_signature[group] = canonical
                self._routed_light_instances.add(key)
                if previous_data is not None and previous_data.users == 0:
                    bpy.data.lights.remove(previous_data)
                if provisional is not None:
                    # A type change on an existing object built a temporary
                    # object to obtain correctly typed Light data.
                    bpy.data.objects.remove(provisional, do_unlink=True)
                    provisional = None
                placed_objects.append((bl_light, world_matrix))
            except Exception as exc:
                if provisional is not None:
                    provisional_data = provisional.data
                    bpy.data.objects.remove(provisional, do_unlink=True)
                    if provisional_data.users == 0:
                        bpy.data.lights.remove(provisional_data)
                if newly_created and bl_light is not None:
                    data = bl_light.data
                    bpy.data.objects.remove(bl_light, do_unlink=True)
                    if data and data.users == 0:
                        bpy.data.lights.remove(data)
                    self.stats['objects_created'] -= 1
                elif previous_data is not None and bl_light is not None:
                    bl_light.data = previous_data
                self.report({'WARNING'}, f'Failed to place light {source_path} on {instance_path}: {exc}')

        if placed_objects:
            # ``bl_light.matrix_world`` assignments save a basis derived from
            # the parent's current datablock matrix, and a parent moved or
            # hidden earlier in this pass (or freshly re-parented) can still
            # evaluate at identity. The parenting above also re-tags the
            # family, so one more update now yields true worlds; stamping the
            # same requested world again recomputes the bases against real
            # anchors instead of leaving lights at (10, 6, 6) style drift.
            self._refresh_view_layer()
            for placed_light, placed_world in placed_objects:
                placed_light.matrix_world = placed_world

        if created_any:
            # Remove only obsolete, mod-created single lights from the old
            # implementation; never touch capture-owned or unrelated lights.
            mod_label = self._mod_collection_label()
            for obj in tuple(self.context.scene.objects):
                if (obj.type == 'LIGHT' and obj.get('usd_light_path') == source_path
                        and not obj.get('usd_light_instance_path')
                        and obj.get(constants.PROP_USD_INSTANCE_PATH) == source_path
                        and any(col.name == mod_label for col in obj.users_collection)):
                    data = obj.data
                    bpy.data.objects.remove(obj, do_unlink=True)
                    if data and data.users == 0:
                        bpy.data.lights.remove(data)

    def _composed_matrix_is_identity(self, prim: Usd.Prim) -> bool:
        """Whether the prim's composed local-to-world matrix is the identity."""
        try:
            matrix = self.xform_cache.GetLocalToWorldTransform(prim)
        except Exception:
            return False
        for row in range(4):
            for col in range(4):
                expected = 1.0 if row == col else 0.0
                if abs(matrix[row][col] - expected) > 1e-6:
                    return False
        return True

    def _create_new_object(self, prim: Usd.Prim) -> None:
        """Route prims the mod adds following the official Toolkit mod model.

        Replacements hold their content on ``mesh_<HASH>`` prototype records
        (a ``references = None`` reset plus ``ref_*`` asset children), while
        placements stay on ``inst_<HASH>[_<N>]`` prims owned by the capture.
        Changed prims route accordingly: a ``mesh_<HASH>`` record swaps its
        content into the scene objects of that hash, an ``inst_`` placement is
        materialized as new objects (except a canonical redefinition of the
        prototype anchor, which also replaces in place), typed lights are
        always their own scene-wide object even under prototype containers,
        and deeper children are covered by their nearest ``inst_``/``mesh_``
        container once.
        """
        prim_path = str(prim.GetPath())

        if self._is_usd_light_prim(prim):
            # Prototype-owned lights are handled before this branch by
            # _process_prim; direct /RootNode/lights sources remain scene lights.
            if prim_path in self._routed_container_paths:
                return
            self._routed_container_paths.add(prim_path)
            new_obj = self._create_light_object(prim)
            if new_obj:
                self._register_created_object(new_obj, prim, prim_path)
            return

        leaf = prim_path.rsplit('/', 1)[-1]
        if re.fullmatch(constants.REGEX_INSTANCE_PATH, leaf):
            self._route_instance_addition(prim)
        elif _prototype_container_path(prim_path):
            self._route_mesh_prototype_addition(prim)
        elif prim.IsA(UsdGeom.Mesh):
            # Meshes under prototype containers are already covered by the
            # container's replacement; attribute edits there must not spawn
            # standalone scene objects.
            if self._hop_to_prototype_container(prim):
                return
            new_obj = self._create_mesh_object(prim)
            if new_obj:
                self._register_created_object(new_obj, prim, prim_path)
                self._bind_first_material(new_obj, (prim,))
        else:
            self._hop_to_prototype_container(prim)

    def _register_created_object(self, new_obj: bpy.types.Object, prim: Usd.Prim, prim_path: str) -> None:
        """Finalize one intrinsically created object (collection, transform, tags)."""
        self.stats['objects_created'] += 1
        try:
            self._target_collection().objects.link(new_obj)
        except Exception as e:
            self.report({'WARNING'}, f"Failed to link object {new_obj.name}: {e}")
        if prim.IsA(UsdGeom.Xformable):
            self._apply_transform(prim, new_obj)
        self._apply_visibility(prim, new_obj)
        new_obj[constants.PROP_USD_INSTANCE_PATH] = prim_path
        print(f"Created new {new_obj.type} object '{new_obj.name}' from <{prim_path}>")

    def _bind_first_material(self, new_obj: bpy.types.Object, candidates) -> None:
        """Bind the first relative material among candidate prims, if any."""
        for candidate in candidates:
            if candidate is None:
                continue
            binding = UsdShade.MaterialBindingAPI(candidate).GetDirectBindingRel()
            if binding and binding.GetTargets():
                self._apply_material(candidate, new_obj)
                return

    def _apply_material_to_hash_objects(self, prim: Usd.Prim) -> None:
        """Apply an over record's material binding to objects of the same hash.

        ``over`` records on ``mesh_<HASH>`` containers never own scene
        transforms, but a rebinding authored inside the prototype (the
        Toolkit texture-swap layout) must still reach the scene objects
        that carry the container's hash. Flag-only records carry no
        binding rel and stop right here.
        """
        binding = UsdShade.MaterialBindingAPI(prim).GetDirectBindingRel()
        if not binding or not binding.GetTargets():
            return

        hash_tokens = _hash_tokens(str(prim.GetPath()).rsplit('/', 1)[-1])
        scene_objects = self._scene_objects_for_hashes(hash_tokens)
        if not scene_objects:
            return

        # Prototype rescoping reskins live capture objects, so the capture
        # material is passed as the existing base: unspecified inputs are
        # preserved instead of lost during the rebuild.
        first_material = None
        for bl_object in scene_objects:
            if bl_object.material_slots and bl_object.material_slots[0].material:
                first_material = bl_object.material_slots[0].material
                break

        try:
            bl_material = mod_apply_utils.get_or_create_mod_instance_material_util(
                base_material_usd_path=str(binding.GetTargets()[0]),
                instance_prim_for_metadata=prim,
                current_mod_stage=self.stage,
                texture_res_context_path_p=os.path.dirname(self.stage.GetRootLayer().realPath),
                mod_file_path_for_tex_p=self.stage.GetRootLayer().realPath,
                mod_base_material_node_cache_param=self.base_material_node_cache,
                local_material_cache_param=self.material_cache,
                report_fn=self.report,
                existing_bl_material_param=first_material,
            )
            if not bl_material:
                return
            for bl_object in scene_objects:
                if bl_object.type != 'MESH':
                    continue
                self._assign_object_material(bl_object, bl_material)
                self.stats['materials_applied'] += 1
        except Exception as e:
            self.report({'WARNING'}, f"Failed to apply materials for {prim.GetPath()}: {e}")

    def _route_instance_addition(self, prim: Usd.Prim) -> None:
        """Route an added ``inst_<HASH>[_<N>]`` placement or canonical anchor."""
        instance_path = str(prim.GetPath())
        if instance_path in self._routed_container_paths:
            return
        self._routed_container_paths.add(instance_path)
        leaf = instance_path.rsplit('/', 1)[-1]
        hash_tokens = _hash_tokens(leaf)
        scene_objects = self._scene_objects_for_hashes(hash_tokens)
        # inst_..._0 with an identity matrix is the prototype's canonical
        # anchor: the mod re-defining it is a replacement, not a new placement.
        suffix = re.search(r'_(\d+)$', leaf)
        canonical_anchor = (
            bool(scene_objects)
            and suffix is not None
            and suffix.group(1) == '0'
            and self._composed_matrix_is_identity(prim)
        )
        if canonical_anchor:
            self._replace_prototype_hash(hash_tokens, prim)
            return
        self._materialize_new_placement(prim, instance_path, leaf)

    def _route_mesh_prototype_addition(self, prim: Usd.Prim) -> None:
        """Route a ``mesh_<HASH>`` prototype record the mod owns."""
        container_path = str(prim.GetPath())
        if container_path in self._routed_container_paths:
            return
        self._routed_container_paths.add(container_path)
        if container_path not in self._mod_authored_prototype_paths:
            # Reached from a child hop: ``over``-only edits keep the
            # capture-owned prototype intact; nothing here owns scene
            # geometry, so the hop merely covers the child.
            return
        hash_tokens = _hash_tokens(container_path.rsplit('/', 1)[-1])
        if not self._scene_objects_for_hashes(hash_tokens):
            # A sky prototype can be relevant to the capture without having
            # a visible Blender mesh or inst_<HASH> anchor. Only directional
            # lights are safe to display globally; never materialize unknown
            # library geometry or unrelated local lights here.
            capture = self._get_capture_light_stage()
            capture_prototype = capture.GetPrimAtPath(container_path) if capture else None
            if capture_prototype and capture_prototype.IsValid():
                for descendant in Usd.PrimRange(prim):
                    if descendant != prim and descendant.GetTypeName() == 'DistantLight':
                        self._sync_prototype_light(descendant, prim)
            else:
                self.report({'INFO'}, f"Prototype {container_path.rsplit('/', 1)[-1]} is unrelated to this scene; skipping")
            return
        for hash_value in hash_tokens:
            self._replace_prototype_hash({hash_value}, prim)
        # Referenced asset layers are not necessarily in GetLayerStack().
        # Their nested light prims may never occur in changed_prims, yet their
        # composed descendants must appear under each captured placement.
        for descendant in Usd.PrimRange(prim):
            if descendant != prim and self._is_usd_light_prim(descendant):
                self._sync_prototype_light(descendant, prim)

    def _hop_to_prototype_container(self, prim: Usd.Prim) -> bool:
        """Cover a changed child (``ref_*``) by its nearest prototype container.

        Returns whether an ``inst_``/``mesh_`` container ancestor was found.
        The child's content is covered when the container is routed by this
        call or was routed earlier in the run.
        """
        ancestor = prim.GetParent()
        while ancestor and str(ancestor.GetPath()) != '/':
            ancestor_path = str(ancestor.GetPath())
            leaf = ancestor_path.rsplit('/', 1)[-1]
            if re.fullmatch(constants.REGEX_INSTANCE_PATH, leaf):
                if ancestor_path not in self._routed_container_paths:
                    self._route_instance_addition(ancestor)
                return True
            if _prototype_container_path(ancestor_path):
                if ancestor_path not in self._routed_container_paths:
                    self._route_mesh_prototype_addition(ancestor)
                return True
            ancestor = ancestor.GetParent()
        return False

    def _shared_mesh_prototype_path(self, mesh_prim) -> str:
        """Return the prototype Mesh path unless this placement overrides geometry.

        The same reference subtree is mapped under each inst_<HASH> prim by
        USD. Instance-local geometry/primvars edits must retain separate IDs.
        """
        mapped = str(mesh_prim.GetPath())
        from_instance = re.match(
            r'^/RootNode/instances/inst_([0-9A-Fa-f]{16})_\d+(/.*)$', mapped
        )
        if not from_instance:
            return mapped
        proposed = '/RootNode/meshes/mesh_' + from_instance.group(1) + from_instance.group(2)
        prototype = self.stage.GetPrimAtPath(proposed)
        if not prototype or not prototype.IsValid() or not prototype.IsA(UsdGeom.Mesh):
            return mapped
        # A layer's *direct* opinion at this instance path (as opposed to a
        # reference opinion on the prototype) can change mesh attributes.
        for layer in self.stage.GetLayerStack(True):
            spec = layer.GetPrimAtPath(mesh_prim.GetPath())
            if not spec:
                continue
            for prop in spec.properties:
                prop_name = str(getattr(prop, 'name', prop))
                if prop_name in {'points', 'faceVertexCounts', 'faceVertexIndices', 'normals'} or prop_name.startswith('primvars:'):
                    return mapped
        return proposed

    def _materialize_new_placement(self, prim: Usd.Prim, instance_path: str, leaf_name: str) -> list:
        """Create new Blender objects for an added placement.

        One object per ``UsdGeom.Mesh`` composing under the placement; each
        keeps the composed Mesh matrix so nested prototype layouts survive
        (A16), and inherits the placement's visibility and provenance tags.
        """
        created = []
        instance_name = bpy.path.clean_name(leaf_name) + '_mod'
        target_collection = self._target_collection()
        for mesh_prim in [p for p in Usd.PrimRange(prim) if p.IsA(UsdGeom.Mesh)]:
            source_mesh_path = self._shared_mesh_prototype_path(mesh_prim)
            bl_mesh = self._new_placement_mesh_data.get(source_mesh_path)
            if bl_mesh is None:
                mesh_data = mod_apply_utils.get_mesh_data_from_mod(
                    mesh_prim, self.time_code, self.up_axis_is_y, self.report
                )
                if not mesh_data:
                    continue
                verts, faces, uvs_data, normals_data = mesh_data
                bl_mesh = bpy.data.meshes.new(name=instance_name)
                bl_mesh.from_pydata(verts, [], faces)
                bl_mesh.update()
                if uvs_data:
                    self._apply_mesh_uvs(bl_mesh, uvs_data)
                if normals_data:
                    self._apply_mesh_normals(bl_mesh, normals_data)
                bl_mesh['usd_mesh_source_path'] = source_mesh_path
                self._new_placement_mesh_data[source_mesh_path] = bl_mesh

            bl_object = bpy.data.objects.new(name=instance_name, object_data=bl_mesh)
            self._apply_transform(mesh_prim, bl_object)
            try:
                target_collection.objects.link(bl_object)
            except Exception as e:
                self.report({'WARNING'}, f"Failed to link object {bl_object.name}: {e}")
            bl_object[constants.PROP_USD_INSTANCE_PATH] = instance_path
            if mesh_prim != prim:
                bl_object[constants.PROP_USD_PRIM_PATH] = str(mesh_prim.GetPath())
            self._apply_visibility(prim, bl_object)
            self._bind_first_material(bl_object, (mesh_prim if mesh_prim != prim else None, prim))
            self.stats['objects_created'] += 1
            created.append((mesh_prim, bl_object))
        if not created:
            self.report({'WARNING'}, f"No UsdGeom.Mesh composes under added placement {instance_path}; skipping")
        return created

    def _prototype_explicitly_resets_references(self, container_prim) -> bool:
        """Whether a mod layer authors ``references = None`` on this prim.

        A reference to an unavailable file is not a deletion: treating every
        reference opinion as an empty reset used to hide unrelated meshes.
        """
        path = container_prim.GetPath()
        for layer in self._get_layers_with_changes():
            spec = layer.GetPrimAtPath(path)
            if not spec or not self._prim_spec_authors_references(spec):
                continue
            operation = getattr(spec, 'referenceList', None)
            if operation is not None and getattr(operation, 'isExplicit', False):
                if not any((getattr(operation, name, ()) or ()) for name in (
                        'explicitItems', 'addedItems', 'prependedItems', 'appendedItems')):
                    return True
        return False

    def _prototype_has_authored_geometry(self, container_prim, mesh_parts) -> bool:
        """Do the mod layers actually author replacement *geometry*?

        Geometry inside a mod-authored ref_* is new. Capture-inherited Mesh
        prims are not new merely because an unrelated light was added to the
        same prototype. Direct Mesh topology edits and container reference
        replacements also count.
        """
        container_path = str(container_prim.GetPath())
        edit_layers = self._get_layers_with_changes()
        if self._prototype_explicitly_resets_references(container_prim):
            return True
        topology_attrs = {'points', 'faceVertexIndices', 'faceVertexCounts'}
        for part in mesh_parts:
            part_path = str(part.GetPath())
            relative = part_path[len(container_path):].strip('/').split('/')
            path = container_path
            for component in relative[:-1]:
                path += '/' + component
                if component.startswith('ref_') and path in self._mod_authored_prototype_paths:
                    return True
            for layer in edit_layers:
                spec = layer.GetPrimAtPath(part.GetPath())
                if not spec:
                    continue
                if (spec.specifier == Sdf.SpecifierDef and
                        str(getattr(spec, 'typeName', '')) == 'Mesh'):
                    return True
                if any(str(getattr(attr, 'name', attr)) in topology_attrs
                       for attr in spec.attributes):
                    return True
        return False

    def _replace_prototype_hash(self, hash_tokens: Set[str], container_prim: Usd.Prim) -> None:
        """Swap scene objects of *hash_tokens* onto the mod's prototype content.

        The replacement's Mesh prims join into one mesh datablock with part
        transforms baked in container-relative space (capture prototypes store
        raw prototype-local vertices the same way), and each target adopts it
        via ``obj.data``: transforms, visibility and provenance tags stay as
        the scene owns them. An empty reset hides the targets instead - the
        mod asks for the prototype to disappear.
        """
        if hash_tokens & self._replaced_prototype_hashes:
            return  # this hash was already routed to a replacement this run
        self._replaced_prototype_hashes |= hash_tokens

        # Replacement covers pre-existing scene content only: placements this
        # run materialized already show the replacement composed per part, and
        # swapping them onto the joined datablock would duplicate geometry.
        # The container being routed right now is not excluded (a canonical
        # anchor replacement must reach the objects tagged with its own path).
        route_paths = set(self._routed_container_paths)
        route_paths.discard(str(container_prim.GetPath()))
        targets = [
            obj for obj in self._scene_objects_for_hashes(hash_tokens)
            if obj.type == 'MESH' and str(obj.get(constants.PROP_USD_INSTANCE_PATH, '')) not in route_paths
        ]
        if not targets:
            self.report({'INFO'}, f"Prototype {'/'.join(sorted(hash_tokens))} has no scene objects; nothing to replace")
            return

        parts = [p for p in Usd.PrimRange(container_prim) if p.IsA(UsdGeom.Mesh)]
        if not parts:
            # A light-only replacement is NOT a deleted mesh. A mod may add
            # SphereLight/RectLight descendants or a ref_* containing only
            # lights while the geometry is defined in the capture. The mod-only
            # stage often cannot see that capture geometry. Never hide the mesh
            # just because no Mesh composes in this stage.
            descendants = [p for p in Usd.PrimRange(container_prim) if p != container_prim]
            if descendants:
                for obj in targets:
                    if obj.get('remix_hidden_by_empty_prototype'):
                        obj.hide_viewport = False
                        obj.hide_render = False
                        del obj['remix_hidden_by_empty_prototype']
                self.report({'INFO'},
                    f"Prototype {container_prim.GetPath()} has no composed geometry "
                    "but contains additions/overrides; preserving capture meshes")
                return
            # A *bare*, explicit reference reset still hides the prototype,
            # preserving existing Toolkit empty-reset semantics. Tag the
            # change so a later mod can restore only addon-owned hiding.
            if not self._prototype_explicitly_resets_references(container_prim):
                return
            for obj in targets:
                if not (obj.hide_viewport or obj.hide_render):
                    obj['remix_hidden_by_empty_prototype'] = True
                obj.hide_viewport = True
                obj.hide_render = True
                self.report({'INFO'}, f"Empty prototype reset: hid '{obj.name}'")
            return

        # References and attributes may add lights to a capture-owned mesh
        # without changing its geometry. Rebuilding that Mesh from the mod-only
        # stage strips UV sets and can overwrite a shared source datablock.
        if not self._prototype_has_authored_geometry(container_prim, parts):
            self.report({'INFO'},
                f"Prototype {container_prim.GetPath()} adds no mesh geometry; "
                "leaving capture Mesh datablocks and UVs intact")
            return

        try:
            container_matrix = self.xform_cache.GetLocalToWorldTransform(container_prim)
        except Exception:
            container_matrix = Gf.Matrix4d(1.0)
        container_matrix_bl = self._gf_matrix_to_mathutils(container_matrix).inverted_safe()
        container_label = bpy.path.clean_name(str(container_prim.GetPath()).rsplit('/', 1)[-1]) + '_mod'

        joined_verts: List = []
        joined_faces: List = []
        slots: List = []
        uvs_parts: List = []
        normals_parts: List = []
        all_parts_have_uvs = True
        all_parts_have_normals = True
        vertex_offset = 0
        loop_start = 0
        for part_prim in parts:
            mesh_data = mod_apply_utils.get_mesh_data_from_mod(
                part_prim, self.time_code, self.up_axis_is_y, self.report
            )
            if not mesh_data:
                print('DEBUG-JOIN part=%r NO-DATA' % str(part_prim.GetPath()))
                continue
            verts, faces, uvs_data, normals_data = mesh_data
            try:
                part_matrix = self.xform_cache.GetLocalToWorldTransform(part_prim)
            except Exception:
                part_matrix = Gf.Matrix4d(1.0)
            part_bake = self._gf_matrix_to_mathutils(part_matrix) @ container_matrix_bl
            for vert in verts:
                joined_verts.append(tuple(part_bake @ mathutils.Vector((vert[0], vert[1], vert[2]))))
            joined_faces.extend(tuple((index + vertex_offset) for index in face) for face in faces)
            # Save the starting vertex offset, not the already incremented end.
            part_vertex_start = vertex_offset
            vertex_offset += len(verts)
            part_loop_count = sum(len(face) for face in faces)
            part_loop_end = loop_start + part_loop_count
            uvs_parts.append((uvs_data, loop_start, part_vertex_start, part_loop_end))
            normals_parts.append((normals_data, loop_start, part_vertex_start, part_loop_end))
            if uvs_data is None:
                all_parts_have_uvs = False
            if normals_data is None:
                all_parts_have_normals = False
            loop_start = part_loop_end

            binding = UsdShade.MaterialBindingAPI(part_prim).GetDirectBindingRel()
            material_path = str(binding.GetTargets()[0]) if (binding and binding.GetTargets()) else None
            slot_material = None
            if material_path:
                slot_material = mod_apply_utils.get_or_create_mod_instance_material_util(
                    base_material_usd_path=material_path,
                    instance_prim_for_metadata=None,
                    current_mod_stage=self.stage,
                    texture_res_context_path_p=os.path.dirname(self.stage.GetRootLayer().realPath),
                    mod_file_path_for_tex_p=self.stage.GetRootLayer().realPath,
                    mod_base_material_node_cache_param=self.base_material_node_cache,
                    local_material_cache_param=self.material_cache,
                    report_fn=self.report,
                )
            slots.append((slot_material, len(joined_faces)))  # slot + face-range end

        if not joined_faces:
            self.report({'WARNING'}, f"Replacement for {'/'.join(sorted(hash_tokens))} has no mesh geometry; skipping")
            return

        replaced_mesh = bpy.data.meshes.new(name=container_label)
        replaced_mesh.from_pydata(joined_verts, [], joined_faces)
        replaced_mesh.update()

        if any(slot is not None for slot, _ in slots):
            for slot_material, _range_end in slots:
                replaced_mesh.materials.append(slot_material)
            for polygon in replaced_mesh.polygons:
                polygon.material_index = self._part_index_for_face(polygon.index, slots)

        if all_parts_have_uvs and uvs_parts:
            self._apply_joined_mesh_uvs(replaced_mesh, uvs_parts)
        if all_parts_have_normals and normals_parts:
            self._apply_joined_mesh_normals(replaced_mesh, normals_parts)

        # Never silently erase the scene's UV maps when the replacement asset
        # has no valid st data. On identical topology, restore ALL UV layers
        # from the original source; otherwise keep the old geometry and warn.
        if not len(replaced_mesh.uv_layers):
            old_sources = [obj.data for obj in targets
                           if obj.data is not None and len(obj.data.uv_layers)]
            if old_sources:
                source = old_sources[0]
                if (all(same_uv_layout(source, old) for old in old_sources[1:])
                        and copy_uv_layers_if_compatible(source, replaced_mesh)):
                    self.report({'INFO'},
                        f"Restored {len(replaced_mesh.uv_layers)} original UV layers "
                        f"on replacement {container_label} (identical topology)")
                else:
                    self.report({'WARNING'},
                        f"Skipped replacement of {container_label}: missing or invalid "
                        "UV data and incompatible original topology; preserving originals")
                    bpy.data.meshes.remove(replaced_mesh)
                    return

        for obj in targets:
            if obj.get('remix_hidden_by_empty_prototype'):
                obj.hide_viewport = False
                obj.hide_render = False
                del obj['remix_hidden_by_empty_prototype']
            old_data = obj.data
            if old_data:
                for key in list(old_data.keys()):
                    try:
                        replaced_mesh[key] = old_data[key]
                    except Exception:
                        pass  # non-copyable props: provenance is best-effort
            obj.data = replaced_mesh
            self.stats['objects_updated'] += 1
            self.report({'INFO'}, f"Replaced prototype content on '{obj.name}' from the mod")

    @staticmethod
    def _gf_matrix_to_mathutils(matrix):
        """Convert a Gf matrix (row-vector convention) to a mathutils matrix."""
        return mathutils.Matrix((
            (matrix[0][0], matrix[1][0], matrix[2][0], matrix[3][0]),
            (matrix[0][1], matrix[1][1], matrix[2][1], matrix[3][1]),
            (matrix[0][2], matrix[1][2], matrix[2][2], matrix[3][2]),
            (matrix[0][3], matrix[1][3], matrix[2][3], matrix[3][3]),
        ))

    @staticmethod
    def _part_index_for_face(face_index: int, slots: list) -> int:
        """Slot index of the part owning the joined-datablock face *face_index*."""
        for part_index, (_material, range_end) in enumerate(slots):
            if face_index < range_end:
                return part_index
        return 0

    def _apply_joined_mesh_uvs(self, bl_mesh: bpy.types.Mesh, uvs_parts: list) -> bool:
        """Read per-part USD UVs into joined Blender loops without shifting them.

        A layer is authored only when every loop can be mapped. In particular,
        do not create a zero-filled ``st`` layer when source indices are wrong;
        callers can then safely reuse compatible original UVs instead.
        """
        loop_uvs = [None] * len(bl_mesh.loops)
        for uvs_data, loop_start, vertex_start, loop_end in uvs_parts:
            if not uvs_data:
                return False
            values, indices, interp = uvs_data
            if values is None or not len(values):
                return False
            loops = bl_mesh.loops[loop_start:loop_end]
            if interp == UsdGeom.Tokens.faceVarying:
                if indices is not None and len(indices):
                    if len(indices) != len(loops):
                        return False
                    uv_indices = list(indices)
                elif len(values) == len(loops):
                    uv_indices = list(range(len(loops)))
                else:
                    return False
                for offset, idx in enumerate(uv_indices):
                    if idx < 0 or idx >= len(values):
                        return False
                    u, v = values[idx]
                    loop_uvs[loop_start + offset] = (u, 1.0 - v)
            elif interp in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying):
                for loop in loops:
                    idx = loop.vertex_index - vertex_start
                    if idx < 0:
                        return False
                    if indices is not None and len(indices):
                        if idx >= len(indices):
                            return False
                        idx = indices[idx]
                    if idx < 0 or idx >= len(values):
                        return False
                    u, v = values[idx]
                    loop_uvs[loop.index] = (u, 1.0 - v)
            elif interp == UsdGeom.Tokens.uniform:
                polygons = [poly for poly in bl_mesh.polygons
                            if poly.loop_start >= loop_start and
                            poly.loop_start + poly.loop_total <= loop_end]
                if indices is not None and len(indices) and len(indices) != len(polygons):
                    return False
                if not len(indices or ()) and len(values) != len(polygons):
                    return False
                for poly_index, poly in enumerate(polygons):
                    idx = indices[poly_index] if indices is not None and len(indices) else poly_index
                    if idx < 0 or idx >= len(values):
                        return False
                    u, v = values[idx]
                    for li in range(poly.loop_start, poly.loop_start + poly.loop_total):
                        loop_uvs[li] = (u, 1.0 - v)
            elif interp == UsdGeom.Tokens.constant:
                u, v = values[0]
                for loop in loops:
                    loop_uvs[loop.index] = (u, 1.0 - v)
            else:
                # Unknown interpolation: do not fabricate a UV map.
                return False
        if any(coords is None for coords in loop_uvs):
            return False
        uv_layer = bl_mesh.uv_layers.new(name="st")
        uv_layer.data.foreach_set("uv", [coord for pair in loop_uvs for coord in pair])
        return True

    def _apply_joined_mesh_normals(self, bl_mesh: bpy.types.Mesh, normals_parts: list) -> None:
        """Fill custom normals for a joined datablock from per-part data.

        Same contract as ``_apply_joined_mesh_uvs`` for ``(value, index,
        interpolation)`` normal tuples; missing interpolation coverage falls
        back to split normals.
        """
        loop_normals = [(0.0, 0.0, 1.0)] * len(bl_mesh.loops)
        interpolated_any = False
        for part_index, (normals_data, loop_start, vertex_start, loop_end) in enumerate(normals_parts):
            if normals_data is None:
                continue
            values, indices, interp = normals_data
            if not values:
                continue
            part_loops = bl_mesh.loops[loop_start:loop_end]
            if interp == UsdGeom.Tokens.faceVarying:
                if indices and len(indices) == len(part_loops):
                    for offset, norm_index in enumerate(indices):
                        if 0 <= norm_index < len(values):
                            loop_normals[loop_start + offset] = tuple(values[norm_index])
                elif len(values) == len(part_loops):
                    for offset, normal in enumerate(values):
                        loop_normals[loop_start + offset] = tuple(normal)
                interpolated_any = True
            elif interp == UsdGeom.Tokens.vertex:
                for loop in part_loops:
                    local_index = loop.vertex_index - vertex_start
                    if 0 <= local_index < len(values):
                        loop_normals[loop.index] = tuple(values[local_index])
                interpolated_any = True
        if not interpolated_any:
            calc_normals_split_compatible(bl_mesh)
            return
        set_mesh_auto_smooth_compatible(bl_mesh, True)
        try:
            set_custom_normals_compatible(bl_mesh, loop_normals)
        except Exception:
            calc_normals_split_compatible(bl_mesh)

    
    def _apply_transform(self, prim: Usd.Prim, bl_object: bpy.types.Object):
        """Apply USD transform to Blender object."""
        try:
            matrix = mod_apply_utils.get_blender_transform_matrix_from_mod(
                prim, self.xform_cache, self.up_axis_is_y, self.report
            )
            
            loc, rot, scale_vec = matrix.decompose()
            scene_scale = self.context.scene.remix_export_scale
            
            bl_object.location = loc * scene_scale
            bl_object.rotation_quaternion = rot
            bl_object.scale = scale_vec * scene_scale
            
        except Exception as e:
            self.report({'WARNING'}, f"Failed to apply transform to {bl_object.name}: {e}")
    
    def _apply_visibility(self, prim: Usd.Prim, bl_object: bpy.types.Object):
        """Apply USD visibility to Blender object."""
        try:
            imageable = UsdGeom.Imageable(prim)
            vis_attr = imageable.GetVisibilityAttr()
            
            if vis_attr and vis_attr.IsAuthored():
                visibility = vis_attr.Get(self.time_code)
                is_invisible = (visibility == UsdGeom.Tokens.invisible)
                
                if bl_object.hide_viewport != is_invisible or bl_object.hide_render != is_invisible:
                    bl_object.hide_viewport = is_invisible
                    bl_object.hide_render = is_invisible
                    print(f"Set '{bl_object.name}' visibility to {not is_invisible}")
                    
        except Exception as e:
            self.report({'WARNING'}, f"Failed to apply visibility to {bl_object.name}: {e}")
    
    def _apply_material(self, prim: Usd.Prim, bl_object: bpy.types.Object):
        """Apply USD material binding to Blender object."""
        try:
            binding_api = UsdShade.MaterialBindingAPI(prim)
            binding_rel = binding_api.GetDirectBindingRel()
            
            if not binding_rel:
                return
            
            targets = binding_rel.GetTargets()
            if not targets:
                return
            
            material_path = str(targets[0])
            
            # Capture the object's currently-bound material (from capture import,
            # or a previous mod load) BEFORE building the override, so unspecified
            # inputs can be preserved instead of lost (see create_mod_material_nodes_util).
            current_bl_material = bl_object.material_slots[0].material if bl_object.material_slots else None

            # Get or create Blender material
            bl_material = mod_apply_utils.get_or_create_mod_instance_material_util(
                base_material_usd_path=material_path,
                instance_prim_for_metadata=prim,
                current_mod_stage=self.stage,
                texture_res_context_path_p=os.path.dirname(self.stage.GetRootLayer().realPath),
                mod_file_path_for_tex_p=self.stage.GetRootLayer().realPath,
                mod_base_material_node_cache_param=self.base_material_node_cache,
                local_material_cache_param=self.material_cache,
                report_fn=self.report,
                existing_bl_material_param=current_bl_material
            )
            
            if bl_material:
                self.stats['materials_applied'] += 1
                
                self._assign_object_material(bl_object, bl_material)
                print(f"Updated material on '{bl_object.name}'")
                    
        except Exception as e:
            self.report({'WARNING'}, f"Failed to apply material to {bl_object.name}: {e}")
    
    @staticmethod
    def _assign_object_material(bl_object, material):
        """Assign an override without changing materials of shared mesh users."""
        if not bl_object.material_slots:
            bl_object.data.materials.append(None)
        slot = bl_object.material_slots[0]
        slot.link = 'OBJECT'
        slot.material = material

    def _is_specific_light_type(self, prim: Usd.Prim, light_type_name: str) -> bool:
        """Check if a prim is a specific USD light type, handling different USD versions gracefully."""
        if hasattr(UsdLux, light_type_name):
            light_class = getattr(UsdLux, light_type_name)
            try:
                return prim.IsA(light_class)
            except Exception:
                return False
        return False

    def _apply_light_properties(self, prim: Usd.Prim, bl_object: bpy.types.Object):
        """Apply USD light property overrides to an existing Blender light in place.

        FIX: this used to be a no-op stub (only bumped a stats counter) - authored
        color/intensity/shape overrides from mod.usda never actually reached the light.
        Ported from the working implementation in sync_ops.py.
        """
        bl_light_data = bl_object.data
        light_api = UsdLux.LightAPI(prim)
        time_code = self.time_code
        current_scene_scale = self.context.scene.remix_export_scale
        changed_props = []

        # Common properties
        if light_api.GetColorAttr().IsDefined() and light_api.GetColorAttr().IsAuthored():
            new_color = Gf.Vec3f(light_api.GetColorAttr().Get(time_code))
            if tuple(bl_light_data.color) != tuple(new_color):
                bl_light_data.color = new_color
                changed_props.append("color")

        intensity_authored = False
        if light_api.GetIntensityAttr().IsDefined() and light_api.GetIntensityAttr().IsAuthored():
            intensity = light_api.GetIntensityAttr().Get(time_code)
            intensity_authored = True
        else:
            stored_exposure = bl_light_data.get("usd_exposure", None)
            intensity = bl_light_data.energy / pow(2, stored_exposure) if stored_exposure is not None else bl_light_data.energy

        exposure_authored = False
        if light_api.GetExposureAttr().IsDefined() and light_api.GetExposureAttr().IsAuthored():
            exposure = light_api.GetExposureAttr().Get(time_code)
            bl_light_data["usd_exposure"] = exposure
            exposure_authored = True
        else:
            exposure = bl_light_data.get("usd_exposure", 0.0)

        if intensity_authored or exposure_authored:
            new_energy_val = intensity * pow(2, exposure)
            if abs(bl_light_data.energy - new_energy_val) > 1e-5:
                bl_light_data.energy = new_energy_val
                changed_props.append("energy (intensity/exposure)")

        # Native Blender light temperature: the light keeps the USD color as
        # a tint, ``use_temperature``/``temperature`` carry the Kelvin value.
        # The helper stores the temperature-free color, so repeated runs
        # recompute instead of compounding the tint.
        enable_temp_attr = light_api.GetEnableColorTemperatureAttr()
        color_temp_attr = light_api.GetColorTemperatureAttr()
        enable_authored = enable_temp_attr.IsDefined() and enable_temp_attr.IsAuthored()
        temp_authored = color_temp_attr.IsDefined() and color_temp_attr.IsAuthored()
        if enable_authored or temp_authored:
            use_temp = (
                enable_temp_attr.Get(time_code)
                if enable_authored
                else bl_light_data.get('usd_color_temperature') is not None
            )
            kelvin = None
            if use_temp:
                kelvin = (
                    color_temp_attr.Get(time_code) if temp_authored
                    else bl_light_data.get('usd_color_temperature')
                )
            base_color = (
                tuple(light_api.GetColorAttr().Get(time_code))
                if light_api.GetColorAttr().IsDefined()
                else bl_light_data.get('usd_base_color')
            )
            temp_color = mod_apply_utils.mod_light_color_with_temperature(
                bl_light_data, base_color, kelvin
            )
            if tuple(bl_light_data.color) != tuple(temp_color):
                bl_light_data.color = temp_color
                changed_props.append('color (color temperature)')

        # Type-specific properties (only if the underlying Blender light type still matches)
        if self._is_specific_light_type(prim, 'SphereLight'):
            sphere_api = UsdLux.SphereLight(prim)
            is_spot, cone_angle, cone_softness = sphere_cone_parameters(prim, time_code)
            if bl_light_data.type == 'SPOT' and is_spot:
                new_angle = cone_spot_size(cone_angle)
                new_blend = max(0.0, min(1.0, cone_softness))
                if abs(bl_light_data.spot_size - new_angle) > 1e-5:
                    bl_light_data.spot_size = new_angle
                    changed_props.append('spot_size')
                if abs(bl_light_data.spot_blend - new_blend) > 1e-5:
                    bl_light_data.spot_blend = new_blend
                    changed_props.append('spot_blend')
            if bl_light_data.type in {'POINT', 'SPOT'}:
                radius_attr = sphere_api.GetRadiusAttr()
                if radius_attr.IsDefined() and radius_attr.IsAuthored():
                    new_radius = float(radius_attr.Get(time_code)) * current_scene_scale
                    treat_as_point = sphere_api.GetTreatAsPointAttr()
                    if (bl_light_data.type == 'POINT' and treat_as_point.IsDefined()
                            and treat_as_point.Get(time_code)):
                        new_radius = 0.0
                    if abs(bl_light_data.shadow_soft_size - new_radius) > 1e-5:
                        bl_light_data.shadow_soft_size = new_radius
                        changed_props.append('radius')
        elif self._is_specific_light_type(prim, 'RectLight') and bl_light_data.type == 'AREA':
            rect_api = UsdLux.RectLight(prim)
            if rect_api.GetWidthAttr().IsDefined() and rect_api.GetWidthAttr().IsAuthored():
                new_width = rect_api.GetWidthAttr().Get(time_code) * current_scene_scale
                if abs(bl_light_data.size - new_width) > 1e-5:
                    bl_light_data.size = new_width
                    changed_props.append("size (width)")
            if rect_api.GetHeightAttr().IsDefined() and rect_api.GetHeightAttr().IsAuthored():
                new_height = rect_api.GetHeightAttr().Get(time_code) * current_scene_scale
                if hasattr(bl_light_data, 'size_y') and abs(bl_light_data.size_y - new_height) > 1e-5:
                    bl_light_data.size_y = new_height
                    changed_props.append("size_y (height)")
        elif self._is_specific_light_type(prim, 'SpotLight') and bl_light_data.type == 'SPOT':
            _, half_angle, softness = sphere_cone_parameters(prim, time_code)
            bl_light_data.spot_size = cone_spot_size(half_angle)
            bl_light_data.spot_blend = max(0.0, min(1.0, softness))
        elif self._is_specific_light_type(prim, 'DiskLight') and bl_light_data.type == 'POINT':
            disk_api = UsdLux.DiskLight(prim)
            if disk_api.GetRadiusAttr().IsDefined() and disk_api.GetRadiusAttr().IsAuthored():
                new_size = disk_api.GetRadiusAttr().Get(time_code) * 2.0 * current_scene_scale
                if abs(bl_light_data.shadow_soft_size - new_size) > 1e-5:
                    bl_light_data.shadow_soft_size = new_size
                    changed_props.append("size (radius)")
        elif self._is_specific_light_type(prim, 'DistantLight') and bl_light_data.type == 'SUN':
            distant_api = UsdLux.DistantLight(prim)
            if distant_api.GetAngleAttr().IsDefined() and distant_api.GetAngleAttr().IsAuthored():
                new_angle_rad = math.radians(distant_api.GetAngleAttr().Get(time_code) * 2.0)
                if abs(bl_light_data.angle - new_angle_rad) > 1e-5:
                    bl_light_data.angle = new_angle_rad
                    changed_props.append("angle")

        self.stats['lights_updated'] += 1
        if changed_props:
            print(f"  Applied overrides to light '{bl_object.name}': {', '.join(changed_props)}")
    
    def _create_mesh_object(self, prim: Usd.Prim) -> Optional[bpy.types.Object]:
        """Create a new mesh object from USD prim."""
        try:
            mesh_data = mod_apply_utils.get_mesh_data_from_mod(
                prim, self.time_code, self.up_axis_is_y, self.report
            )
            
            if not mesh_data:
                return None
            
            verts, faces, uvs_data, normals_data = mesh_data
            
            # Create mesh
            mesh_name = bpy.path.clean_name(prim.GetName()) + "_mod"
            bl_mesh = bpy.data.meshes.new(name=mesh_name)
            bl_mesh.from_pydata(verts, [], faces)
            bl_mesh.update()
            
            # Apply UVs
            if uvs_data:
                self._apply_mesh_uvs(bl_mesh, uvs_data)
            
            # Apply normals
            if normals_data:
                self._apply_mesh_normals(bl_mesh, normals_data)
            
            # Create object
            bl_object = bpy.data.objects.new(name=mesh_name, object_data=bl_mesh)
            
            return bl_object
            
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create mesh from {prim.GetPath()}: {e}")
            return None
    
    def _create_light_object(self, prim: Usd.Prim) -> Optional[bpy.types.Object]:
        """Create a new light object from USD prim."""
        try:
            scene_scale = self.context.scene.get('remix_import_unit_scale')
            if scene_scale is None:
                scene_scale = self.context.scene.remix_export_scale
            return mod_apply_utils.create_new_blender_light_from_mod(
                prim, self.time_code, scene_scale, self.report
            )
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create light from {prim.GetPath()}: {e}")
            return None
    
    def _apply_mesh_uvs(self, bl_mesh: bpy.types.Mesh, uvs_data: Tuple):
        """Apply UV data to mesh."""
        uv_values, uv_indices_list, uv_interpolation = uvs_data
        
        if not uv_values or not bl_mesh.loops:
            return
        
        uv_layer = bl_mesh.uv_layers.new(name="st")
        blender_loop_uvs = [(0.0, 0.0)] * len(bl_mesh.loops)
        
        # Handle different interpolation types
        if uv_interpolation == UsdGeom.Tokens.faceVarying:
            if uv_indices_list and len(uv_indices_list) == len(bl_mesh.loops):
                for i, loop in enumerate(bl_mesh.loops):
                    uv_idx = uv_indices_list[i]
                    if 0 <= uv_idx < len(uv_values):
                        u, v = uv_values[uv_idx]
                        blender_loop_uvs[loop.index] = (u, 1.0 - v)  # Flip V
            elif len(uv_values) == len(bl_mesh.loops):
                for i, loop in enumerate(bl_mesh.loops):
                    u, v = uv_values[i]
                    blender_loop_uvs[loop.index] = (u, 1.0 - v)
        
        elif uv_interpolation == UsdGeom.Tokens.vertex:
            if len(uv_values) == len(bl_mesh.vertices):
                for loop in bl_mesh.loops:
                    u, v = uv_values[loop.vertex_index]
                    blender_loop_uvs[loop.index] = (u, 1.0 - v)
        
        # Set UVs
        flattened_uvs = [coord for pair in blender_loop_uvs for coord in pair]
        uv_layer.data.foreach_set("uv", flattened_uvs)
    
    def _apply_mesh_normals(self, bl_mesh: bpy.types.Mesh, normals_data: Tuple):
        """Apply normal data to mesh."""
        norm_values, norm_indices_list, norm_interpolation = normals_data
        
        if not norm_values or not bl_mesh.loops:
            calc_normals_split_compatible(bl_mesh)
            return
        
        set_mesh_auto_smooth_compatible(bl_mesh, True)
        
        loop_normals = [(0.0, 0.0, 1.0)] * len(bl_mesh.loops)
        
        # Handle different interpolation types
        if norm_interpolation == UsdGeom.Tokens.vertex:
            if len(norm_values) == len(bl_mesh.vertices):
                for loop in bl_mesh.loops:
                    loop_normals[loop.index] = tuple(norm_values[loop.vertex_index])
        
        elif norm_interpolation == UsdGeom.Tokens.faceVarying:
            if norm_indices_list and len(norm_indices_list) == len(bl_mesh.loops):
                for i, loop in enumerate(bl_mesh.loops):
                    norm_idx = norm_indices_list[i]
                    if 0 <= norm_idx < len(norm_values):
                        loop_normals[loop.index] = tuple(norm_values[norm_idx])
            elif len(norm_values) == len(bl_mesh.loops):
                for loop in bl_mesh.loops:
                    loop_normals[loop.index] = tuple(norm_values[loop.index])
        
        # Set normals
        try:
            set_custom_normals_compatible(bl_mesh, loop_normals)
        except Exception:
            calc_normals_split_compatible(bl_mesh)
    
    def _is_usd_light_prim(self, prim: Usd.Prim) -> bool:
        """Check if prim is a USD light type."""
        light_types = ['SphereLight', 'RectLight', 'DiskLight', 'DistantLight', 
                      'SpotLight', 'CylinderLight', 'GeometryLight']
        
        for light_type in light_types:
            if hasattr(UsdLux, light_type):
                light_class = getattr(UsdLux, light_type)
                try:
                    if prim.IsA(light_class):
                        return True
                except:
                    continue
        
        try:
            if UsdLux.LightAPI(prim):
                return True
        except:
            pass
        
        return False
    
    def _report_statistics(self):
        """Report loading statistics."""
        self.report({'INFO'}, 
            f"Processed {self.stats['prims_processed']} prims: "
            f"{self.stats['objects_updated']} updated, "
            f"{self.stats['objects_created']} created, "
            f"{self.stats['materials_applied']} materials applied, "
            f"{self.stats['lights_updated']} lights updated"
        )
    
    def _disable_viewport_updates(self):
        """Disable viewport updates for better performance during batch operations."""
        # Store original state
        self._viewport_state = {
            'use_simplify': bpy.context.scene.render.use_simplify,
            'simplify_subdivision': bpy.context.scene.render.simplify_subdivision,
            'use_auto_refresh': []
        }
        
        # Enable viewport simplification
        bpy.context.scene.render.use_simplify = True
        bpy.context.scene.render.simplify_subdivision = 0
        
        # Disable auto-refresh for all 3D viewports
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            self._viewport_state['use_auto_refresh'].append((space, space.show_object_viewport_curve))
                            # Keep curve display but disable some heavy options if available
                            pass  # Most updates are internal to Blender
    
    def _enable_viewport_updates(self):
        """Re-enable viewport updates after batch operations."""
        if not hasattr(self, '_viewport_state'):
            return
        
        # Restore original viewport settings
        bpy.context.scene.render.use_simplify = self._viewport_state['use_simplify']
        bpy.context.scene.render.simplify_subdivision = self._viewport_state['simplify_subdivision']
        
        # Restore auto-refresh
        for space, original_value in self._viewport_state.get('use_auto_refresh', []):
            space.show_object_viewport_curve = original_value
        
        # Force viewport update
        self._refresh_viewport()
    
    def _refresh_viewport(self):
        """Refresh the 3D viewport."""
        for area in self.context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
