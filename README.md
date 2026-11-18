> This project uses AI-generated code.

**English** · [Русский](docs/README.ru.md)

# Remix Toolkit for Blender

Import RTX Remix captures and export replacement meshes, materials and lights into a Remix mod (`mod.usda`) from Blender. No RTX Remix Toolkit, Omniverse Kit or background service.

[Releases](https://github.com/hopelessnes5/blender-remix/releases) · [Feature parity](docs/PARITY.md) · [Issues](https://github.com/hopelessnes5/blender-remix/issues) · [How the plugin works](docs/PLUGIN_PIPELINE.ru.md)

Not affiliated with NVIDIA. RTX Remix, the RTX Remix Toolkit and AperturePBR materials belong to NVIDIA.

## Install

Requires Blender 4.0+ on Windows (or Wine on Linux). The release zip bundles `texconv`.

1. Download the [latest release](https://github.com/hopelessnes5/blender-remix/releases).
2. In Blender: `Edit > Preferences > Add-ons > Install...` and pick `rtx_remix_importer.zip`.
3. Enable **Remix Toolkit for Blender**.

For a source install, put `texconv.exe` into `rtx_remix_importer/texconv/` and install the `rtx_remix_importer` folder. If Blender ignores code changes, delete the `__pycache__` folders and restart.

## Usage

1. Open the **RTX Remix** panel in the 3D Viewport sidebar (`N`) and import a capture.
2. Set the mod file (`mod.usda`) and choose the target sublayer.
3. Place replacements and press `Export Selected Meshes/Lights` or `Export Selected Materials`.
4. Use `Hide Selected Meshes` / `Restore Hidden Meshes` to mask captured geometry without exporting it.

`Export Settings` controls the target mesh, scale, material inheritance, hide-original mode, normal encoding and output folders. Every write goes to the active target layer.

## Limits

- Skinned meshes and non-USD sources (FBX/OBJ/glTF) are not supported.
- `Apply Mod File Changes` is experimental.
- A mesh with several materials is exported as separate USD meshes.

## Credits

Based on the original *Remix Toolkit for Blender* by sambow23; maintained by [hopelessnes5](https://github.com/hopelessnes5/). Ported NVIDIA code keeps its Apache-2.0 attribution headers.
