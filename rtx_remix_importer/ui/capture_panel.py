import bpy
import os
from bpy.utils import previews
from .. import core_utils
from .operators.capture_ops import *
from .operators.utility_ops import *

# Global preview collection
preview_collections = {}

class PT_RemixCapturePanel(bpy.types.Panel):
    """Panel for managing RTX Remix capture imports"""
    bl_label = "Captures"
    bl_idname = "SCENE_PT_remix_capture"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RTX Remix"
    bl_order = 0  # Initial setup above the mod workspace
    bl_context = ""
    
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # Select the game and mod only once, then collapse this panel. All
        # subsequent asset/sublayer export work happens in RTX Remix Project.
        setup = layout.box()
        header = setup.row(align=True)
        header.label(text="Game and Capture Setup", icon='SETTINGS')
        if scene.remix_setup_finished:
            header.prop(scene, 'remix_setup_finished', text='Edit Setup',
                        icon='PREFERENCES', toggle=True)
            if scene.remix_mod_file_path:
                setup.label(text=f'Active mod: {os.path.basename(os.path.dirname(bpy.path.abspath(scene.remix_mod_file_path)))}',
                            icon='CHECKMARK')
            else:
                setup.label(text='No mod selected', icon='INFO')
            setup.label(text='Workspace is in RTX Remix Project.', icon='FILE_3D')
            # Keep Captures out of the way during mod/sublayer editing.
            return

        # Stored outside the .blend in Blender's user config, so different
        # projects and Blender sessions share the same saved game locations.
        saved_row = setup.row(align=True)
        saved_row.prop(scene, 'remix_saved_game_choice', text='Saved Games')
        saved_row.operator('remix.remove_saved_game', text='', icon='REMOVE')

        row_game = setup.row(align=True)
        row_game.prop(scene, 'remix_game_folder_path', text='Game Folder')
        row_game.operator('remix.rescan_game_folder', text='', icon='FILE_REFRESH')

        save_row = setup.row(align=True)
        save_row.prop(scene, 'remix_saved_game_label', text='Name')
        save_row.operator('remix.save_game_location', text='Save / Rename', icon='ADD')
        if scene.remix_saved_games_status:
            setup.label(text=scene.remix_saved_games_status, icon='INFO')

        if scene.remix_discovery_status:
            setup.label(text=scene.remix_discovery_status,
                        icon='CHECKMARK' if scene.remix_remix_folder_path else 'INFO')

        if scene.remix_remix_folder_path:
            game_root = scene.remix_detected_game_root or scene.remix_game_folder_path
            for name, folder in [('RTX Remix', scene.remix_remix_folder_path),
                                 ('Mods', scene.remix_mods_folder_path),
                                 ('Captures', scene.remix_capture_folder_path)]:
                if not folder:
                    continue
                try:
                    relative = os.path.relpath(folder, bpy.path.abspath(game_root))
                except (ValueError, OSError):
                    relative = folder
                setup.label(text=f'{name}: {relative}', icon='FILE_FOLDER')

            if scene.remix_mods_folder_path:
                setup.prop(scene, 'remix_active_mod_choice', text='Active Mod')
            if scene.remix_mod_file_path:
                setup.label(text=f'Chosen mod: {os.path.basename(os.path.dirname(bpy.path.abspath(scene.remix_mod_file_path)))}',
                            icon='CHECKMARK')
        else:
            setup.label(text='Select a game containing an rtx-remix folder.', icon='INFO')

        if scene.remix_mod_load_status:
            setup.label(text=scene.remix_mod_load_status, icon='ERROR')

        if scene.remix_capture_folder_path:
            row_captures = setup.row(align=True)
            row_captures.label(text='Capture files detected automatically', icon='FILE_3D')
            row_captures.operator(ScanCaptureFolder.bl_idname, icon='FILE_REFRESH',
                                  text='Refresh Captures')
        else:
            setup.label(text='No captures folder found.', icon='INFO')

        footer = setup.row()
        footer.enabled = bool(scene.remix_mod_file_path)
        footer.prop(scene, 'remix_setup_finished', text='Finish Setup - Work with Mod',
                    icon='CHECKMARK', toggle=True)

        # Capture utilities are only relevant during the initial setup.
        box = layout.box()
        cache_row = box.row(align=True)
        cache_row.operator(ClearMaterialCache.bl_idname, icon='TRASH', text="Clear Cache")
        cache_row.operator(FixBrokenTextures.bl_idname, icon='TEXTURE', text="Fix Broken Textures")
        cache_row.scale_y = 0.8  # Make it smaller since it's a utility function
        
        # Import Settings
        if scene.remix_capture_folder_path:
            settings_box = layout.box()
            settings_box.label(text="Import Settings", icon='SETTINGS')
            
            col = settings_box.column(align=True)
            col.prop(scene, "remix_capture_scene_scale")
            col.prop(scene, "remix_capture_import_materials")
            col.prop(scene, "remix_capture_import_lights")
            
            # Advanced settings (collapsible)
            col.separator()
        
        # Available Captures List
        captures_box = layout.box()
        captures_box.label(text="Available Captures", icon='FILE_3D')
        
        # Get the scanned captures
        available_captures = scene.remix_captures
        
        if not available_captures:
            if scene.remix_capture_folder_path:
                captures_box.label(text="No captures found. Folder may be empty.", icon='INFO')
            else:
                captures_box.label(text="Select Game Folder above.", icon='INFO')
        else:
            # Show count and controls
            header_row = captures_box.row(align=True)
            header_row.label(text=f"Found: {len(available_captures)} files")
            
            # Batch import button
            selected_count = len([c for c in available_captures if c.is_selected])
            if selected_count > 0:
                op = header_row.operator("remix.batch_import_selected_captures", text=f"Import {selected_count} Selected")
            else:
                op = header_row.operator("remix.batch_import_all_captures", text=f"Import All")

            header_row.operator(ClearCaptureList.bl_idname, icon='TRASH', text="Clear")
            
            captures_box.separator()

            # Align View button is now a menu
            # Check if there are any imported cameras to decide if the menu should be active
            imported_cameras_exist = any(
                obj.type == 'CAMERA' and 'is_remix_camera' in obj.data 
                for obj in context.scene.objects
            )
            
            row = captures_box.row()
            row.enabled = imported_cameras_exist
            row.menu("UI_MT_remix_camera_menu", text="Align View to Imported Camera", icon='CAMERA_DATA')
            
            captures_box.separator()

            # Draw the UIList
            captures_box.template_list(
                "REMIX_UL_capture_list", # UIList class name
                "",                      # propertyname of the list's data
                scene,                   # data pointer for the list
                "remix_captures",        # propertyname of the list's data
                scene,                   # data pointer for the active index
                "remix_captures_index",  # propertyname for the active index
                rows=10                  # Number of rows to display
            )

            # --- Thumbnail Preview ---
            active_capture_index = scene.remix_captures_index
            if 0 <= active_capture_index < len(available_captures):
                active_capture = available_captures[active_capture_index]
                
                # Get the preview image path
                pcoll = preview_collections.get("main")
                
                if pcoll is not None and active_capture.full_path in pcoll:
                    preview_image = pcoll[active_capture.full_path]
                    
                    # Draw the preview
                    preview_box = captures_box.box()
                    preview_box.label(text="Preview:")
                    preview_box.template_icon(icon_value=preview_image.icon_id, scale=5)
                else:
                    # If preview not loaded yet, show a placeholder
                    preview_box = captures_box.box()
                    preview_box.label(text="Preview: (Generating...)")


def register_previews():
    # Create a new preview collection
    pcoll = previews.new()
    pcoll.my_previews_dir = ""
    preview_collections["main"] = pcoll

def unregister_previews():
    # Unload all preview collections
    for pcoll in preview_collections.values():
        previews.remove(pcoll)
    preview_collections.clear()

def load_thumbnail(capture_path):
    """Load a single thumbnail into the preview collection."""
    pcoll = preview_collections["main"]

    if capture_path and capture_path not in pcoll:
        thumb_path = core_utils.get_thumbnail_preview(capture_path)
        if thumb_path:
            pcoll.load(capture_path, thumb_path, 'IMAGE')

@bpy.app.handlers.persistent
def on_depsgraph_update(scene):
    """Check for active capture and load its thumbnail if needed."""
    if bpy.context.scene and hasattr(bpy.context.scene, "remix_captures"):
        captures = bpy.context.scene.remix_captures
        index = bpy.context.scene.remix_captures_index
        if 0 <= index < len(captures):
            capture = captures[index]
            load_thumbnail(capture.full_path)
