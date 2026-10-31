"""Stage composition for the direct RTX Remix backend.

The Toolkit composes a project from a workfile layer plus sublayers: the mod
(``replacement``) layer is stronger than the capture layer, and the capture
layer is always the weakest sublayer.  That ordering is what makes replacements
win while captures stay untouched.  This module reproduces it with ``pxr`` only.

SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
SPDX-License-Identifier: Apache-2.0

(The Toolkit's layer manager is Kit-specific.  This is an independent ``pxr``
implementation of the same layer ordering rules, ported for Blender.)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pxr import Sdf, Tf, Usd, UsdGeom

from . import capture, constants

_LOG = logging.getLogger(__name__)

__all__ = [
    "ProjectComposition",
    "apply_omni_layer_state",
    "find_layer_of_type",
    "find_mod_layer",
    "open_stage",
]


def apply_omni_layer_state(
    stage: Usd.Stage,
    *,
    protected_layers=(),
) -> tuple[list[str], list[Sdf.Layer]]:
    """Apply the Toolkit ``omni_layer`` lock/mute metadata to *stage*.

    The official layer manager stores per-project layer state in the workfile's
    ``customLayerData``::

        dictionary omni_layer = {
            dictionary locked = { bool "./capture.usda" = 1 }
            dictionary muteness = { bool "./baker.usda" = 1 }
        }

    ``pxr`` exposes muteness as a runtime stage feature
    (``MuteAndUnmuteLayers``), so it has to be applied explicitly after the stage
    is opened; locking is applied through the layer edit permission, exactly like
    the capture lock.

    Args:
        stage: Stage whose root layer carries ``omni_layer`` metadata.
        protected_layers: Layers that must never be muted (typically the mod
            authoring layer and the capture layer).

    Returns:
        ``(muted_layer_ids, locked_layers)`` that were applied.  Muting
        invalidates the Python handles of the muted layers in some OpenUSD
        builds, so identifiers are returned instead.
    """
    if stage is None:
        return [], []
    root_layer = stage.GetRootLayer()
    metadata = root_layer.customLayerData.get(constants.OMNI_LAYER_KEY) or {}
    protected = {id(layer) for layer in protected_layers if layer is not None}

    stack_layers = list(stage.GetLayerStack(includeSessionLayers=False))
    by_path: dict[str, Sdf.Layer] = {}
    for layer in stack_layers:
        for identifier in (layer.identifier, layer.realPath):
            if identifier:
                by_path[_norm_layer_path(identifier)] = layer

    locked: list[Sdf.Layer] = []
    for relative_path, enabled in (metadata.get("locked") or {}).items():
        if not enabled:
            continue
        layer = by_path.get(_norm_layer_path(root_layer.ComputeAbsolutePath(relative_path)))
        if layer is None:
            continue
        if _lock_layer(layer):
            locked.append(layer)

    muted_layers: list[Sdf.Layer] = []
    for relative_path, enabled in (metadata.get("muteness") or {}).items():
        if not enabled:
            continue
        layer = by_path.get(_norm_layer_path(root_layer.ComputeAbsolutePath(relative_path)))
        if layer is None or id(layer) in protected:
            continue
        muted_layers.append(layer)

    muted_ids: list[str] = []
    if muted_layers:
        muted_ids = [layer.identifier for layer in muted_layers]
        stage.MuteAndUnmuteLayers(muted_ids, [])
        muted_ids = [identifier for identifier in muted_ids if stage.IsLayerMuted(identifier)]

    return muted_ids, locked



def find_layer_of_type(stage: Usd.Stage, *layer_types: str) -> Sdf.Layer | None:
    """Return the first layer of the given ``lightspeed_layer_type`` in the stack."""
    if stage is None:
        return None
    for layer in stage.GetLayerStack(includeSessionLayers=False):
        if layer.customLayerData.get(constants.LAYER_TYPE_KEY) in layer_types:
            return layer
    return None


def find_mod_layer(stage: Usd.Stage) -> Sdf.Layer | None:
    """Return the strongest ``replacement``/``workfile`` layer of *stage*, if any.

    A replacement (mod) layer outranks the workfile layer: the workfile is the
    project container, the replacement layer is where Remix edits belong.
    """
    for layer_type in (constants.LAYER_TYPE_REPLACEMENT, constants.LAYER_TYPE_WORKFILE):
        layer = find_layer_of_type(stage, layer_type)
        if layer is not None:
            return layer
    return None


def open_stage(path: str, *, load_all: bool = True) -> Usd.Stage:
    """Open *path* as a composed stage.

    Args:
        path: A capture, mod or full-project USDA/USDC file.
        load_all: Load every payload (default) so geometry is resolvable.

    Returns:
        The composed :class:`Usd.Stage`.
    """
    stage = Usd.Stage.Open(path, load=Usd.Stage.LoadAll if load_all else Usd.Stage.LoadNone)
    if stage is None:
        raise RuntimeError(f"Unable to open USD stage: {path}")
    return stage


class ProjectComposition:
    """A composed Remix project with a locked capture layer and a mod edit target.

    Typical use::

        project = ProjectComposition.open(".../capture.usda")
        stage = project.stage
        ...
        project.close()

    The capture layer is locked as soon as it is found, so accidental writes
    raise instead of corrupting the capture.  :meth:`add_capture_sublayer`
    always inserts the capture at the weakest position, matching the Toolkit.
    """

    def __init__(self, stage: Usd.Stage, path: str):
        self._stage = stage
        self._path = os.path.abspath(path)
        self.capture_layer: Sdf.Layer | None = capture.get_capture_layer(stage)
        self.mod_layer: Sdf.Layer | None = find_mod_layer(stage)
        self._lock_capture_layer()
        if self.mod_layer is not None:
            stage.SetEditTarget(Usd.EditTarget(self.mod_layer))
        # The workfile's omni_layer metadata is authoritative for muting/locking.
        # Never mute the layer this project authors into or the capture itself.
        self.muted_layers, self.locked_layers = apply_omni_layer_state(
            stage,
            protected_layers=(self.mod_layer, self.capture_layer),
        )

    # -- construction -----------------------------------------------------
    @classmethod
    def open(cls, path: str, *, load_all: bool = True) -> "ProjectComposition":
        """Open *path* and prepare capture locking / edit target."""
        return cls(open_stage(path, load_all=load_all), path)

    def close(self) -> None:
        """Drop the stage reference (USD frees the stage with its last handle)."""
        self._stage = None
        self.capture_layer = None
        self.mod_layer = None
        self.muted_layers = []
        self.locked_layers = []

    # -- inspection -------------------------------------------------------
    @property
    def stage(self) -> Usd.Stage:
        if self._stage is None:
            raise RuntimeError("ProjectComposition is closed")
        return self._stage

    @property
    def path(self) -> str:
        return self._path

    @property
    def is_open(self) -> bool:
        return self._stage is not None

    @property
    def is_capture_only(self) -> bool:
        """Whether the opened file is a raw capture with no mod layer."""
        return self.capture_layer is not None and self.mod_layer is None

    @property
    def up_axis_is_y(self) -> bool:
        """Whether the capture geometry is Y-up.

        The capture layer is authoritative: a composed project can declare a
        different root ``upAxis`` (older projects copy it late), but the
        geometry itself is authored in the capture's space.
        """
        value = _layer_metadata(self.capture_layer, "upAxis")
        if value is None:
            value = UsdGeom.GetStageUpAxis(self.stage)
        return value == UsdGeom.Tokens.y

    @property
    def meters_per_unit(self) -> float:
        """Capture scale in metres per USD unit (captures often use ``0.01``)."""
        value = _layer_metadata(self.capture_layer, "metersPerUnit")
        if value is None:
            value = UsdGeom.GetStageMetersPerUnit(self.stage)
        return float(value) if value else 1.0

    @property
    def capture_directory(self) -> str | None:
        """Directory holding the capture, used to resolve textures."""
        layer = self.capture_layer
        if layer is None:
            return None
        layer_path = layer.realPath or layer.identifier
        parent = Path(layer_path).parent
        return str(parent)

    def capture_metadata(self) -> dict:
        layer = self.capture_layer
        return dict(layer.customLayerData) if layer is not None else {}

    # -- layer management -------------------------------------------------
    def _lock_capture_layer(self) -> None:
        """Lock the capture so no code can author into it.

        ``Sdf.Layer`` exposes edit permission rather than a ``locked`` flag in
        current OpenUSD builds; older builds used ``layer.locked``.  The lock is
        in-memory only and never rewrites the capture file.
        """
        _lock_layer(self.capture_layer)

    def add_capture_sublayer(self, capture_path: str) -> Sdf.Layer:
        """Add *capture_path* as the weakest sublayer of the project root layer.

        Any previous capture layer is removed first, mirroring
        ``Setup.import_capture_layer`` (delete existing capture, then append).
        """
        stage = self.stage
        root_layer = stage.GetRootLayer()
        if self.capture_layer is not None:
            self._remove_layer_from_stack(root_layer, self.capture_layer)
            self.capture_layer = None

        capture_layer = Sdf.Layer.FindOrOpen(capture_path)
        if capture_layer is None:
            raise RuntimeError(f"Unable to open capture layer: {capture_path}")
        # SubLayers order is strongest first; append() is the weakest.  Store a
        # path relative to the project layer so the project stays portable.
        root_path = root_layer.realPath or root_layer.identifier
        asset_path = _relative_asset_path(root_path, capture_layer.realPath or capture_path)
        root_layer.subLayerPaths.append(asset_path)
        self.capture_layer = capture_layer
        self._lock_capture_layer()
        self._publish_metadata(capture_layer)
        return capture_layer

    def set_edit_target_mod(self) -> Sdf.Layer | None:
        """Point the edit target at the mod layer, if one exists."""
        if self.mod_layer is None:
            return None
        self.stage.SetEditTarget(Usd.EditTarget(self.mod_layer))
        return self.mod_layer

    def save(self) -> None:
        """Save every dirty, writable layer of the composition."""
        if self._stage is None:
            return
        self._stage.Save()

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _remove_layer_from_stack(root_layer: Sdf.Layer, layer: Sdf.Layer) -> None:
        """Remove every sublayer reference to *layer*, recursively."""
        identifiers = {layer.identifier, layer.realPath}
        visited: set[str] = set()

        def remove_from(current: Sdf.Layer) -> None:
            if current.identifier in visited:
                return
            visited.add(current.identifier)
            original = list(current.subLayerPaths)
            kept = []
            for path in original:
                # ``FindRelativeToLayer`` resolves relative sublayer paths
                # against the layer that authors them, which a bare
                # ``Sdf.Layer.Find`` does not.
                child = Sdf.Layer.FindRelativeToLayer(current, path)
                resolved = current.ComputeAbsolutePath(path)
                if path in identifiers or resolved in identifiers:
                    continue
                kept.append(path)
                if child is not None:
                    remove_from(child)
            # Do not touch layers whose stack is unchanged: a project may lock
            # unrelated sublayers through omni_layer metadata.
            if kept != original:
                current.subLayerPaths[:] = kept

        remove_from(root_layer)

    def _publish_metadata(self, capture_layer: Sdf.Layer) -> None:
        """Apply capture stage metadata to the project layer and publish remix config."""
        stage = self.stage
        # Stage metadata must be authored on the root or session layer; the
        # current edit target is the mod layer.
        with Usd.EditContext(stage, stage.GetRootLayer()):
            for key in ("upAxis", "metersPerUnit", "timeCodesPerSecond"):
                value = _layer_metadata(capture_layer, key)
                if value is None:
                    continue
                if key == "upAxis":
                    UsdGeom.SetStageUpAxis(stage, value)
                elif key == "metersPerUnit":
                    UsdGeom.SetStageMetersPerUnit(stage, value)
                else:
                    stage.SetTimeCodesPerSecond(value)
        capture.publish_remix_config(stage)


def _relative_asset_path(anchor_path: str, target_path: str) -> str:
    """Return *target_path* relative to the layer at *anchor_path*.

    ``Sdf.ComputeAssetPathRelativeToLayer`` can fall back to an absolute path
    when the two layers use differently normalised (short/long) Windows paths,
    which would make a saved project non-portable.  ``os.path.relpath`` on the
    absolute paths is deterministic; a cross-drive target stays absolute.
    """
    try:
        anchor_dir = os.path.dirname(os.path.abspath(anchor_path))
        relative = os.path.relpath(os.path.abspath(target_path), anchor_dir)
    except ValueError:  # different drive on Windows
        return str(target_path).replace("\\", "/")
    relative = relative.replace("\\", "/")
    if not relative.startswith("."):
        relative = "./" + relative
    return relative


def _layer_is_locked(layer: Sdf.Layer | None) -> bool:
    """Return whether *layer* rejects edits."""
    if layer is None:
        return False
    if hasattr(layer, "permissionToEdit"):
        return not layer.permissionToEdit
    return bool(getattr(layer, "locked", False))


def _lock_layer(layer: Sdf.Layer | None) -> bool:
    """Make *layer* read-only in memory; return whether it is locked."""
    if layer is None:
        return False
    if _layer_is_locked(layer):
        return True
    try:
        if hasattr(layer, "SetPermissionToEdit"):
            layer.SetPermissionToEdit(False)
        else:  # pragma: no cover - very old USD
            layer.locked = True
    except Tf.ErrorException as exc:  # pragma: no cover - only on exotic USD builds
        _LOG.warning("Could not lock layer %s: %s", layer.identifier, exc)
        return False
    return _layer_is_locked(layer)


def _norm_layer_path(path: str | None) -> str:
    """Normalise a layer path for Windows-safe comparison."""
    if not path:
        return ""
    return os.path.normcase(os.path.abspath(path))


def _layer_metadata(layer: Sdf.Layer | None, key: str):
    """Read layer-level metadata (``pxr`` exposes it on the pseudo-root).

    ``Sdf.Layer`` has no ``GetMetadata`` in the Python bindings; layer
    metadata such as ``upAxis``/``metersPerUnit`` lives on its pseudo-root
    spec.  Returns ``None`` when the layer does not author *key*.
    """
    if layer is None:
        return None
    pseudo_root = layer.pseudoRoot
    if pseudo_root is None or not pseudo_root.HasInfo(key):
        return None
    return pseudo_root.GetInfo(key)
