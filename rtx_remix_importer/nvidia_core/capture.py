"""Direct port of the capture helpers from the NVIDIA RTX Remix Toolkit.

Source: ``lightspeed.trex.capture.core.shared.lightspeed.trex.capture.core.shared.setup``
in RTX Remix Toolkit 1.6.1.

SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
SPDX-License-Identifier: Apache-2.0

Only the capture validation / metadata / hash-discovery behaviour is ported.
The Kit layer manager, thumbnails, ``omni.client`` asset listing and undo
machinery stay in the Toolkit: Blender never launches them and reads the same
files through ``pxr`` directly.
"""

from __future__ import annotations

import logging

from pxr import Sdf, Tf, Usd, UsdGeom

from . import constants

_LOG = logging.getLogger(__name__)

__all__ = [
    "copy_capture_stage_metadata",
    "get_capture_game_name",
    "get_capture_layer",
    "get_captured_hashes",
    "get_hashes_from_capture_layer",
    "get_layer_metadata",
    "is_capture_file",
    "is_layer_a_capture_file",
    "publish_remix_config",
]


def is_layer_a_capture_file(layer: Sdf.Layer) -> bool:
    """Return whether the layer metadata identifies a Remix capture."""
    if not layer:
        return False
    return layer.customLayerData.get(constants.LAYER_TYPE_KEY) == constants.LAYER_TYPE_CAPTURE


def is_capture_file(path: str) -> bool:
    """Return whether the asset metadata identifies a Remix capture.

    Uses ``Sdf.Layer.OpenAsAnonymous(metadataOnly=True)`` exactly like the
    Toolkit, so a 200 MB capture is classified without loading its prims.
    """
    try:
        layer = Sdf.Layer.OpenAsAnonymous(path, metadataOnly=True)
    except Tf.ErrorException:
        return False
    return is_layer_a_capture_file(layer)


def get_layer_metadata(path: str) -> dict:
    """Return ``customLayerData`` of *path* without opening the whole layer."""
    try:
        layer = Sdf.Layer.OpenAsAnonymous(path, metadataOnly=True)
    except Tf.ErrorException:
        return {}
    return dict(layer.customLayerData)


def get_capture_game_name(layer: Sdf.Layer) -> str:
    """Return the ``lightspeed_game_name`` recorded by the capture."""
    if not layer:
        return ""
    return str(layer.customLayerData.get(constants.LAYER_GAME_NAME_KEY, ""))


def get_capture_layer(stage: Usd.Stage) -> Sdf.Layer | None:
    """Return the capture layer of *stage*, or ``None`` when there is none.

    Kit asks its layer manager; the direct backend inspects the composed layer
    stack (root layer and sublayers, excluding the session layer).
    """
    if stage is None:
        return None
    layer_stack = stage.GetLayerStack(includeSessionLayers=False)
    for layer in layer_stack:
        if is_layer_a_capture_file(layer):
            return layer
    return None


def copy_capture_stage_metadata(stage_source: Usd.Stage, stage_destination: Usd.Stage) -> None:
    """Copy up-axis, meters-per-unit and time codes from capture to project.

    Port of ``Setup.__copy_metadata_from_stage_to_stage``.
    """
    with Usd.EditContext(stage_destination, stage_destination.GetRootLayer()):
        UsdGeom.SetStageUpAxis(stage_destination, UsdGeom.GetStageUpAxis(stage_source))
        UsdGeom.SetStageMetersPerUnit(stage_destination, UsdGeom.GetStageMetersPerUnit(stage_source))
        stage_destination.SetTimeCodesPerSecond(stage_source.GetTimeCodesPerSecond())


def publish_remix_config(stage: Usd.Stage) -> None:
    """Republish a pre-namespace capture's Remix config under the ``remix`` namespace.

    Port of ``Setup._publish_remix_config``.  Hydra gathers only namespaced
    attributes off a render settings prim, so the bare ``remix_config`` that
    pre-namespace captures use never reaches HdRemix and those captures render
    with default ``rtx.sceneScale``.  The fallback is written to the session
    layer, which is never saved, because the capture layer is read-only.
    """
    if stage is None:
        return

    # Drop the previous capture's fallback first. It out-composes every other layer, so
    # leaving it would both mask the incoming capture and read below as a project override.
    session_layer = stage.GetSessionLayer()
    session_prim_spec = session_layer.GetPrimAtPath(constants.CAPTURED_REMIX_SETTINGS)
    if (
        session_prim_spec is not None
        and constants.CAPTURED_REMIX_CONFIG_ATTR in session_prim_spec.properties
    ):
        del session_prim_spec.properties[constants.CAPTURED_REMIX_CONFIG_ATTR]

    prim = stage.GetPrimAtPath(constants.CAPTURED_REMIX_SETTINGS)
    if not prim.IsValid():
        return

    # Whatever still states the namespaced attribute is either the capture itself or a
    # project-wide override in a stronger layer, and both outrank this fallback. The opinion
    # is read off the property stack rather than through HasAuthoredValue(), which reports a
    # value block as absent -- a block states "no config here" and has to be honoured too.
    namespaced_attribute = prim.GetAttribute(constants.CAPTURED_REMIX_CONFIG_ATTR)
    namespaced_specs = namespaced_attribute.GetPropertyStack(Usd.TimeCode.Default()) if namespaced_attribute else []
    if any(spec.HasInfo("default") for spec in namespaced_specs):
        return

    legacy_attribute = prim.GetAttribute(constants.CAPTURED_REMIX_CONFIG_LEGACY_ATTR)
    if not legacy_attribute or not legacy_attribute.HasAuthoredValue():
        return

    # The capture layer is locked, and the config belongs to the capture rather than to the
    # mod, so the fallback goes on the session layer, which is never written to disk.
    with Usd.EditContext(stage, session_layer):
        prim.CreateAttribute(
            constants.CAPTURED_REMIX_CONFIG_ATTR, legacy_attribute.GetTypeName()
        ).Set(legacy_attribute.Get())


def get_hashes_from_capture_layer(layer: Sdf.Layer) -> tuple[dict[str, Sdf.Path], dict[str, set[str]]]:
    """Collect the asset hashes of a capture layer without composing a stage.

    Port of ``Setup.get_hashes_from_capture_layer``.

    Args:
        layer: The layer to traverse.

    Returns:
        ``(hashes, replaced_switch_grouped)`` where *hashes* maps every
        ``mesh_``/``material``/``light_`` hash to its prim path and
        *replaced_switch_grouped* maps a material hash to the mesh hashes that
        bind it.
    """
    result: dict[str, Sdf.Path] = {}
    # for replaced assets, if a prim is a key of this dictionary, we use the list as a value instead.
    # For example, for material, if a material as an override and this materials is assigned to multiple meshes
    # we set all meshes as "replaced".
    result_switch_grouped: dict[str, set[str]] = {}
    for path in (constants.ROOTNODE_LOOKS, constants.ROOTNODE_MESHES, constants.ROOTNODE_LIGHTS):
        root_prim = layer.GetObjectAtPath(path)
        if root_prim is None:
            # Real captures may omit e.g. /RootNode/Looks when nothing was captured.
            _LOG.debug("Capture layer %s has no %s", layer.identifier, path)
            continue
        if path == constants.ROOTNODE_MESHES:
            for child in root_prim.nameChildren:
                mesh_hash = str(child.path)[-16:]
                if constants.MATERIAL_RELATIONSHIP in child.relationships:
                    materials = child.relationships[constants.MATERIAL_RELATIONSHIP].targetPathList.explicitItems
                    # Always take the first material as there should never be more than 1 material here
                    mat_hash = str(materials[0])[-16:]
                    result[mat_hash] = materials[0]
                    result_switch_grouped.setdefault(mat_hash, set()).add(mesh_hash)
                result[mesh_hash] = child.path
        else:
            for child in root_prim.nameChildren:
                result[str(child.path)[-16:]] = child.path
    return result, result_switch_grouped


def get_captured_hashes(
    layer: Sdf.Layer, ignore_capture_check: bool = False
) -> tuple[dict[str, Sdf.Path], dict[str, set[str]]]:
    """Port of ``Setup.get_captured_hashes``."""
    if not ignore_capture_check and not is_layer_a_capture_file(layer):
        return {}, {}
    return get_hashes_from_capture_layer(layer)
