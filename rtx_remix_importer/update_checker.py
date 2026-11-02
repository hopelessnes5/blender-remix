"""Add-on preferences and a non-blocking check of published GitHub releases.

Only the standard library is used. The network request runs in a worker thread;
all Blender RNA/UI updates stay on Blender's main thread via an app timer.
"""

import json
import queue
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import bpy

from . import bl_info


REPOSITORY_URL = "https://github.com/hopelessnes5/blender-remix"
RELEASES_URL = REPOSITORY_URL + "/releases"
LATEST_RELEASE_API = "https://api.github.com/repos/hopelessnes5/blender-remix/releases/latest"

_results = queue.Queue()
_state = {
    "phase": "idle",  # idle, checking, current, update, ahead, error
    "message": "Press 'Check for Updates' to query GitHub releases.",
    "tag": "",
    "url": RELEASES_URL,
}


def _parse_version(tag):
    """Parse ordinary stable release tags without using external packages."""
    match = re.fullmatch(r"[vV]?(\d+(?:\.\d+){0,3})", tag.strip())
    if not match:
        raise ValueError("GitHub release has an unsupported version tag")
    return tuple(int(piece) for piece in match.group(1).split("."))


def _compare_versions(installed, released):
    size = max(len(installed), len(released))
    local = tuple(installed) + (0,) * (size - len(installed))
    remote = tuple(released) + (0,) * (size - len(released))
    return (remote > local) - (remote < local)


def _get_latest_release():
    """Runs in a worker thread. Must not call Blender APIs."""
    request = Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Blender-Remix-Toolkit-update-checker",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=8) as response:
        data = json.load(response)

    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        raise ValueError("GitHub release does not contain a version tag")
    version = _parse_version(tag)
    release_url = data.get("html_url", RELEASES_URL)
    if not isinstance(release_url, str) or not release_url.startswith(RELEASES_URL + "/"):
        release_url = RELEASES_URL
    return tag, version, release_url


def _check_worker():
    try:
        _results.put((True, _get_latest_release()))
    except HTTPError as exc:
        if exc.code == 404:
            message = "No published stable releases found on GitHub."
        elif exc.code == 403:
            message = "GitHub API access denied or rate limit reached (HTTP 403)."
        else:
            message = f"GitHub returned HTTP {exc.code}."
        _results.put((False, message))
    except (URLError, TimeoutError, OSError) as exc:
        _results.put((False, f"Cannot reach GitHub: {str(exc.reason) if isinstance(exc, URLError) else str(exc)}"))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        _results.put((False, f"Invalid GitHub release data: {exc}"))
    except Exception as exc:
        _results.put((False, f"Update check failed: {type(exc).__name__}: {exc}"))


def _redraw_preferences():
    """Called only by Blender's main thread."""
    wm = bpy.context.window_manager
    if not wm:
        return
    for window in wm.windows:
        screen = window.screen
        if not screen:
            continue
        for area in screen.areas:
            if area.type == 'PREFERENCES':
                area.tag_redraw()


def _poll_result():
    """Transfer the worker result back to Blender's main thread."""
    try:
        ok, value = _results.get_nowait()
    except queue.Empty:
        return 0.2 if _state["phase"] == "checking" else None

    if ok:
        tag, version, release_url = value
        comparison = _compare_versions(bl_info["version"], version)
        if comparison > 0:
            phase = "update"
            message = f"Update available: {tag} (installed: {_installed_version()})."
        elif comparison < 0:
            phase = "ahead"
            message = f"Installed version {_installed_version()} is newer than release {tag}."
        else:
            phase = "current"
            message = f"Up to date: {_installed_version()} (latest release: {tag})."
        _state.update(phase=phase, message=message, tag=tag, url=release_url)
    else:
        _state.update(phase="error", message=value, tag="", url=RELEASES_URL)
    _redraw_preferences()
    return None


def _installed_version():
    return ".".join(map(str, bl_info["version"]))


class REMIX_OT_CheckForUpdates(bpy.types.Operator):
    """Check the latest published stable release without blocking Blender"""

    bl_idname = "remix.check_for_updates"
    bl_label = "Check for Updates"
    bl_description = "Check the latest stable release on GitHub (no download or installation)"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _state["phase"] != "checking"

    def execute(self, context):
        # Avoid showing a previous result while a new request is running.
        while True:
            try:
                _results.get_nowait()
            except queue.Empty:
                break
        _state.update(phase="checking", message="Checking GitHub releases...", tag="", url=RELEASES_URL)
        try:
            if not bpy.app.timers.is_registered(_poll_result):
                bpy.app.timers.register(_poll_result, first_interval=0.2)
            threading.Thread(target=_check_worker, name="RemixReleaseCheck", daemon=True).start()
        except Exception as exc:
            _state.update(phase="error", message=f"Could not start update check: {exc}")
            self.report({'ERROR'}, _state["message"])
            return {'CANCELLED'}
        self.report({'INFO'}, "Checking GitHub releases...")
        return {'FINISHED'}


class REMIX_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__  # rtx_remix_importer

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"Installed version: {_installed_version()}", icon='INFO')

        row = layout.row(align=True)
        row.operator(REMIX_OT_CheckForUpdates.bl_idname, icon='FILE_REFRESH')
        row.operator('wm.url_open', text="Open Repository", icon='URL').url = REPOSITORY_URL

        phase = _state["phase"]
        icons = {
            "idle": 'INFO', "checking": 'TIME', "current": 'CHECKMARK',
            "update": 'ERROR', "ahead": 'INFO', "error": 'ERROR',
        }
        layout.label(text=_state["message"], icon=icons.get(phase, 'INFO'))
        if phase in {"update", "current", "ahead", "error"}:
            layout.operator('wm.url_open', text="View Releases", icon='URL').url = _state["url"]


_CLASSES = (REMIX_OT_CheckForUpdates, REMIX_AddonPreferences)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    if bpy.app.timers.is_registered(_poll_result):
        bpy.app.timers.unregister(_poll_result)
    _state.update(phase="idle", message="Press 'Check for Updates' to query GitHub releases.", tag="", url=RELEASES_URL)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
