"""User-selectable normal texture encodings for RTX Remix."""

import bpy

DEFAULT_MODE = 'OCT_CONVERT'
MODES = {
    'OCT_CONVERT': (0, 'BC5_UNORM'),
    'OCT_EXISTING': (0, 'BC5_UNORM'),
    'OPENGL': (1, 'BC7_UNORM'),
    'DIRECTX': (2, 'BC7_UNORM'),
}


def current_mode(scene=None):
    scene = scene if scene is not None else bpy.context.scene
    mode = getattr(scene, 'remix_normal_export_encoding', DEFAULT_MODE)
    if mode not in MODES:
        raise ValueError(f'Unknown normal export encoding: {mode}')
    return mode


def usd_encoding(mode):
    return MODES[mode][0]
