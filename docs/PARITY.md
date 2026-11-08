# Feature parity and backend details

Status snapshot: 1 November 2026. [Back to README](../README.md) · [Русский](PARITY.ru.md).

The README badges and progress map are generated from these tables by `python tools/generate_progress.py`.

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
| Mass import + capture browser with previews | `lightspeed.trex.home.widget`, project wizard | ✅ | list with checkboxes, `Batch Import All / Selected`, DDS thumbnails read directly into memory; no converted-file cache |
| Captured materials → viewport materials | `lightspeed.tool.material.core`, `omni.flux.asset_importer.core` | ✅ | imported materials build as standard Principled BSDF (transmission layer for AperturePBR_Translucent values); export still authors the bundled `AperturePBR*.mdl` shaders into USD |
| Instance / selection tree editing | `lightspeed.trex.selection_tree.widget` | 🟡 | one Blender object per `inst_<hash>_N`, mesh data shared per prototype; no prototype tree UI |
| Per-instance render state / material metadata | mesh and material panes | 🟡 | reads `metadata:*` and legacy `primvars:_remix_metadata`; binding resolution uses the direct relationship, composed bindings and prototype/child fallbacks |
| Lights → Blender lights (Sphere / Disk / Cylinder / Rect / Distant) | light replacement | ✅ | intensity, color, radius, shaping cone |
| Cameras → Blender camera + align view | stage camera | ✅ | `Select Imported Camera`, `Align View to Imported Camera` |
| Unit handling on import | ingest `ApplyUnitScale` | 🟡 | direct backend reads the capture's `metersPerUnit`; the legacy backend still uses only `Capture Scale` |

### Mesh replacement and asset creation

| Feature | Official Toolkit component | Status | Notes in this add-on |
|---|---|:--:|---|
| Replace a captured mesh: `over "mesh_<HASH>" ( references = None )` + child `def Xform "ref_<uuid>"` with `prepend references` and `custom bool IsRemixRef = 1` | `lightspeed.trex.asset_replacements.core.shared`, `lightspeed.trex.properties_pane.mesh.widget` | ✅ | `Export Selected Meshes/Lights`; holder prim is reused on repeated exports, duplicates and stale overrides are cleaned up |
| Hide a captured mesh without exporting geometry | `Setup.remove_reference` (cross-layer branch) | 🟡 | `Hide Selected Meshes` writes `references = None` on `mesh_<HASH>` in the mod layer; instances and capture layers are not modified. Game runtime behavior remains unverified |
| Ingested mesh asset layout: `/ReferenceTarget` → `/XForms` → `Mesh`, `MaterialBindingAPI`, `subdivisionScheme`, `extent`, face-varying UVs and normals | `lightspeed.trex.asset_pipeline.core` (`meta` step), IngestCraft | ✅ | same structure the official ingest pipeline writes, including `defaultPrim`; the direct import composes it back — the nested `ref_`/`XForms` Mesh is found several levels below the prototype and placed on the full transform chain (instance placement keeps USD reference semantics) |
| Unit normalisation of the asset | ingest `ApplyUnitScale` | 🟡 | geometry is converted with `Export Scale` instead of `metersPerUnit` |
| Add a brand-new instanced mesh | ingest + add reference | ✅ | creates `mesh_<HASH>` + `inst_<HASH>_0` when no target mesh is selected |
| Add an *extra* reference to a mesh prim (combine assets) | mesh pane "add reference" | ⛔ | only single replace |
| Restore / unhide the original captured mesh | remove reference | ✅ | `Restore Hidden Meshes`; removes the `references = None` mask from the mod layer, no geometry is exported |
| Duplicate a replacement (copy USD overrides) | `Setup.copy_replacement_overrides_to_path` | 🟡 | ported to `nvidia_core/copier.py`: attribute overrides via `Sdf.CopySpec`, relationship overrides with internal-target remapping, sublayer recursion; no Blender duplicate operator wired yet |
| Skeleton / skinned mesh replacement + automatic joint remap (`skel:remix_joints`) | `lightspeed.trex.skeleton_replacements.widget`, `.../skeleton.py` | ⛔ | |
| Ingest non-USD sources (FBX, OBJ, glTF, …) via `omni.kit.asset_converter` | `omni.flux.asset_importer.core` | ⛔ | USD in / USD out only |

### Materials

| Feature | Official Toolkit component | Status | Notes in this add-on |
|---|---|:--:|---|
| Author `AperturePBR_Opacity` / `AperturePBR_Translucent` materials (texture slots + constants) | `lightspeed.tool.material.core` | ✅ | diffuse, metallic, roughness, normal, emissive, opacity, specular; transmittance, IOR, thin-walled. Principled BSDF inputs plus an active Material Output → Displacement → image chain; opaque height maps export as BC4 with displace_in/out |
| Replace the material of a captured mesh | `lightspeed.tool.material.core` | 🟡 | authors an `over` on the original `mat_<HASH>` and its shader; a capture-declared MDL family switch (opaque ↔ translucent) rebinds the ident trio and masks the dead family's inputs; all instances using that material are affected; the composed game runtime is not verified |
| Correct material binding strength (`bindMaterialAs` as relationship metadata) | USD material binding | ✅ | authored through `MaterialBindingAPI`, not as an attribute |
| Blender-side Aperture node groups (`Aperture Opaque` / `Aperture Translucent`) + Principled ⇄ Aperture conversion operators | — (Blender-side authoring) | ⛔ | deliberately removed: materials are authored natively as standard Principled BSDF; node groups written by old releases remain readable for re-export |
| Copy / transfer material overrides between prims | `transfer_property_spec_to_layer`, `transfer_reference_spec_to_layer` | ⛔ | |
| MDL parameter browser / material presets | material tool | ⛔ | |

### Textures

| Feature | Official Toolkit component | Status | Notes in this add-on |
|---|---|:--:|---|
| Convert textures to DDS (BC7 / BC5 / BC4) on export | ingest `ConvertToDDS` | ✅ | `texconv` wrapper, per-channel formats, background jobs |
| Reuse existing textures | — | 🟡 | Existing non-normal DDS files with the exact image name are reused; normals are reconverted |
| Replace a captured texture asset | `lightspeed.trex.texture_replacements.core.shared` | 🟡 | new textures are authored on the exported material; texture paths on captured `mat_<HASH>` prims are not swapped |
| Octahedral normal encoding | ingest `ConvertToOctahedral` | 🟡 | NVIDIA hemispherical conversion for Blender OpenGL normals on material export; BC5 DDS and `inputs:encoding = 0` |
| RTXIO texture compression | `lightspeed.trex.rtxio.core` | ⛔ | |
| DDS → PNG texture conversion, broken DDS repair | — (Blender-side) | ✅ | `Convert DDS to PNG`, `Fix Broken Textures`, texture cache cleanup |

### Assets, utilities, integration

| Feature | Official Toolkit component | Status | Notes in this add-on |
|---|---|:--:|---|
| List / inspect the assets a mod replaces | stage manager, `get_replaced_hashes` | ✅ | `Exported Assets` box: scan, filter "not in scene", select, delete |
| Read the mod back into the scene | stage manager reload | 🟡 | `Apply Mod File Changes` (experimental): follows the Toolkit mod model — scene additions are limited by the 16-character hash relation with the current scene and capture; replacements swap prototype content into the existing hash objects in place (empty resets hide them); `over` records and material-binding scan hits touch materials only, never transforms; Toolkit color temperature uses Blender's native light temperature setting; new placements, lights and standalone meshes go to a dedicated `mod <name>` collection |
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
| `lightspeed.trex.asset_replacements.core.shared.Setup.copy_replacement_overrides_to_path` | `nvidia_core/copier.py` | Descendant override copying ported: prim specs under the source path across the replacement layer and its sublayers, attribute specs through `Sdf.CopySpec`, relationships (e.g. `material:binding`) with internal-target remapping. `SetRelationshipTargetsCommand` became `Usd.Relationship.SetTargets`; capture-typed or locked source layers and capture edit targets are rejected. No Blender duplicate UI yet. |
| `lightspeed.layer_manager.core` layer ordering | `nvidia_core/composition.py` | Reimplemented with `pxr`: capture is always the weakest sublayer, the mod/replacement layer is the edit target, capture is locked read-only, and the workfile's `omni_layer.muteness` / `omni_layer.locked` metadata is applied on open. Every save re-checks the stack's capture layers and the edit target before writing. The full layer model, mute/lock/reorder UI and events are not ported. |
| `omni.usd.get_composed_references_from_prim`, `omni.usd.get_introducing_layer` | `compatibility/kit_adapters.py` | Reimplemented with `Sdf` / `Usd.PrimCompositionQuery`, including authoring-layer resolution and relative asset paths. |
| `omni.flux.utils.common.lights.get_light_type` | `compatibility/kit_adapters.py` | Light type enum and the historical `DomeLight_1` alias ported. |
| Toolkit mesh/prim conventions | `nvidia_core/geometry.py` | Blender extraction written against the same `mesh_<HASH>` / `inst_<HASH>_<N>` layout. N-gons are preserved, left-handed winding is reversed, and per-corner UV/normal arrays stay aligned with `faceVertexIndices`. |

**Not ported (P1+):** the other asset-replacement transfer commands
(`transfer_property_spec_to_layer` / `transfer_prim_definition_spec_to_layer` /
`transfer_reference_spec_to_layer`), ingest/packaging pipelines,
skeleton replacement, material/texture override transfer, ComfyUI integration,
StageCraft, Kit undo, and the properties panes. Material *conversion* remains the
add-on's existing `material_utils.py` code, not an NVIDIA port; it now builds
standard Principled BSDF node graphs instead of the custom Aperture node groups.

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
`tests/unit/test_copier.py` reproduces the Toolkit's own unit tests for
`copy_replacement_overrides_to_path`: it copies attribute and relationship
overrides from a source prim to its duplicate across the replacement layer and
its sublayers, remaps targets that live inside the source hierarchy, skips the
root prim's own override spec and invalid destinations, honours the "no
replacement layer" early return, and rejects capture-typed layers both as
override source and as edit target. `tests/unit/test_hide.py` and
`TestHideMeshOperators` cover hiding/restoring captured meshes: the mask is
authored on the mesh prototype, the capture file hash is unchanged and no
`assets/` output appears. Tests exercise multiple instances and restoration;
standard USD can still compose instance geometry from capture-layer references. `tests/blender/test_principled_materials.py` checks
Principled constants, opacity gating, transmission type detection and selected
USD export values on real Blender nodes. `tests/unit/test_material_defaults.py` checks omission of MDL defaults and preservation of stronger opinions over weaker layers. `tests/blender/test_material_weak_composition.py`
checks the replacement write against a composed capture: MDL defaults are omitted only
when the weaker layers agree; explicit values mask disagreeing capture values; an
undeclared family is not invented; weak-only inputs the user turned off are masked
(empty asset paths, `False` flags) and a declared family switch rebinds the
`info:mdl` ident trio while masking the dead family's inputs.
`tests/blender/test_nested_prototype_roundtrip.py` makes the add-on's own
mod re-import with its geometry: a deeply nested ingested-asset Mesh
(`mesh_<HASH>/ref_x/XForms/<name>`) is found under the prototype, a
standalone prototype is placed on the full container chain, an instance is
placed on its mapped subtree and keeps USD reference semantics (the
prototype container's transform is not folded into the instance placement),
and the plain capture layout keeps importing as before.
`tests/blender/test_light_camera_roundtrip.py` pins the light/camera unit
and basis conversions, including parent chains: a rigid parent chain
(translate, rotate, uniform scale) roundtrips through the light export,
a sheared parent chain refuses the light export with an explicit error
instead of silently misplacing it, sheared USD chains import with a
warning (exact position, approximated orientation), a parented camera
keeps its composed world matrix, and the CylinderLight/ELLIPSE to
Rectangle/DiskLight approximations are stated instead of assumed.
`tests/blender/test_capture_thumbnails.py`
checks direct DDS thumbnail lookup, decoding, refresh and missing files without
a converted-file cache. `tests/blender/test_write_guard.py` checks the shared
write guard behind every layer-save entry point: capture and locked layers and
capture edit targets are refused immediately before each write, a globally
unlocked capture blocks saving the composition, and the export stage path
reports rejection without touching bytes. `tests/blender/test_texture_source_guard.py`
checks the texture source guard: height and normal exports refuse hard-link
and symlink aliases and the recorded pre-decode source path before texconv
availability is even checked, and a distinct target still exports.

**Executed result:** 330 tests pass on both Blender 4.5.5 LTS (bundled OpenUSD
25.02) and Blender 5.1.2 (bundled OpenUSD 25.08) on Windows, exit code `0`.
`register()` / `unregister()` are exercised by the suite through the
UI-operator tests. The package-install smoke check — a freshly built
`rtx_remix_importer.zip` installed through `preferences.addon_install` into a
clean profile on both versions, importing the fixture (763 vertices, 1
material, `direct` backend) — was verified on the earlier, smaller suite and
has not been repeated since. `git diff --check` and `python -m compileall` also
pass.

**Not verified:**

- Height export supports an image connected to an Object Space Displacement node
  on the active output, constant midlevel/scale and optional Value nodes. Negative
  scales, arbitrary processing and translucent displacement are unsupported.
  Blender uses object-space distance while Remix uses UV-relative depth, so the
  mapped values do not guarantee the same visual depth. Real game rendering and
  Toolkit MAX mip filtering (texconv uses its default mip filter) were not tested.
  Import reconstructs the height node: Scale = displace_in + displace_out,
  Midlevel = displace_in / Scale (0.5 when Scale is zero).

- Mesh-prototype masking was checked against a real capture using an anonymous
  mod layer, but the result has not been tested in the RTX Remix game runtime.
  Its instances may still show geometry in standard USD composition.

- Octahedral normal export was tested with synthetic Blender images and texconv;
  appearance in a real Remix game and non-OpenGL input conventions were not tested.

- Capture thumbnails support RGBA16F, RGBA8/BGRA8 and BC1–BC3 DDS;
  other DDS formats (including BC7) are not decoded. A real game capture thumbnail
  was decoded in background Blender; interactive thumbnail display was not tested.

- No real game capture with textures was available; only the Toolkit fixture
  (two DDS files) was exercised. Blender 4.0–4.4 and Linux/Wine were not run.
- Imported materials are authored as standard Principled BSDF node graphs (the
  custom Aperture node groups were removed). Only the opaque AperturePBR path
  is exercised by the fixture; the transmission mapping for
  `AperturePBR_Translucent` is verified on unit level only.
- `omni_layer` muteness/locking is exercised on the Toolkit fixture. Projects that
  mute the capture or the mod authoring layer are not covered: those layers are
  protected and deliberately never muted, while the reference Toolkit may expose
  a different UI policy for them.
- Light import delegates to the existing `light_utils.py`, which still warns
  about Y-up rotation.
- Non-ASCII Windows paths and Linux/Wine were not tested.

<!-- BEGIN 2026-11-06 MOD-LOADING WAVE -->
## 6 November 2026: Apply Mod overhaul

Full suite — 375 tests, 375 passed in Blender 4.5.5 LTS and 5.1.2. Highlights: hash-scoped selective Apply, shared `Light`/`Mesh` datablocks for identical prototype lights with per-instance placement, normal graph (octahedral/DirectX → Separate (R,G) + Combine (B=1) → Normal Map), Glass BSDF for genuine translucent materials, shader family classifier (`material_family.py`), UV/visibility protection on light-only prototype resets, a standalone SUN for `sky_<HASH>` meshes without capture instances, and Blender-native light temperature. Current status and remaining limits: [AUDIT_CURRENT.ru.md](AUDIT_CURRENT.ru.md). Game RTX Remix runtime remains unverified.
<!-- END 2026-11-06 MOD-LOADING WAVE -->
