> This project uses AI-generated code.

# Remix Toolkit for Blender

A Blender add-on that imports RTX Remix captures and exports meshes, materials and lights back into a Remix mod (`mod.usda`).

Not affiliated with NVIDIA. RTX Remix, the RTX Remix Toolkit and the AperturePBR materials belong to NVIDIA.

- Author / maintainer: [hopelessnes5](https://github.com/hopelessnes5/)
- Repository: [github.com/hopelessnes5/blender-remix](https://github.com/hopelessnes5/blender-remix)
- Version: 0.4

---

## Feature parity with the official RTX Remix Toolkit

The official Toolkit is a Kit-based application (`lightspeed.trex.*` extensions). This add-on re-implements the parts that make sense inside Blender. Below is a per-feature comparison.

**Legend:** ✅ implemented · 🟡 partial / different approach · ⛔ not implemented

### Project and layers

| Feature | Official Toolkit component | Status | Notes in this add-on |
|---|---|:--:|---|
| Create / open a mod project (`mod.usda`) | `lightspeed.trex.project_wizard.core`, `lightspeed.trex.replacement.core.shared` | ✅ | `Load Remix Project`, `Create New Mod File`; project root is derived from `mod.usda` |
| Sub-layer stack: add existing, create new, pick edit target | `lightspeed.trex.layer_manager.core`, stage manager | 🟡 | create + add + set active target layer; no drag-and-drop tree, no reordering |
| Layer mute / lock, reorder by drag and drop | `lightspeed.trex.layer_manager.core` | ⛔ | |
| Autosave, project validation, layer cleanup events | `lightspeed.event.autosave`, `lightspeed.event.validate_project`, `lightspeed.event.layers_cleanup` | ⛔ | |
| Mod packaging into a single `.usd` (+ dependency collection) | `lightspeed.trex.packaging.core`, `lightspeed.trex.mod_packaging_*` | ⛔ | |
| Configurable asset output directories | ingest context | ✅ | `Mesh Directory` (`assets/ingested`), `Texture Directory` (`rtx-remix/textures`) |

### Capture import

| Feature | Official Toolkit component | Status | Notes in this add-on |
|---|---|:--:|---|
| Import a capture file | `lightspeed.trex.capture.core.shared` | ✅ | `Import RTX Remix Capture`; `.usd`, `.usda`, `.usdc` |
| Mass import + capture browser with previews | `lightspeed.trex.home.widget`, project wizard | ✅ | list with checkboxes, `Batch Import All / Selected`, DDS→PNG thumbnails |
| Captured materials → viewport materials | `lightspeed.tool.material.core`, `omni.flux.asset_importer.core` | ✅ | AperturePBR Opaque / Translucent / Model / Normal / SpriteSheet (bundled `.mdl`), Principled BSDF fallback |
| Instance / selection tree editing | `lightspeed.trex.selection_tree.widget` | 🟡 | one Blender object per `inst_<hash>_N`, mesh data shared per prototype; no prototype tree UI |
| Per-instance render state / material metadata | mesh and material panes | 🟡 | reads `metadata:*` and legacy `primvars:_remix_metadata`; binding resolved from the direct `material:binding` relationship only |
| Lights → Blender lights (Sphere / Disk / Cylinder / Rect / Distant) | light replacement | ✅ | intensity, color, radius, shaping cone |
| Cameras → Blender camera + align view | stage camera | ✅ | `Select Imported Camera`, `Align View to Imported Camera` |
| Unit handling on import | ingest `ApplyUnitScale` | 🟡 | `Capture Scale` (default `0.01`), `metersPerUnit` is not read |

### Mesh replacement and asset creation

| Feature | Official Toolkit component | Status | Notes in this add-on |
|---|---|:--:|---|
| Replace a captured mesh: `over "mesh_<HASH>" ( references = None )` + child `def Xform "ref_<uuid>"` with `prepend references` and `custom bool IsRemixRef = 1` | `lightspeed.trex.asset_replacements.core.shared`, `lightspeed.trex.properties_pane.mesh.widget` | ✅ | `Export Selected Meshes/Lights`; holder prim is reused on repeated exports, duplicates and stale overrides are cleaned up |
| Ingested mesh asset layout: `/ReferenceTarget` → `/XForms` → `Mesh`, `MaterialBindingAPI`, `subdivisionScheme`, `extent`, face-varying UVs and normals | `lightspeed.trex.asset_pipeline.core` (`meta` step), IngestCraft | ✅ | same structure the official ingest pipeline writes, including `defaultPrim` |
| Unit normalisation of the asset | ingest `ApplyUnitScale` | 🟡 | geometry is converted with `Export Scale` instead of `metersPerUnit` |
| Add a brand-new instanced mesh | ingest + add reference | ✅ | creates `mesh_<HASH>` + `inst_<HASH>_0` when no target mesh is selected |
| Add an *extra* reference to a mesh prim (combine assets) | mesh pane "add reference" | ⛔ | only single replace |
| Restore / unhide the original captured mesh | remove reference | ⛔ | remove the `ref_` child prim manually |
| Duplicate a replacement (copy USD overrides) | `copy_replacement_overrides_to_path` | ⛔ | |
| Skeleton / skinned mesh replacement + automatic joint remap (`skel:remix_joints`) | `lightspeed.trex.skeleton_replacements.widget`, `.../skeleton.py` | ⛔ | |
| Ingest non-USD sources (FBX, OBJ, glTF, …) via `omni.kit.asset_converter` | `omni.flux.asset_importer.core` | ⛔ | USD in / USD out only |

### Materials

| Feature | Official Toolkit component | Status | Notes in this add-on |
|---|---|:--:|---|
| Author `AperturePBR_Opacity` / `AperturePBR_Translucent` materials (texture slots + constants) | `lightspeed.tool.material.core` | ✅ | diffuse, metallic, roughness, normal, emissive, opacity, specular; transmittance, IOR, thin-walled |
| Replace the material of a captured mesh | `lightspeed.tool.material.core` | 🟡 | exports a new `mat_<uuid>` and binds it with `strongerThanDescendants`; it does not `over` the captured `mat_<HASH>` |
| Correct material binding strength (`bindMaterialAs` as relationship metadata) | USD material binding | ✅ | authored through `MaterialBindingAPI`, not as an attribute |
| Aperture node group inside Blender + Principled ⇄ Aperture conversion | — (Blender-side authoring) | ✅ | `Create Aperture Opaque / Translucent` |
| Copy / transfer material overrides between prims | `transfer_property_spec_to_layer`, `transfer_reference_spec_to_layer` | ⛔ | |
| MDL parameter browser / material presets | material tool | ⛔ | |

### Textures

| Feature | Official Toolkit component | Status | Notes in this add-on |
|---|---|:--:|---|
| Convert textures to DDS (BC7 / BC5 / BC4) on export | ingest `ConvertToDDS` | ✅ | `texconv` wrapper, per-channel formats, background jobs |
| Reuse existing textures by base material name | — | ✅ | `Reuse Existing Textures` |
| Replace a captured texture asset | `lightspeed.trex.texture_replacements.core.shared` | 🟡 | new textures are authored on the exported material; texture paths on captured `mat_<HASH>` prims are not swapped |
| Octahedral normal encoding | ingest `ConvertToOctahedral` | ⛔ | |
| RTXIO texture compression | `lightspeed.trex.rtxio.core` | ⛔ | |
| DDS → PNG previews, broken DDS repair | — (Blender-side) | ✅ | `Convert DDS to PNG`, `Fix Broken Textures`, texture cache cleanup |

### Assets, utilities, integration

| Feature | Official Toolkit component | Status | Notes in this add-on |
|---|---|:--:|---|
| List / inspect the assets a mod replaces | stage manager, `get_replaced_hashes` | ✅ | `Exported Assets` box: scan, filter "not in scene", select, delete |
| Read the mod back into the scene | stage manager reload | 🟡 | `Apply Mod File Changes` (experimental) |
| Force re-export of an asset | — | ✅ | `Invalidate Remix Assets` |
| Undo / redo of USD edits | `lightspeed.trex.commands`, Kit undo stack | ⛔ | Blender operator undo only |
| Properties panes for raw USD attributes | `lightspeed.trex.properties_pane.*` | ⛔ | |
| ComfyUI integration | `lightspeed.trex.comfyui.*` | ⛔ | |
| Particle system authoring (`ParticleSystemAPI`) | particle editors | ⛔ | |
| Stagecraft service / multi-user collaboration | `lightspeed.trex.service.host`, stagecraft | ⛔ | |
| Presets, project settings import/export | project settings | ⛔ | |

### Add-on specific features (no Toolkit equivalent)

| Feature | Where |
|---|---|
| Capture browser with thumbnails directly in Blender | `Captures` panel |
| Aperture node group authoring and Principled BSDF fallback | `RTX Remix Materials` panel, Shader Editor sidebar |
| Background / parallel texture processing with job status UI | `Texture Management` panel |
| Export scale, auto-apply transforms, "inherit parent material" | `Export Settings` |
| Texture cache and duplicate texture cleanup | `Texture Management` panel |

---

## Requirements

- Windows (or Wine on Linux)
- Blender 4.0 or newer (developed and tested on 4.5 LTS)
- [texconv from DirectXTex](https://github.com/microsoft/DirectXTex/releases/latest) — only needed for the source install, it ships inside the release zip

## Installation

### From a release

1. Download the latest release from [github.com/hopelessnes5/blender-remix/releases](https://github.com/hopelessnes5/blender-remix/releases)
2. In Blender: `Edit > Preferences > Add-ons > Install...`, pick `rtx_remix_importer.zip`
3. Enable **Remix Toolkit for Blender**

### From source

1. Clone the repository
2. Put `texconv.exe` into `rtx_remix_importer/texconv/` (create the folder); `texconv.exe` is git-ignored, so it is not part of the clone
3. `Edit > Preferences > Add-ons > Install...` and select the `rtx_remix_importer` folder (or copy it into `scripts/addons/`)
4. Enable **Remix Toolkit for Blender**

If Blender ever refuses to pick up a code change, clear the bytecode cache with `delete-py-cache-and-open-blender.ps1` in the repository root.

---

## Usage

### Import captures

- Open the **RTX Remix** panel in the 3D viewport sidebar (`N` key)
- Set `Captures > Capture Folder` to your game's capture folder
- Import a single capture, or tick several and use `Batch Import Selected`

### Project and sub-layers

- `RTX Remix Project > Remix Mod File` — point at an existing `mod.usda` or create a new one
- `RTX Remix Project > Sublayers` — pick the target sub-layer, or create a new one
- The active target layer receives every export

### Export meshes and lights

1. Select an imported object as `Export Settings > Target Mesh` (the captured mesh whose hash has to be replaced). If you export an object that was imported from a capture, it is used automatically
2. Import or create meshes / lights and place them in the scene
3. Select them and press `Export Selected Meshes/Lights`

What gets written in the target layer:

```usda
over "RootNode"
{
    over "meshes"
    {
        over "mesh_<16HEX>" ( references = None )
        {
            def Xform "ref_<32HEX>" ( prepend references = @./assets/ingested/<mesh>.usda@ )
            {
                custom bool IsRemixRef = 1
                ... identity translate / rotateXYZ / scale ...
            }
        }
    }
}
```

The ingested asset referenced from there is a self-contained mesh asset:

```usda
def Xform "ReferenceTarget" ( kind = "group" )      # defaultPrim
{
    def Xform "XForms" ( kind = "group" )
    {
        def Mesh "<mesh>" ( prepend apiSchemas = ["MaterialBindingAPI"] )
        {
            rel material:binding = </ReferenceTarget/Looks/<mesh>__> (
                bindMaterialAs = "weakerThanDescendants"
            )
            uniform token subdivisionScheme = "none"
        }
    }
    def "Looks" { def Material "<mesh>__" { ... } }
}
```

### Export materials

1. Select the mesh whose material you want to replace and open the Shader Editor
2. Captured materials come in as Aperture node groups; `Principled BSDF` works too
3. Use `Create Aperture Opaque / Translucent` from the Shader Editor sidebar when authoring a material from scratch
4. Press `Export Selected Materials`

### Export settings

| Setting | Default | Meaning |
|---|---|---|
| `Target Mesh` | – | captured `mesh_<HASH>` this export replaces |
| `Export Scale` | `0.01` | game units per Blender unit; `0.01` suits most Remix games |
| `Auto Apply Transforms` | on | bakes rotation/scale into the exported geometry |
| `Hide Target Mesh` | on | writes `references = None` so the captured mesh is replaced instead of overlapped |
| `Inherit Parent Material` | off | keep the captured material instead of exporting a new one |
| `Reuse Existing Textures` | on | reuse DDS files by base material name |
| `Mesh Directory` | `assets/ingested` | where ingested mesh assets are written |
| `Texture Directory` | `rtx-remix/textures` | where exported DDS textures are written |

### Exported assets

`Exported Assets` lists every mesh and light reference the selected layers author, with a select / delete action and a `Show Assets Not in Scene` filter.

---

## Known issues

- Skinned mesh export is not supported yet (no skeleton replacement / joint remapping)
- `Apply Mod File Changes` is experimental and may behave unexpectedly
- Light replacement is not wired to `/RootNode/lights/light_<HASH>` yet — lights exported without a target mesh end up under `/RootNode/remix_assets`
- Albedo textures with an alpha channel may not round-trip correctly
- Some `.dds` textures appear purple; use `Captures > Fix Broken Textures`
- The `Aperture Opaque` node group is incomplete (animation, iridescence, flags, …)
- Non-USD source assets (FBX/OBJ/glTF) are not ingested
- Only one reference per mesh prim can be authored; combining assets into one mesh is not supported

## Troubleshooting

| Symptom | Fix |
|---|---|
| `NameError` / add-on fails to enable after updating | Clear the bytecode cache: `delete-py-cache-and-open-blender.ps1`, restart Blender |
| Textures are not converted to DDS | `texconv.exe` is missing in `rtx_remix_importer/texconv/` |
| Purple textures in the viewport | `Captures > Fix Broken Textures` |
| Export says "No Target Mesh selected" | Select the captured mesh in `Export Settings > Target Mesh` |

## Credits

- Uncle Burrito (RTX Remix Showcase Discord) — the Aperture Opaque node group
- Based on the original *Remix Toolkit for Blender* by sambow23; this fork is maintained by [hopelessnes5](https://github.com/hopelessnes5/)

Issues and pull requests: [github.com/hopelessnes5/blender-remix/issues](https://github.com/hopelessnes5/blender-remix/issues)
