"""The single guard shared by every layer-write entry point.

Remix writes two kinds of USD layers: the game's own capture data and the
editable project layers (workfile, replacement and freshly exported targets).
Rule for the whole add-on (audit A01, AGENTS.md): a capture layer is never
written, a layer locked through ``permissionToEdit`` is never written, and
every save re-checks immediately before writing instead of trusting the check
made when the layer was first opened.

Operators route their writes through the helpers below so the rules cannot
drift between call sites.  The messages double as user-facing errors; they
keep the wording (``capture`` / ``locked``) that existing tests match.
"""
from __future__ import annotations

from pxr import Sdf, Usd

__all__ = [
    "layer_type",
    "is_capture_layer",
    "ensure_writable_layer",
    "open_writable_layer",
    "ensure_editable_edit_target",
]


def layer_type(layer: Sdf.Layer | None) -> str | None:
    """Return the ``lightspeed_layer_type`` recorded for *layer*, if any.

    Key duplicated from :mod:`rtx_remix_importer.nvidia_core.constants` on
    purpose: this module must stay a leaf so operators can import it before
    (and while) the core package finishes its own submodule chain.
    """
    if layer is None:
        return None
    data = layer.customLayerData
    value = data.get("lightspeed_layer_type") if data else None
    return value if isinstance(value, str) else None


def is_capture_layer(layer: Sdf.Layer | None) -> bool:
    """Whether *layer* carries the game-capture layer type."""
    from .nvidia_core import constants  # deferred: keeps this module a leaf

    return layer_type(layer) == constants.LAYER_TYPE_CAPTURE


def ensure_writable_layer(
    layer: Sdf.Layer | None,
    *,
    open_path: str | None = None,
    verb: str = "edit",
) -> None:
    """Raise ``ValueError`` when *layer* must not be written.

    Run this immediately before authoring or saving.  A capture layer stays
    read-only (it is the game's data); a layer with ``permissionToEdit`` off is
    locked, which is also how an open capture is pinned in memory.  Pass
    ``open_path`` to name the file the layer was opened from, and ``verb`` to
    describe the refused operation ("edit", "export into a") in the error.
    """
    if layer is None:
        raise ValueError(f"Could not open layer: {open_path}" if open_path else "No layer to author into")
    label = open_path or layer.identifier
    if is_capture_layer(layer):
        raise ValueError(f"Cannot {verb} capture layer: {label}")
    if not layer.permissionToEdit:
        raise ValueError(f"Layer is locked: {label}")


def open_writable_layer(path: str, *, verb: str = "edit") -> Sdf.Layer:
    """Open *path* for authoring, refusing capture and locked layers."""
    layer = Sdf.Layer.FindOrOpen(path)
    if not layer:
        raise ValueError(f"Could not open layer: {path}")
    ensure_writable_layer(layer, open_path=path, verb=verb)
    return layer


def ensure_editable_edit_target(stage: Usd.Stage | None, *, action: str = "author") -> None:
    """Refuse a stage whose edit target points at the capture layer.

    ``Usd.EditContext`` authors every edit into the edit target, so a capture
    target would corrupt the game's data even when the guard ran on other
    layers first.  Same wording as the replacement-copier guard.
    """
    if stage is None:
        raise ValueError("No stage to author into")
    target_layer = stage.GetEditTarget().GetLayer()
    if is_capture_layer(target_layer):
        raise ValueError(
            f"Refusing to {action} into the capture layer {target_layer.identifier}; "
            "set the edit target to the mod (replacement) layer first"
        )
