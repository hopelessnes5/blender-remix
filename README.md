> This project uses AI-generated code.

**English** · [Русский](docs/README.ru.md)
# Remix Toolkit for Blender

A Blender add-on that imports RTX Remix captures and exports meshes, materials and lights back into a Remix mod (`mod.usda`).

Not affiliated with NVIDIA. RTX Remix, the RTX Remix Toolkit and the AperturePBR materials belong to NVIDIA.

- Author / maintainer: [hopelessnes5](https://github.com/hopelessnes5/)
- Repository: [github.com/hopelessnes5/blender-remix](https://github.com/hopelessnes5/blender-remix)
- Version: 0.4
- Progress dashboard (HTML): [progress.html](progress.html) (open in a browser)

---

## Feature parity with the official RTX Remix Toolkit

The official Toolkit is a Kit-based application (`lightspeed.trex.*` extensions). This add-on re-implements the parts that make sense inside Blender. Below is a per-feature comparison.

**Legend:** ✅ implemented · 🟡 partial / different approach · ⛔ not implemented

### Project and layers

| Feature | Official Toolkit component | Status | Notes in this add-on |
|---|---|:--:|---|
| Create / open a mod project (`mod.usda`) | `lightspeed.trex.project_wizard.core`, `lightspeed.trex.replacement.core.shared` | ✅ | `Load Remix Project`, `Create New Mod File`; project root is derived from `mod.usda` |
| Sub-layer stack: add existing, create new, pick edit target | `lightspeed.trex.layer_manager.core`, stage manager | 🟡 | create + add + set active target layer; no drag-and-drop tree, no reordering |
| Layer mute / lock, reorder by drag and drop | `lightspeed.trex.layer_manager.core` | 🟡 | `omni_layer` `muteness` / `locked` metadata is applied when a project opens; no mute/lock toggles and no drag-and-drop reorder UI |
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
| Per-instance render state / material metadata | mesh and material panes | 🟡 | reads `metadata:*` and legacy `primvars:_remix_metadata`; binding resolution uses the direct relationship, composed bindings and prototype/child fallbacks |
| Lights → Blender lights (Sphere / Disk / Cylinder / Rect / Distant) | light replacement | ✅ | intensity, color, radius, shaping cone |
| Cameras → Blender camera + align view | stage camera | ✅ | `Select Imported Camera`, `Align View to Imported Camera` |
| Unit handling on import | ingest `ApplyUnitScale` | 🟡 | direct backend reads the capture's `metersPerUnit`; the legacy backend still uses only `Capture Scale` |

### Mesh replacement and asset creation

| Feature | Official Toolkit component | Status | Notes in this add-on |
|---|---|:--:|---|
| Replace a captured mesh: `over "mesh_<HASH>" ( references = None )` + child `def Xform "ref_<uuid>"` with `prepend references` and `custom bool IsRemixRef = 1` | `lightspeed.trex.asset_replacements.core.shared`, `lightspeed.trex.properties_pane.mesh.widget` | ✅ | `Export Selected Meshes/Lights`; holder prim is reused on repeated exports, duplicates and stale overrides are cleaned up |
| Hide a captured mesh without exporting geometry | `Setup.remove_reference` (cross-layer branch) | ✅ | `Hide Selected Meshes` writes `references = None` into the mod layer on the reference-owning prim (`inst_<HASH>_<N>` for inline captures); capture layers are never touched |
| Ingested mesh asset layout: `/ReferenceTarget` → `/XForms` → `Mesh`, `MaterialBindingAPI`, `subdivisionScheme`, `extent`, face-varying UVs and normals | `lightspeed.trex.asset_pipeline.core` (`meta` step), IngestCraft | ✅ | same structure the official ingest pipeline writes, including `defaultPrim` |
| Unit normalisation of the asset | ingest `ApplyUnitScale` | 🟡 | geometry is converted with `Export Scale` instead of `metersPerUnit` |
| Add a brand-new instanced mesh | ingest + add reference | ✅ | creates `mesh_<HASH>` + `inst_<HASH>_0` when no target mesh is selected |
| Add an *extra* reference to a mesh prim (combine assets) | mesh pane "add reference" | ⛔ | only single replace |
| Restore / unhide the original captured mesh | remove reference | ✅ | `Restore Hidden Meshes`; removes the `references = None` mask from the mod layer, no geometry is exported |
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

## Direct NVIDIA backend (P0)

The importer now has two backends. The default **direct** backend runs NVIDIA's
capture algorithms inside Blender using the `pxr` (OpenUSD) that ships with
Blender. It does not start RTX Remix Toolkit, Omniverse Kit, StageCraft, an MCP
server or any HTTP/REST service, and it writes no intermediate files.

The old importer is preserved verbatim as `rtx_remix_importer/legacy_import_core.py`
and remains the rollback path.

### Architecture

| Path | Responsibility |
|---|---|
| `rtx_remix_importer/nvidia_core/` | Pure `pxr` port: constants, prim classification, capture validation/hashes, layer composition, mesh extraction. Imports neither `omni`, `carb` nor `bpy`. |
| `rtx_remix_importer/compatibility/kit_adapters.py` | Narrow replacements for the Omniverse helpers (`omni.usd`, `omni.flux`) used by the ported code. |
| `rtx_remix_importer/blender_bridge/` | Creates Blender meshes, resolves/applies materials and builds the scene (`scene_builder.import_with_direct_backend`). |
| `rtx_remix_importer/import_core.py` | Facade with backend selection and logged fallback. Keeps the legacy signature and return value. |
| `rtx_remix_importer/legacy_import_core.py` | The previous importer, unchanged and used for rollback. |

### NVIDIA code reuse matrix

Source paths refer to RTX Remix Toolkit 1.6.1 (`source/extensions/...`, Apache-2.0).

| NVIDIA source | Ported to | Reuse |
|---|---|---|
| `lightspeed.common.lightspeed.common.constants` | `nvidia_core/constants.py` | Values and regexes copied; the `omni.ui` import that made the module unusable outside Kit was removed. |
| `lightspeed.trex.utils.common.prim_utils` | `nvidia_core/prim_utils.py` | Algorithms ported (classification, prototype lookup, reference discovery, transferable specs, ghost detection). The implicit `omni.usd` context and selection were replaced by explicit `stage` / `selection` arguments. |
| `lightspeed.trex.capture.core.shared.setup` | `nvidia_core/capture.py` | Capture detection, layer metadata, hash discovery, stage-metadata copy and `remix_config` publishing ported. Kit layer manager, thumbnails, `omni.client` listing and undo are not ported. |
| `lightspeed.layer_manager.core` layer ordering | `nvidia_core/composition.py` | Reimplemented with `pxr`: capture is always the weakest sublayer, the mod/replacement layer is the edit target, capture is locked read-only, and the workfile's `omni_layer.muteness` / `omni_layer.locked` metadata is applied on open. The full layer model, mute/lock/reorder UI and events are not ported. |
| `omni.usd.get_composed_references_from_prim`, `omni.usd.get_introducing_layer` | `compatibility/kit_adapters.py` | Reimplemented with `Sdf` / `Usd.PrimCompositionQuery`, including authoring-layer resolution and relative asset paths. |
| `omni.flux.utils.common.lights.get_light_type` | `compatibility/kit_adapters.py` | Light type enum and the historical `DomeLight_1` alias ported. |
| Toolkit mesh/prim conventions | `nvidia_core/geometry.py` | Blender extraction written against the same `mesh_<HASH>` / `inst_<HASH>_<N>` layout. N-gons are preserved, left-handed winding is reversed, and per-corner UV/normal arrays stay aligned with `faceVertexIndices`. |

**Not ported (P1+):** asset replacement transfer, ingest/packaging pipelines,
skeleton replacement, material/texture override transfer, ComfyUI integration,
StageCraft, Kit undo, and the properties panes. Material *conversion* remains the
add-on's existing `material_utils.py` code, not an NVIDIA port.

### Backend switch and rollback

Resolution order:

1. `REMIX_IMPORT_BACKEND` environment variable.
2. `scene["remix_import_backend"]` custom property.
3. `direct` (default).

| Value | Behaviour |
|---|---|
| `direct` (default) | NVIDIA algorithms in process; on an unexpected error the import is logged and retried with the legacy importer. |
| `direct-only` | Strict direct backend; errors are raised and never fall back. |
| `legacy` | Old importer only — use this to roll back instantly. |

```powershell
# strict direct backend
$env:REMIX_IMPORT_BACKEND = "direct-only"

# rollback to the previous importer
$env:REMIX_IMPORT_BACKEND = "legacy"
```

To roll back permanently, set the same value in the scene property or remove the
`blender_bridge` / `nvidia_core` / `compatibility` packages and restore
`import_core.py` from `legacy_import_core.py`. All changes are additive apart from
the `import_core.py` rename, so `git revert` of the integration commit is enough.

### Import semantics

- The capture layer is locked in memory. Only the mod/replacement layer is an
  edit target; `ProjectComposition.add_capture_sublayer()` appends a capture as
  the **weakest** sublayer after removing any previous capture.
- With the UI default `Capture Scale = 0.01`, the direct backend uses the
  capture's own `metersPerUnit` and `upAxis` (captures commonly author `0.01`
  and Y-up). Any other UI value is treated as an explicit override.
- One Blender mesh datablock is shared by every instance of a prototype.
  Materials are linked per object (`slot.link = 'OBJECT'`) so instances can
  differ without duplicating geometry.
- Capture materials are resolved through the direct `material:binding`, composed
  bindings, then prototype and mesh-child fallbacks. Invalid targets (for
  example a stale `/RootNode/Looks/...` path) are rejected.

### Tests

Tests live in `tests/` and must run inside Blender because the add-on package
imports `bpy`:

```powershell
& "C:\Blender 4.5 LTS\blender.exe" `
  --background --factory-startup --python tests/run_tests.py
```

The fixtures in `tests/data/full_project/` are copied from the Toolkit's
`lightspeed.layer_manager.core` test data (Apache-2.0). The suite covers the
Kit adapters, prim utilities, capture helpers, composition/locking, geometry
(n-gons, left-handed winding, primvar indices), capture immutability, backend
selection/fallback and real Blender imports of both `capture.usda` and
`full_project.usda`; composition tests also verify that `omni_layer` muteness hides muted sublayers, that metadata-locked sublayers become read-only, that saving a project writes only the mod layer, and that authoring into the locked capture raises. `tests/blender/test_ui_operators.py` drives the actual UI
operators (`remix.import_capture`, `remix.scan_capture_folder`) end to end,
including the scene-property backend switch and the `direct-only` error path.
`tests/unit/test_hide.py` and `TestHideMeshOperators` cover hiding/restoring
captured meshes: the mask is authored on the reference-owning instance prim, the
capture file hash is unchanged, no `assets/` output appears, and the composed
geometry disappears/returns.

**Executed result:** 89 tests pass on both Blender 4.5.5 LTS (bundled OpenUSD
25.02) and Blender 5.1.2 (bundled OpenUSD 25.08) on Windows, exit code `0`;
`register()` / `unregister()` was smoke-tested on both as well. A freshly built
`rtx_remix_importer.zip` was installed through `preferences.addon_install` into a
clean profile on both versions and imported the fixture (763 vertices, 1
material, `direct` backend). `git diff --check` and `python -m compileall` also
pass.

**Not verified:**

- No real game capture with textures was available; only the Toolkit fixture
  (two DDS files) was exercised. Blender 4.0–4.4 and Linux/Wine were not run.
- In this environment the `Aperture Opaque` node group could not be appended, so
  material conversion exercised the existing Principled BSDF fallback.
- `omni_layer` muteness/locking is exercised on the Toolkit fixture. Projects that
  mute the capture or the mod authoring layer are not covered: those layers are
  protected and deliberately never muted, while the reference Toolkit may expose
  a different UI policy for them.
- Light import delegates to the existing `light_utils.py`, which still warns
  about Y-up rotation.
- Non-ASCII Windows paths and Linux/Wine were not tested.

---

## Requirements

- Windows (or Wine on Linux)
- Blender 4.0 or newer (verified in background mode on 4.5.5 LTS and 5.1.2)
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

### Hide / restore captured meshes (no export)

1. Select one or more imported capture meshes
2. Press `Hide Selected Meshes` — the add-on only writes into the mod layer:

```usda
over "RootNode"
{
    over "instances"
    {
        over "inst_<16HEX>_<N>" ( references = None )
        {
        }
    }
}
```

3. Press `Restore Hidden Meshes` with the same selection to remove the mask

No mesh, material or texture is exported. The capture layer is opened read-only
and is never modified; hiding is refused if the prim already owns a replacement
reference. For inline captures the `mesh_<HASH>` prototype holds the geometry and
the `inst_<HASH>_<N>` prim owns the `prepend references` that makes it visible,
which is why the instance carries the mask. A `mesh_<HASH>` path is still
accepted for captures whose prototype references external geometry.

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
