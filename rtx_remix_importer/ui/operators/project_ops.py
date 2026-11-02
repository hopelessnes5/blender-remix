import bpy
import os
import bpy_extras

try:
    from pxr import Usd, Sdf, UsdGeom, UsdShade, Vt, UsdLux, Gf 
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False
    Usd = None 
    Sdf = None
    UsdGeom = None 
    UsdShade = None
    Vt = None
    UsdLux = None
    Gf = None

from ... import write_guard

def _require_writable_layer(path):
    """Open a layer for authoring, refusing capture and locked layers."""
    # Shared guard: identical semantics for every write entry point (audit A01).
    return write_guard.open_writable_layer(path)


def _layer_identity(layer):
    return os.path.normcase(os.path.realpath(layer.realPath or layer.identifier))


def _sublayer_graph(root):
    """Return descendant IDs and whether this tree contains a capture.

    Fail closed on missing dependencies: an unresolved subtree cannot be
    checked for cycles or capture ordering safely.
    """
    seen = set()
    active = set()
    contains_capture = False

    def visit(layer):
        nonlocal contains_capture
        identity = _layer_identity(layer)
        if identity in active:
            raise ValueError(f'Cycle in sublayer graph: {layer.identifier}')
        if identity in seen:
            return
        seen.add(identity)
        active.add(identity)
        if layer.customLayerData.get('lightspeed_layer_type') == 'capture':
            contains_capture = True
        for subpath in layer.subLayerPaths:
            child = Sdf.Layer.FindRelativeToLayer(layer, subpath)
            if child is None:
                child = Sdf.Layer.FindOrOpen(layer.ComputeAbsolutePath(subpath))
            if child is None:
                raise ValueError(f'Cannot inspect sublayer {subpath} in {layer.identifier}')
            visit(child)
        active.remove(identity)

    visit(root)
    return seen, contains_capture


def _insert_sublayer(target_layer, child_path):
    """Add a sublayer without cycles, duplicate paths or stronger captures."""
    target_layer = _require_writable_layer(target_layer.realPath or target_layer.identifier)
    child = Sdf.Layer.FindOrOpen(child_path)
    if not child:
        raise ValueError(f'Cannot open sublayer: {child_path}')
    target_id, child_id = _layer_identity(target_layer), _layer_identity(child)
    child_ids, child_has_capture = _sublayer_graph(child)
    if target_id in child_ids:
        raise ValueError(f'Adding {child_path} would create a sublayer cycle')

    existing = []
    for path in target_layer.subLayerPaths:
        layer = Sdf.Layer.FindRelativeToLayer(target_layer, path)
        if layer is None:
            layer = Sdf.Layer.FindOrOpen(target_layer.ComputeAbsolutePath(path))
        if layer is None:
            raise ValueError(f'Cannot inspect existing sublayer: {path}')
        if _layer_identity(layer) == child_id:
            return False
        _, has_capture = _sublayer_graph(layer)
        existing.append((path, has_capture))

    relative = os.path.relpath(child.realPath, start=os.path.dirname(target_layer.realPath)).replace('\\', '/')
    if not relative.startswith('..'):
        relative = './' + relative
    # An imported capture subtree belongs at the weak end. A replacement is
    # inserted ahead of any existing subtree containing a capture.
    insertion = len(existing) if child_has_capture else next(
        (i for i, (_, has_capture) in enumerate(existing) if has_capture), len(existing)
    )
    target_layer.subLayerPaths.insert(insertion, relative)
    return True

class LoadRemixProject(bpy.types.Operator):
    """Loads the specified Remix mod file and populates the sublayer list"""
    bl_idname = "remix.load_project"
    bl_label = "Load Remix Project"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Check module-level USD_AVAILABLE
        return USD_AVAILABLE and context.scene.remix_mod_file_path

    def execute(self, context):
        if not USD_AVAILABLE: # Check module-level variable
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        mod_file_path = bpy.path.abspath(context.scene.remix_mod_file_path)
        
        # Check if path is a directory instead of a file
        if os.path.isdir(mod_file_path):
            # Try to find mod.usda in the directory
            potential_mod = os.path.join(mod_file_path, "mod.usda")
            if os.path.exists(potential_mod):
                mod_file_path = potential_mod
                self.report({'INFO'}, f"Using mod.usda from directory: {mod_file_path}")
            else:
                self.report({'ERROR'}, f"Path is a directory, not a file. Please select mod.usda: {mod_file_path}")
                return {'CANCELLED'}
        
        if not os.path.exists(mod_file_path):
            self.report({'ERROR'}, f"Mod file not found: {mod_file_path}")
            return {'CANCELLED'}
        
        if not mod_file_path.lower().endswith(('.usda', '.usdc', '.usd')):
            self.report({'ERROR'}, f"File must be a USD file (.usda, .usdc, or .usd): {mod_file_path}")
            return {'CANCELLED'}
        
        project_dir = os.path.dirname(mod_file_path)
        print(f"Loading Remix project from: {mod_file_path}")
        print(f"Project Directory: {project_dir}")
        # Store the detected project dir for display
        context.scene.remix_project_root_display = project_dir 

        try:
            stage = Usd.Stage.Open(mod_file_path)
            if not stage:
                self.report({'ERROR'}, f"Failed to open USD stage: {mod_file_path}")
                return {'CANCELLED'}

            root_layer = stage.GetRootLayer()
            
            # Build a tree structure recursively
            def scan_sublayers_recursive(layer, depth=0, visited=None):
                """Recursively scan sublayers and build hierarchy.
                
                Returns list of tuples: (full_path, display_name, rel_path, depth, has_children)
                """
                if visited is None:
                    visited = set()
                
                result = []
                sublayer_paths = layer.subLayerPaths
                
                if not sublayer_paths:
                    return result
                
                for rel_path in sublayer_paths:
                    full_path = layer.ComputeAbsolutePath(rel_path)
                    if not full_path:
                        print(f"  WARNING: Could not resolve sublayer path: {rel_path}")
                        continue
                    
                    # Skip invalid paths (directories, empty, etc.)
                    if not full_path.lower().endswith(('.usda', '.usdc', '.usd')):
                        print(f"  WARNING: Skipping non-USD file: {full_path}")
                        continue
                    
                    if not os.path.exists(full_path):
                        print(f"  WARNING: Sublayer file not found: {full_path}")
                        continue
                    
                    # Avoid infinite loops from circular references
                    if full_path in visited:
                        print(f"  WARNING: Circular reference detected for {full_path}")
                        continue
                    
                    visited.add(full_path)
                    display_name = os.path.basename(full_path)
                    
                    # Try to open this sublayer to check if it has children
                    child_layer = None
                    has_children = False
                    try:
                        child_layer = Sdf.Layer.FindOrOpen(full_path)
                        if child_layer and child_layer.subLayerPaths:
                            has_children = True
                    except Exception as e:
                        print(f"  WARNING: Could not open sublayer {full_path}: {e}")
                    
                    # Add this layer to result
                    result.append((full_path, display_name, rel_path, depth, has_children))
                    print(f"  {'  ' * depth}Found sublayer: {display_name} (depth={depth})")
                    
                    # Recursively scan children if any
                    if child_layer and has_children:
                        children = scan_sublayers_recursive(child_layer, depth + 1, visited)
                        result.extend(children)
                
                return result
            
            # Scan all sublayers recursively starting at depth 1 (since root will be depth 0)
            ordered_sublayers = scan_sublayers_recursive(root_layer, depth=1)
            
            if not ordered_sublayers:
                print("No sublayers found in mod file.")
            
            # Add root mod.usda file to the beginning of the list at depth 0
            root_display_name = os.path.basename(mod_file_path)
            root_has_children = len(ordered_sublayers) > 0
            
            # Insert root file at the beginning with depth 0
            ordered_sublayers.insert(0, (mod_file_path, root_display_name, "", 0, root_has_children))
            
            # Convert to dict format for ID property storage (ID properties need dicts for complex data)
            ordered_sublayers_as_dicts = []
            for full_path, display_name, rel_path, depth, has_children in ordered_sublayers:
                ordered_sublayers_as_dicts.append({
                    'full_path': full_path,
                    'display_name': display_name,
                    'rel_path': rel_path,
                    'depth': depth,
                    'has_children': has_children
                })
            
            # Store the hierarchical list in the scene
            context.scene["_remix_sublayers_ordered"] = ordered_sublayers_as_dicts
            # Reset active sublayer path
            context.scene.remix_active_sublayer_path = ""

            self.report({'INFO'}, f"Loaded {len(ordered_sublayers)} total sublayers (including nested) from {os.path.basename(mod_file_path)}")

        except Exception as e:
            self.report({'ERROR'}, f"Failed to load project: {e}")
            if "_remix_sublayers_ordered" in context.scene:
                del context.scene["_remix_sublayers_ordered"]
            return {'CANCELLED'}
        
        return {'FINISHED'}

class SetTargetSublayer(bpy.types.Operator):
    """Sets the active sublayer path for export operations"""
    bl_idname = "remix.set_target_sublayer"
    bl_label = "Set Active Target Sublayer"
    bl_options = {'REGISTER', 'UNDO'}

    sublayer_path: bpy.props.StringProperty(
        name="Sublayer Path",
        description="Full path of the sublayer to set as active target"
    )

    def execute(self, context):
        if self.sublayer_path:
            try:
                _require_writable_layer(bpy.path.abspath(self.sublayer_path))
            except Exception as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
            context.scene.remix_active_sublayer_path = self.sublayer_path
            print(f"Set active export target: {self.sublayer_path}")
            # Force UI redraw if necessary (might not be needed depending on context)
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
                        break
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, "No sublayer path provided.")
            return {'CANCELLED'}

class CreateRemixSublayer(bpy.types.Operator):
    """Creates a new sublayer .usda file and adds it to the main mod file"""
    bl_idname = "remix.create_sublayer"
    bl_label = "Create New Sublayer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Check module-level USD_AVAILABLE
        return (USD_AVAILABLE and 
                context.scene.remix_mod_file_path and 
                context.scene.remix_new_sublayer_name)

    def execute(self, context):
        if not USD_AVAILABLE: # Check module-level variable
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        mod_file_path = bpy.path.abspath(context.scene.remix_mod_file_path)
        new_name = context.scene.remix_new_sublayer_name
        target_path = context.scene.remix_active_sublayer_path or mod_file_path
        try:
            _require_writable_layer(bpy.path.abspath(target_path))
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if not new_name:
            self.report({'ERROR'}, "New sublayer name cannot be empty.")
            return {'CANCELLED'}
        
        # Sanitize name for file usage
        file_name_base = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in new_name)
        if not file_name_base:
             self.report({'ERROR'}, "Invalid characters in new sublayer name.")
             return {'CANCELLED'}
        
        new_file_name = f"{file_name_base}.usda"
        project_dir = os.path.dirname(mod_file_path)
        
        # Determine where to create the new sublayer file
        # Each sublayer gets its own directory named after itself
        active_sublayer = context.scene.remix_active_sublayer_path
        
        # Normalize paths for comparison
        active_sublayer_normalized = os.path.normpath(active_sublayer) if active_sublayer else None
        mod_file_normalized = os.path.normpath(mod_file_path)
        
        # Check if active is a sublayer (not the root mod.usda)
        if (active_sublayer_normalized and 
            os.path.exists(active_sublayer_normalized) and 
            active_sublayer_normalized != mod_file_normalized):
            # Create in a subdirectory next to the parent sublayer
            # E.g., if active is base_mod/replacements/replacements.usda
            # Create in base_mod/replacements/materials/materials.usda
            parent_dir = os.path.dirname(active_sublayer)
            sublayers_dir = os.path.join(parent_dir, file_name_base)
        else:
            # Create in mod root directory with own directory
            # E.g., base_mod/materials/materials.usda
            sublayers_dir = os.path.join(project_dir, file_name_base)
        
        new_sublayer_path = os.path.normpath(os.path.join(sublayers_dir, new_file_name))

        # Create sublayers directory if it doesn't exist
        try:
            os.makedirs(sublayers_dir, exist_ok=True)
        except OSError as e:
            self.report({'ERROR'}, f"Could not create subUSDAs directory: {e}")
            return {'CANCELLED'}

        # Check if file already exists
        if os.path.exists(new_sublayer_path):
             self.report({'WARNING'}, f"Sublayer file already exists: {new_sublayer_path}. Cannot overwrite.")
             # Optionally, offer to just add existing? For now, cancel.
             return {'CANCELLED'}

        # Create the new empty sublayer file
        try:
            new_stage = Usd.Stage.CreateNew(new_sublayer_path)
            if not new_stage:
                 raise RuntimeError("Failed to create new stage object.")
            # Add minimal structure - Use Sdf.Path
            root_prim_path = Sdf.Path("/RootNode")
            # Use OverridePrim instead of DefinePrim to match reference format
            root_prim = new_stage.OverridePrim(root_prim_path) 
            UsdGeom.SetStageUpAxis(new_stage, UsdGeom.Tokens.z)
            
            # Add custom layer data to match reference format
            root_layer = new_stage.GetRootLayer()
            custom_data = {
                'lightspeed_game_name': context.scene.remix_game_name,
                'lightspeed_layer_type': "replacement",
            }
            root_layer.customLayerData = custom_data
            
            # Add other metadata to match reference
            root_layer.startTimeCode = 0 # Reference has startTimeCode = 0
            root_layer.endTimeCode = 100 # Reference has endTimeCode = 100
            root_layer.timeCodesPerSecond = 24 # Reference has timeCodesPerSecond = 24
            root_layer.metersPerUnit = 1 # Reference has metersPerUnit = 1
            # Remove defaultPrim setting if present (not in reference header)
            if root_layer.HasDefaultPrim():
                 root_layer.ClearDefaultPrim()
            
            write_guard.ensure_writable_layer(root_layer)
            new_stage.GetRootLayer().Save()
            print(f"Created new sublayer file: {new_sublayer_path}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create new sublayer file {new_sublayer_path}: {e}")
            return {'CANCELLED'}

        # Add reference to the active sublayer if one is selected, otherwise to the main mod file
        try:
            # Determine target file - use active sublayer if selected, otherwise mod file
            active_sublayer = context.scene.remix_active_sublayer_path
            if active_sublayer and os.path.exists(active_sublayer):
                target_file = active_sublayer
                target_name = os.path.basename(target_file)
            else:
                target_file = mod_file_path
                target_name = os.path.basename(mod_file_path)
            
            # Open the target layer
            target_layer = _require_writable_layer(target_file)
            if not target_layer:
                raise RuntimeError(f"Failed to open target file {target_file} to add sublayer.")
            
            # Calculate relative path from target file to new sublayer
            target_dir = os.path.dirname(target_file)
            relative_path = os.path.relpath(new_sublayer_path, start=target_dir).replace('\\', '/')
            # Ensure it starts with ./ if in the same directory or subdirs
            if not relative_path.startswith(".."):
                relative_path = f"./{relative_path}"

            if not _insert_sublayer(target_layer, new_sublayer_path):
                self.report({'INFO'}, f"Sublayer '{relative_path}' already exists in {target_name}.")
            else:
                write_guard.ensure_writable_layer(target_layer)
                target_layer.Save()
                print(f"Added '{relative_path}' to sublayers in {target_name}")
                if active_sublayer:
                    self.report({'INFO'}, f"Created and added sublayer '{new_file_name}' to {target_name}.")
                else:
                    self.report({'INFO'}, f"Created and added sublayer '{new_file_name}'.")

        except Exception as e:
             self.report({'ERROR'}, f"Failed to add sublayer reference to {mod_file_path}: {e}")
             return {'CANCELLED'}

        # Refresh the UI list by calling the load operator
        bpy.ops.remix.load_project()

        return {'FINISHED'}

class AddRemixSublayer(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    """Selects an existing USD file and adds it as a sublayer to the main mod file"""
    bl_idname = "remix.add_sublayer"
    bl_label = "Add Existing Sublayer"
    bl_options = {'REGISTER', 'UNDO'}

    # ImportHelper properties
    filename_ext = ".usda"
    filter_glob: bpy.props.StringProperty(
        default="*.usda",
        options={'HIDDEN'},
        maxlen=255, 
    )

    @classmethod
    def poll(cls, context):
        # Check module-level USD_AVAILABLE
        return USD_AVAILABLE and context.scene.remix_mod_file_path

    def execute(self, context):
        if not USD_AVAILABLE: # Check module-level variable
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        mod_file_path = bpy.path.abspath(context.scene.remix_mod_file_path)
        existing_sublayer_path = bpy.path.abspath(self.filepath) # Path selected by user
        project_dir = os.path.dirname(mod_file_path)

        if not os.path.exists(mod_file_path):
            self.report({'ERROR'}, f"Mod file not found: {mod_file_path}")
            return {'CANCELLED'}
        if not os.path.exists(existing_sublayer_path):
             self.report({'ERROR'}, f"Selected sublayer file not found: {existing_sublayer_path}")
             return {'CANCELLED'}
        if not existing_sublayer_path.lower().endswith(".usda"):
             self.report({'ERROR'}, f"Selected file must be a .usda file: {existing_sublayer_path}")
             return {'CANCELLED'}
             
        # Add reference to the active sublayer if one is selected, otherwise to the main mod file
        try:
            # Determine target file - use active sublayer if selected, otherwise mod file
            active_sublayer = context.scene.remix_active_sublayer_path
            if active_sublayer and os.path.exists(active_sublayer):
                target_file = active_sublayer
                target_name = os.path.basename(target_file)
            else:
                target_file = mod_file_path
                target_name = os.path.basename(mod_file_path)
            
            # Open the target layer
            target_layer = _require_writable_layer(target_file)
            if not target_layer:
                raise RuntimeError(f"Failed to open target file {target_file} to add sublayer.")
            
            # Calculate relative path from target file to existing sublayer
            target_dir = os.path.dirname(target_file)
            relative_path = os.path.relpath(existing_sublayer_path, start=target_dir).replace('\\', '/')
            # Ensure it starts with ./ if in the same directory or subdirs
            if not relative_path.startswith(".."):
                relative_path = f"./{relative_path}"

            if not _insert_sublayer(target_layer, existing_sublayer_path):
                self.report({'INFO'}, f"Sublayer '{relative_path}' already exists in {target_name}. No changes made.")
            else:
                write_guard.ensure_writable_layer(target_layer)
                target_layer.Save()
                print(f"Added '{relative_path}' to sublayers in {target_name}")
                if active_sublayer:
                    self.report({'INFO'}, f"Added existing sublayer '{os.path.basename(existing_sublayer_path)}' to {target_name}.")
                else:
                    self.report({'INFO'}, f"Added existing sublayer '{os.path.basename(existing_sublayer_path)}'.")
                # Refresh the UI list by calling the load operator
                bpy.ops.remix.load_project()

        except Exception as e:
             self.report({'ERROR'}, f"Failed to add sublayer reference: {e}")
             return {'CANCELLED'}
             
        return {'FINISHED'}

class CreateRemixModFile(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    """Creates a new, empty mod.usda file for an RTX Remix project"""
    bl_idname = "remix.create_mod_file"
    bl_label = "Create New Mod File"
    bl_options = {'REGISTER', 'UNDO'}

    # ExportHelper properties
    filename_ext = ".usda"
    filename: bpy.props.StringProperty(default="mod.usda") # Default filename
    filter_glob: bpy.props.StringProperty(
        default="*.usda",
        options={'HIDDEN'},
        maxlen=255, 
    )

    # Check USD availability
    @classmethod
    def poll(cls, context):
        return USD_AVAILABLE

    def execute(self, context):
        if not USD_AVAILABLE:
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        new_mod_path = bpy.path.abspath(self.filepath) # Get path from file browser
        
        if not new_mod_path.lower().endswith(".usda"):
            self.report({'ERROR'}, "File must end with .usda")
            return {'CANCELLED'}
            
        # Prevent overwriting existing files
        if os.path.exists(new_mod_path):
            self.report({'ERROR'}, f"File already exists: {new_mod_path}. Cannot overwrite.")
            return {'CANCELLED'}
            
        # Create the new mod file stage
        try:
            stage = Usd.Stage.CreateNew(new_mod_path)
            if not stage:
                raise RuntimeError("Failed to create new stage object.")
            
            # Set basic stage metadata
            UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
            stage.SetMetadata("timeCodesPerSecond", 24) # Common default
            
            # Add standard Remix replacement mod custom layer data
            root_layer = stage.GetRootLayer()
            custom_data = {
                'lightspeed_game_name': context.scene.remix_game_name,
                'lightspeed_layer_type': "replacement",
            }
            root_layer.customLayerData = custom_data
            
            # Add other metadata to match reference
            root_layer.startTimeCode = 0 # Reference has startTimeCode = 0
            root_layer.endTimeCode = 100 # Reference has endTimeCode = 100
            root_layer.timeCodesPerSecond = 24 # Reference has timeCodesPerSecond = 24
            root_layer.metersPerUnit = 1 # Reference has metersPerUnit = 1
            # Remove defaultPrim setting if present (not in reference header)
            if root_layer.HasDefaultPrim():
                 root_layer.ClearDefaultPrim()
            
            # Ensure the file has content before trying to load it
            write_guard.ensure_writable_layer(root_layer)
            root_layer.Save()
            print(f"Created new mod file: {new_mod_path}")
            self.report({'INFO'}, f"Created new mod file: {os.path.basename(new_mod_path)}")

            # Set the newly created file as the active project file in the UI
            context.scene.remix_mod_file_path = new_mod_path
            # Automatically load the new (empty) project
            bpy.ops.remix.load_project()

        except Exception as e:
            self.report({'ERROR'}, f"Failed to create new mod file {new_mod_path}: {e}")
            return {'CANCELLED'}
            
        return {'FINISHED'} 
