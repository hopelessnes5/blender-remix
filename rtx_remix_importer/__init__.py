bl_info = {
    "name": "Remix Toolkit for Blender",
    "author": "CR",
    "version": (0, 4),
    "blender": (4, 0, 0),
    "description": "An alternative to the official NVIDIA RTX Remix Toolkit. Imports RTX Remix captures and export assets back in a RTX Remix compatible format.",
    "doc_url": "",
    "category": "Import-Export",
}

import bpy
from bpy.app.handlers import persistent
from . import ui
from . import operators
from .core_utils import get_blender_version, is_blender_4_1_or_newer


@persistent
def _cleanup_before_load(_unused):
    from .core_utils import cleanup_addon_resources
    cleanup_addon_resources()

def register():
    # Check Blender version and show compatibility info
    version = get_blender_version()
    print(f"RTX Remix Importer: Running on Blender {version[0]}.{version[1]}.{version[2]}")
    
    if is_blender_4_1_or_newer():
        print("RTX Remix Importer: Blender 4.1+ detected - using compatibility mode for deprecated mesh methods")
    
    operators.register()
    ui.register()
    if _cleanup_before_load not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_cleanup_before_load)

def unregister():
    if _cleanup_before_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_cleanup_before_load)
    from .core_utils import cleanup_addon_resources
    cleanup_addon_resources()
    ui.unregister()
    operators.unregister()
