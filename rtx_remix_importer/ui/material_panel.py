import bpy
from bpy.types import Panel

class PT_RemixMaterialPanel(Panel):
    """Panel showing RTX Remix material information"""
    bl_label = "RTX Remix Materials"
    bl_idname = "PT_remix_material_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "material"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        # Show panel when we have an active object with materials
        return (context.active_object is not None and
                len(context.active_object.material_slots) > 0)

    def draw(self, context):
        layout = self.layout
        obj = context.active_object

        if not obj:
            layout.label(text="No active object")
            return

        if not obj.material_slots:
            layout.label(text="No materials on this object")
            return

        active_material = obj.active_material

        if not active_material:
            layout.label(text="No active material")
            return

        # Material info section
        box = layout.box()
        box.label(text=f"Active Material: {active_material.name}", icon='MATERIAL')

        # Describe the material build-up: standard Principled BSDF (optionally
        # with a transmission layer) or a legacy pre-port Aperture group.
        legacy_group = None
        principled = None

        if active_material.use_nodes:
            for node in active_material.node_tree.nodes:
                if principled is None and node.type == 'BSDF_PRINCIPLED':
                    principled = node
                elif legacy_group is None and node.type == 'GROUP' and node.node_tree and "Aperture" in node.node_tree.name:
                    legacy_group = node

        if principled is not None:
            trans = principled.inputs.get('Transmission Weight') or principled.inputs.get('Transmission')
            is_translucent = bool(trans is not None and not trans.is_linked and trans.default_value > 0.5)
            if is_translucent:
                box.label(text="Type: Principled BSDF (Translucent)", icon='CHECKMARK')
            else:
                box.label(text="Type: Principled BSDF (Opaque)", icon='CHECKMARK')
        elif legacy_group is not None:
            box.label(text=f"Type: Legacy node group '{legacy_group.node_tree.name}'", icon='INFO')
        else:
            box.label(text="Type: Standard Blender Material", icon='DOT')
