import bpy
import json
import os
import tempfile
import uuid

from .operators.capture_ops import auto_scan_capture_folder
from .capture_list import RemixCaptureListItem
from .capture_panel import load_thumbnail


# -----------------------------------------------------------------------------
# Game folder discovery. The original mod/capture properties remain intact so
# existing import/export operators continue to work without modification.
# -----------------------------------------------------------------------------
_NO_MOD = "__NONE__"
_MOD_ITEM_CACHE = {}  # Keep strings alive for Blender's dynamic EnumProperty.
_RESCANNING = set()
_NO_SAVED_GAME = '__NO_SAVED_GAME__'
_SAVED_GAME_ITEM_CACHE = {}  # Blender dynamic EnumProperty requires persistent strings.
_SWITCHING_SAVED_GAME = set()


def _subfolder(parent, name):
    """Find child directory case-insensitively, including on Windows."""
    if not parent or not os.path.isdir(parent):
        return ""
    try:
        with os.scandir(parent) as entries:
            for entry in entries:
                if entry.name.casefold() == name.casefold() and entry.is_dir():
                    return entry.path
    except OSError:
        pass
    return ""


def _subfolders(parent):
    try:
        with os.scandir(parent) as entries:
            return sorted((e.path for e in entries if e.is_dir(follow_symlinks=False)),
                          key=str.casefold)
    except OSError:
        return []


def _discover_remix_folder(game_folder):
    """Find rtx-remix at game root or up to two directories beneath it."""
    if os.path.basename(game_folder).casefold() == 'rtx-remix':
        return game_folder, os.path.dirname(game_folder)

    levels = [[game_folder]]
    for _ in range(2):
        levels.append([child for parent in levels[-1]
                       for child in _subfolders(parent)
                       if os.path.basename(child).casefold() != 'rtx-remix'])

    matches = []
    for depth, parents in enumerate(levels):
        for parent in parents:
            candidate = _subfolder(parent, 'rtx-remix')
            if candidate:
                quality = bool(_subfolder(candidate, 'mods')) + bool(_subfolder(candidate, 'captures'))
                matches.append((-quality, depth, candidate))
    if matches:
        matches.sort(key=lambda it: (it[0], it[1], it[2].casefold()))
        return matches[0][2], game_folder
    return "", game_folder


def _scan_mods(mods_dir):
    """Find mod.usda (and mod.usd/usdc) under a Remix mods directory."""
    if not os.path.isdir(mods_dir):
        return []
    found = []
    for current, dirs, files in os.walk(mods_dir, followlinks=False):
        rel_dir = os.path.relpath(current, mods_dir)
        depth = 0 if rel_dir == '.' else len(PathParts(rel_dir))
        dirs[:] = sorted((d for d in dirs if not os.path.islink(os.path.join(current, d))),
                         key=str.casefold) if depth < 4 else []
        for name in sorted(files, key=str.casefold):
            if name.casefold() not in ('mod.usda', 'mod.usd', 'mod.usdc'):
                continue
            full_path = os.path.join(current, name)
            if not os.path.isfile(full_path):
                continue
            rel = os.path.relpath(full_path, mods_dir).replace('\\', '/')
            dirname = os.path.dirname(rel).replace('\\', '/')
            label = dirname if dirname else '(Mods root)'
            found.append({'id': rel, 'label': label, 'path': full_path})
    found.sort(key=lambda m: (m['label'].casefold(), m['id'].casefold()))
    return found


def PathParts(path):
    return [part for part in path.replace('\\', '/').split('/') if part]


def _available_mods(scene):
    try:
        value = json.loads(scene.remix_available_mods_json or '[]')
        return value if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


def _mod_enum_items(self, context):
    raw = self.remix_available_mods_json or ''
    items = _MOD_ITEM_CACHE.get(raw)
    if items is None:
        items = [(_NO_MOD, 'Select a mod...', 'Choose which discovered mod to work with')]
        for entry in _available_mods(self):
            name = entry['label']
            if os.path.basename(entry['id']).casefold() != 'mod.usda':
                name += ' [' + os.path.basename(entry['id']) + ']'
            items.append((entry['id'], name, entry['path']))
        # EnumProperty stores pointers to strings: retain items for the lifetime
        # of each distinct list; do not rebuild ephemeral strings per UI redraw.
        _MOD_ITEM_CACHE[raw] = items
    return items


def _clear_project_state(scene):
    scene.remix_active_sublayer_path = ''
    for name in ('_remix_sublayers_ordered', '_remix_exported_assets'):
        if name in scene:
            del scene[name]


def _choose_mod(scene, context, *, load=True):
    identifier = scene.remix_active_mod_choice
    match = next((m for m in _available_mods(scene) if m['id'] == identifier), None)
    full_path = match['path'] if match else ''
    mods_root = scene.remix_mods_folder_path
    if full_path:
        try:
            valid = (os.path.commonpath([os.path.realpath(full_path),
                                         os.path.realpath(mods_root)]) == os.path.realpath(mods_root))
        except (OSError, ValueError):
            valid = False
        if not valid or not os.path.isfile(full_path):
            full_path = ''

    if os.path.normcase(scene.remix_mod_file_path) == os.path.normcase(full_path):
        return
    _clear_project_state(scene)
    scene.remix_mod_file_path = full_path
    project_root = os.path.dirname(full_path) if full_path else ''
    scene.remix_project_root = project_root
    scene.remix_project_root_display = project_root or '(No mod selected)'
    scene.remix_mod_load_status = ''

    if load and full_path and context is not None:
        # Use the existing USD project loader. A manual button remains available
        # when pxr is unavailable or loading fails, and never writes mod data.
        try:
            if bpy.ops.remix.load_project.poll():
                result = bpy.ops.remix.load_project('EXEC_DEFAULT')
                if 'FINISHED' not in result:
                    scene.remix_mod_load_status = 'Mod selected, but loading failed. See Blender log.'
            else:
                scene.remix_mod_load_status = 'Mod selected; Load Project is unavailable (check USD support).'
        except (AttributeError, RuntimeError, TypeError) as exc:
            scene.remix_mod_load_status = f'Mod selected; load failed: {exc}'


def _on_mod_changed(self, context):
    if self.as_pointer() not in _RESCANNING:
        _choose_mod(self, context)
        _save_current_mod_preference(self)


def scan_game_folder(scene, context=None):
    """Discover the runtime, mods and captures; preserve an existing selection."""
    key = scene.as_pointer()
    if key in _RESCANNING:
        return 0
    _RESCANNING.add(key)
    try:
        previous_game = scene.remix_detected_game_root
        previous_mod_path = scene.remix_mod_file_path
        input_path = scene.remix_game_folder_path.strip()
        game_folder = os.path.normpath(os.path.abspath(bpy.path.abspath(input_path))) if input_path else ''
        if game_folder and not os.path.isdir(game_folder):
            status = 'Game folder does not exist.'
            remix_dir, resolved_game = '', game_folder
        elif game_folder:
            remix_dir, resolved_game = _discover_remix_folder(game_folder)
            status = 'rtx-remix folder not found.' if not remix_dir else ''
        else:
            remix_dir, resolved_game = '', ''
            status = 'Select a game folder.'

        mods_dir = _subfolder(remix_dir, 'mods') if remix_dir else ''
        captures_dir = _subfolder(remix_dir, 'captures') if remix_dir else ''
        mods = _scan_mods(mods_dir) if mods_dir else []
        game_changed = os.path.normcase(previous_game) != os.path.normcase(resolved_game)
        if game_changed:
            scene.remix_game_name = os.path.basename(resolved_game) if remix_dir else 'Unknown Game'
        scene.remix_detected_game_root = resolved_game
        scene.remix_remix_folder_path = remix_dir
        scene.remix_mods_folder_path = mods_dir

        # Clear old capture entries before updating their scan target.
        if scene.remix_capture_folder_path != captures_dir:
            scene.remix_captures.clear()
            scene.remix_captures_index = 0
            scene.remix_capture_folder_path = captures_dir
        elif captures_dir and context is not None:
            # The user clicked Refresh; a property update won't run if unchanged.
            try:
                if bpy.ops.remix.scan_capture_folder.poll():
                    bpy.ops.remix.scan_capture_folder('EXEC_DEFAULT')
            except (AttributeError, RuntimeError, TypeError) as exc:
                print(f'[RTX Remix] Capture scan failed: {exc}')

        # Clear the old enum value before replacing its item list.
        scene.remix_active_mod_choice = _NO_MOD
        scene.remix_available_mods_json = json.dumps(mods, ensure_ascii=False)
        preferred = next((m['id'] for m in mods
                          if _normalized_path(m['path']) == _normalized_path(previous_mod_path)), None)
        if preferred is None and game_changed:
            saved = _find_saved_game(resolved_game)
            if saved and saved['last_mod_id'] in {m['id'] for m in mods}:
                preferred = saved['last_mod_id']
        if preferred is None and len(mods) == 1:
            preferred = mods[0]['id']
        scene.remix_active_mod_choice = preferred or _NO_MOD

        # Reset the project state if game changed or the previous mod disappeared.
        chosen = next((m['path'] for m in mods if m['id'] == preferred), '')
        if os.path.normcase(chosen) != os.path.normcase(previous_mod_path):
            _clear_project_state(scene)
        scene.remix_mod_file_path = chosen
        project_root = os.path.dirname(chosen) if chosen else ''
        scene.remix_project_root = project_root
        scene.remix_project_root_display = project_root or '(No mod selected)'
        scene.remix_mod_load_status = ''

        if remix_dir:
            notes = []
            if not mods_dir:
                notes.append('mods not found')
            elif not mods:
                notes.append('no mod.usda found')
            if not captures_dir:
                notes.append('captures not found')
            status = f'{len(mods)} mod(s) discovered' + (f' | {", ".join(notes)}' if notes else '')
        scene.remix_discovery_status = status
        if chosen and (game_changed or os.path.normcase(chosen) != os.path.normcase(previous_mod_path)):
            # During property updates the target mod may be selected automatically.
            _choose_mod_force_load(scene, context)
        return len(mods)
    finally:
        _RESCANNING.discard(key)


def _choose_mod_force_load(scene, context):
    if context is None:
        return
    try:
        if bpy.ops.remix.load_project.poll():
            result = bpy.ops.remix.load_project('EXEC_DEFAULT')
            if 'FINISHED' not in result:
                scene.remix_mod_load_status = 'Mod selected; loading failed. See Blender log.'
        else:
            scene.remix_mod_load_status = 'Mod selected; Load Project unavailable (check USD support).'
    except (AttributeError, RuntimeError, TypeError) as exc:
        scene.remix_mod_load_status = f'Mod selected; load failed: {exc}'


def _on_game_folder_changed(self, context):
    self.remix_saved_games_status = ''
    scan_game_folder(self, context)
    _sync_saved_game_selection(self)


class REMIX_OT_RescanGameFolder(bpy.types.Operator):
    """Rediscover RTX Remix directories and available mods in the selected game"""
    bl_idname = 'remix.rescan_game_folder'
    bl_label = 'Refresh RTX Remix Game Folder'
    bl_options = {'REGISTER'}

    def execute(self, context):
        count = scan_game_folder(context.scene, context)
        self.report({'INFO'}, f'RTX Remix discovery complete: {count} mod(s) found')
        return {'FINISHED'}



# -----------------------------------------------------------------------------
# Saved game installations, shared by all .blend scenes and Blender sessions.
# Settings are kept in the user's Blender CONFIG folder, NOT inside the mod.
# -----------------------------------------------------------------------------
def _saved_games_file():
    folder = bpy.utils.user_resource('CONFIG', path='rtx_remix_importer', create=True)
    return os.path.join(folder, 'saved_games.json')


def _normalized_path(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path))) if path else ''


def _read_saved_games():
    try:
        with open(_saved_games_file(), 'r', encoding='utf-8') as fp:
            data = json.load(fp)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return []
    entries = data.get('games', []) if isinstance(data, dict) else []
    if not isinstance(entries, list):
        return []
    games = []
    seen_ids, seen_paths = set(), set()
    for item in entries[:256]:
        if not isinstance(item, dict):
            continue
        identifier, name, path = item.get('id'), item.get('name'), item.get('path')
        if not all(isinstance(v, str) for v in (identifier, name, path)):
            continue
        if (not identifier or not name.strip() or not os.path.isabs(path)
                or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier)):
            continue
        normalized = _normalized_path(path)
        if identifier in seen_ids or normalized in seen_paths:
            continue
        seen_ids.add(identifier)
        seen_paths.add(normalized)
        mod_id = item.get('last_mod_id', '')
        games.append({'id': identifier[:80], 'name': name[:128], 'path': path,
                      'last_mod_id': mod_id if isinstance(mod_id, str) else ''})
    return games


def _write_saved_games(games):
    destination = _saved_games_file()
    folder = os.path.dirname(destination)
    os.makedirs(folder, exist_ok=True)
    # Replace atomically so a stopped Blender process cannot truncate the list.
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder,
                                         prefix='.saved_games_', suffix='.tmp',
                                         delete=False) as fp:
            temp_path = fp.name
            json.dump({'version': 1, 'games': games}, fp,
                      ensure_ascii=False, indent=2)
            fp.write('\n')
        os.replace(temp_path, destination)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _find_saved_game(path, games=None):
    target = _normalized_path(path)
    if not target:
        return None
    return next((g for g in (games if games is not None else _read_saved_games())
                 if _normalized_path(g['path']) == target), None)


def _saved_game_enum_items(self, context):
    games = _read_saved_games()
    key = tuple((g['id'], g['name'], g['path']) for g in games)
    if key not in _SAVED_GAME_ITEM_CACHE:
        items = [(_NO_SAVED_GAME, 'Select saved game...',
                  'Choose a saved game installation', 'FILE_FOLDER', 0)]
        for game in games:
            state = '' if os.path.isdir(game['path']) else ' [missing folder]'
            # Stable numeric IDs are required by Blender: dynamic EnumProperty
            # selections are saved as integers, not string identifiers.
            numeric_id = (int(game['id'][:8], 16) & 0x7fffffff) or 1
            items.append((game['id'], game['name'] + state, game['path'],
                          'FILE_FOLDER', numeric_id))
        _SAVED_GAME_ITEM_CACHE[key] = items
    return _SAVED_GAME_ITEM_CACHE[key]


def _set_saved_selection(scene, identifier):
    # Changing the selector on file browsing must not trigger project switching.
    key = scene.as_pointer()
    _SWITCHING_SAVED_GAME.add(key)
    try:
        scene.remix_saved_game_choice = identifier
    finally:
        _SWITCHING_SAVED_GAME.discard(key)


def _sync_saved_game_selection(scene):
    game_path = scene.remix_detected_game_root or scene.remix_game_folder_path
    entry = _find_saved_game(game_path)
    _set_saved_selection(scene, entry['id'] if entry else _NO_SAVED_GAME)
    if entry:
        scene.remix_saved_game_label = entry['name']
    elif game_path:
        scene.remix_saved_game_label = os.path.basename(os.path.normpath(game_path))
    else:
        scene.remix_saved_game_label = ''


def _on_saved_game_choice_changed(self, context):
    if self.as_pointer() in _SWITCHING_SAVED_GAME:
        return
    chosen_id = self.remix_saved_game_choice
    if chosen_id == _NO_SAVED_GAME:
        return
    game = next((g for g in _read_saved_games() if g['id'] == chosen_id), None)
    if not game:
        self.remix_saved_games_status = 'Saved game no longer exists. Refresh the list.'
        return
    if not os.path.isdir(game['path']):
        self.remix_saved_games_status = f'Missing game folder: {game["path"]}'
        return
    # Scan is invoked by the game folder update; its mod preference is read
    # from saved_games.json, and its capture/project paths are rebuilt there.
    if _normalized_path(self.remix_game_folder_path) != _normalized_path(game['path']):
        self.remix_game_folder_path = game['path']
    else:
        scan_game_folder(self, context)
    self.remix_saved_game_label = game['name']
    if self.remix_remix_folder_path:
        self.remix_saved_games_status = 'Project switched; Blender scene objects are retained.'
    else:
        self.remix_saved_games_status = 'Saved game selected, but rtx-remix was not found.'


def _save_current_mod_preference(scene):
    game_path = scene.remix_detected_game_root or scene.remix_game_folder_path
    games = _read_saved_games()
    entry = _find_saved_game(game_path, games)
    if entry is None:
        return
    identifier = scene.remix_active_mod_choice
    desired = '' if identifier == _NO_MOD else identifier
    if entry['last_mod_id'] == desired:
        return
    entry['last_mod_id'] = desired
    try:
        _write_saved_games(games)
    except OSError as exc:
        scene.remix_saved_games_status = f'Could not remember selected mod: {exc}'


class REMIX_OT_SaveGameLocation(bpy.types.Operator):
    """Remember this game's folder and last selected mod for future Blender sessions"""
    bl_idname = 'remix.save_game_location'
    bl_label = 'Save Game Location'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.remix_remix_folder_path and
                    os.path.isdir(context.scene.remix_detected_game_root))

    def execute(self, context):
        scene = context.scene
        path = os.path.normpath(scene.remix_detected_game_root)
        name = scene.remix_saved_game_label.strip() or os.path.basename(path)
        games = _read_saved_games()
        entry = _find_saved_game(path, games)
        if entry:
            entry['name'] = name
            entry['last_mod_id'] = (scene.remix_active_mod_choice
                                    if scene.remix_active_mod_choice != _NO_MOD else '')
            identifier = entry['id']
        else:
            identifier = uuid.uuid4().hex
            games.append({'id': identifier, 'name': name, 'path': path,
                          'last_mod_id': scene.remix_active_mod_choice
                          if scene.remix_active_mod_choice != _NO_MOD else ''})
        try:
            _write_saved_games(games)
        except OSError as exc:
            self.report({'ERROR'}, f'Unable to save game location: {exc}')
            return {'CANCELLED'}
        _set_saved_selection(scene, identifier)
        scene.remix_saved_game_label = name
        scene.remix_saved_games_status = f'Saved: {name}'
        self.report({'INFO'}, f'Saved game location: {path}')
        return {'FINISHED'}


class REMIX_OT_RemoveSavedGame(bpy.types.Operator):
    """Remove this entry from the list; no game or mod files are deleted"""
    bl_idname = 'remix.remove_saved_game'
    bl_label = 'Forget Saved Game'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.scene.remix_saved_game_choice not in ('', _NO_SAVED_GAME)

    def execute(self, context):
        scene = context.scene
        selected = scene.remix_saved_game_choice
        games = _read_saved_games()
        updated = [game for game in games if game['id'] != selected]
        if len(updated) == len(games):
            self.report({'WARNING'}, 'Saved game not found')
            return {'CANCELLED'}
        try:
            _write_saved_games(updated)
        except OSError as exc:
            self.report({'ERROR'}, f'Unable to update saved games: {exc}')
            return {'CANCELLED'}
        _set_saved_selection(scene, _NO_SAVED_GAME)
        scene.remix_saved_games_status = 'Saved location removed. Active project unchanged.'
        self.report({'INFO'}, 'Removed location from saved games (files untouched)')
        return {'FINISHED'}


# --- Scene Properties ---

def poll_is_mesh_object(self, object):
    """ Poll function for PointerProperty to allow selecting only MESH objects. """
    return object.type == 'MESH'

def on_capture_index_update(self, context):
    """Load the thumbnail for the newly selected capture immediately.

    Relying solely on depsgraph_update_post is not enough: simply changing
    the active index of the capture UIList (a plain Scene IntProperty) does
    not trigger a depsgraph update, so the preview would never load/refresh
    when clicking through the list.
    """
    captures = self.remix_captures
    index = self.remix_captures_index
    if 0 <= index < len(captures):
        load_thumbnail(captures[index].full_path)

def register_properties():
    bpy.utils.register_class(REMIX_OT_RescanGameFolder)
    bpy.utils.register_class(REMIX_OT_SaveGameLocation)
    bpy.utils.register_class(REMIX_OT_RemoveSavedGame)
    bpy.types.Scene.remix_saved_game_choice = bpy.props.EnumProperty(
        name='Saved Games', description='Quick switch between remembered RTX Remix games',
        items=_saved_game_enum_items, update=_on_saved_game_choice_changed)
    bpy.types.Scene.remix_saved_game_label = bpy.props.StringProperty(
        name='Saved Name', description='Friendly name for this saved game location',
        default='')
    bpy.types.Scene.remix_saved_games_status = bpy.props.StringProperty(
        name='Saved Games Status', default='')
    bpy.types.Scene.remix_game_folder_path = bpy.props.StringProperty(
        name='Game Folder', description='Game installation folder containing rtx-remix',
        subtype='DIR_PATH', default='', update=_on_game_folder_changed)
    # The game and capture selection are an initial setup step. After loading
    # the capture(s), the user can collapse setup and focus on the mod workspace.
    bpy.types.Scene.remix_setup_finished = bpy.props.BoolProperty(
        name='Setup Finished',
        description='Hide game selection and capture browser until they are needed again',
        default=False)
    bpy.types.Scene.remix_detected_game_root = bpy.props.StringProperty(options={'HIDDEN'}, default='')
    bpy.types.Scene.remix_remix_folder_path = bpy.props.StringProperty(options={'HIDDEN'}, default='')
    bpy.types.Scene.remix_mods_folder_path = bpy.props.StringProperty(options={'HIDDEN'}, default='')
    bpy.types.Scene.remix_available_mods_json = bpy.props.StringProperty(options={'HIDDEN'}, default='[]')
    bpy.types.Scene.remix_active_mod_choice = bpy.props.EnumProperty(
        name='Active Mod', description='Select the RTX Remix mod to edit and export into',
        items=_mod_enum_items, update=_on_mod_changed)
    bpy.types.Scene.remix_discovery_status = bpy.props.StringProperty(default='Select a game folder.')
    bpy.types.Scene.remix_mod_load_status = bpy.props.StringProperty(default='')
    bpy.types.Scene.remix_mod_file_path = bpy.props.StringProperty(
        name="Remix Mod File",
        description="Path to the main mod.usda file for the Remix project",
        subtype='FILE_PATH',
        default=""
    )
    bpy.types.Scene.remix_active_sublayer_path = bpy.props.StringProperty(
        name="Active Sublayer Path",
        description="Full path to the selected target sublayer",
        default=""
    )
    bpy.types.Scene.remix_project_root_display = bpy.props.StringProperty(
        name="Detected Project Root",
        description="The detected root directory of the Remix project",
        default="(Project not loaded)"
    )
    # Add game name property
    bpy.types.Scene.remix_game_name = bpy.props.StringProperty(
        name="Game Name",
        description="Name of the game for the RTX Remix mod",
        default="Unknown Game"
    )
    # Temporary storage for loaded paths - better approach might be needed
    # bpy.types.Scene._remix_loaded_sublayers = bpy.props.CollectionProperty(type=bpy.types.PropertyGroup)
    bpy.types.Scene.remix_new_sublayer_name = bpy.props.StringProperty(
        name="New Sublayer Name",
        description="Name for the new sublayer file (e.g., my_replacements)",
        default="new_sublayer"
    )
    # Add Anchor Target property to Scene
    bpy.types.Scene.remix_anchor_object_target = bpy.props.PointerProperty(
        name="Target Mesh",
        description="Select an imported mesh object to anchor exported assets to (optional)",
        type=bpy.types.Object,
        poll=poll_is_mesh_object # Reuse the same poll function
    )
    bpy.types.Scene.remix_export_scale = bpy.props.FloatProperty(
        name="Export Scale",
        description="Global scale factor to apply when exporting assets. 0.01 is recommended for most RTX Remix games.",
        default=0.01,
        min=0.001,
        soft_min=0.01,
        soft_max=100.0,
        precision=3,
    )
    
    # Add property to control automatic transform application
    bpy.types.Scene.remix_auto_apply_transforms = bpy.props.BoolProperty(
        name="Auto Apply Transforms",
        description="Automatically apply all transforms (location, rotation, and scale) before exporting meshes (fixes coordinate issues in-game and results in clean identity transforms)",
        default=True,
    )
    
    # Add property to control texture reuse optimization
    bpy.types.Scene.remix_reuse_existing_textures = bpy.props.BoolProperty(
        name="Reuse Existing Textures",
        description="Automatically reuse existing textures for materials with the same base name (ignoring hash suffixes). Disable to force reprocessing of all textures",
        default=True,
    )
    
    # Add property to control hiding original mesh on export
    bpy.types.Scene.remix_hide_original_mesh = bpy.props.BoolProperty(
        name="Hide Target Mesh",
        description="Hide the original mesh by setting 'references = None' when exporting a replacement mesh. "
                    "This is what makes a replacement a replacement; disable only for debugging",
        default=True,
    )
    
    # Add properties for custom asset export directories
    bpy.types.Scene.remix_custom_mesh_dir = bpy.props.StringProperty(
        name="Mesh Directory",
        description="Custom directory for exported meshes (relative to mod root). Default: 'assets/ingested'",
        default="assets/ingested",
    )
    
    bpy.types.Scene.remix_custom_texture_dir = bpy.props.StringProperty(
        name="Texture Directory",
        description="Custom directory for exported textures (relative to mod root). Default: 'rtx-remix/textures'",
        default="rtx-remix/textures",
    )
    
    bpy.types.Scene.remix_show_assets_not_in_scene = bpy.props.BoolProperty(
        name="Show Assets Not in Scene",
        description="Show assets that exist in USD files but are not present in the current Blender scene",
        default=False,
    )
    
    # --- New Capture Properties ---
    bpy.types.Scene.remix_capture_folder_path = bpy.props.StringProperty(
        name="Capture Folder",
        description="Path to the RTX Remix capture folder containing .usd files",
        subtype='DIR_PATH',
        default="",
        update=lambda self, context: auto_scan_capture_folder(self, context)
    )
    bpy.types.Scene.remix_capture_scene_scale = bpy.props.FloatProperty(
        name="Capture Scale",
        description="Scale factor to apply when importing capture files. 0.01 is recommended for most RTX Remix games",
        default=0.01,
        min=0.001, soft_min=0.01, soft_max=100.0,
    )
    bpy.types.Scene.remix_capture_import_materials = bpy.props.BoolProperty(
        name="Import Materials",
        description="Import and convert RTX Remix materials from captures",
        default=True,
    )
    bpy.types.Scene.remix_capture_import_lights = bpy.props.BoolProperty(
        name="Import Lights",
        description="Import light sources from captures",
        default=True,
    )

    # --- UIList Properties ---
    bpy.types.Scene.remix_captures = bpy.props.CollectionProperty(type=RemixCaptureListItem)
    bpy.types.Scene.remix_captures_index = bpy.props.IntProperty(
        name="Capture List Index",
        default=0,
        update=on_capture_index_update,
    )

    bpy.types.Scene.remix_project_root = bpy.props.StringProperty(
        name="Project Root",
        description="Root directory of the RTX Remix project",
        subtype='DIR_PATH'
    )
    bpy.types.Scene.remix_last_imported_camera = bpy.props.StringProperty(
        name="Last Imported Camera",
        description="Name of the most recently imported camera object",
        default=""
    )
    bpy.types.Scene.remix_sublayer_file = bpy.props.StringProperty(
        name="Sublayer File",
        description="The USD sublayer file for asset replacements",
    )

def unregister_properties():
    for name in ('remix_saved_game_choice', 'remix_saved_game_label',
                 'remix_saved_games_status',
                 'remix_setup_finished', 'remix_game_folder_path', 'remix_detected_game_root',
                 'remix_remix_folder_path', 'remix_mods_folder_path',
                 'remix_available_mods_json', 'remix_active_mod_choice',
                 'remix_discovery_status', 'remix_mod_load_status'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    bpy.utils.unregister_class(REMIX_OT_RemoveSavedGame)
    bpy.utils.unregister_class(REMIX_OT_SaveGameLocation)
    bpy.utils.unregister_class(REMIX_OT_RescanGameFolder)
    _SAVED_GAME_ITEM_CACHE.clear()
    _SWITCHING_SAVED_GAME.clear()
    _MOD_ITEM_CACHE.clear()
    del bpy.types.Scene.remix_mod_file_path
    # del bpy.types.Scene.remix_project_dir
    if hasattr(bpy.types.Scene, "remix_active_sublayer_path"):
        del bpy.types.Scene.remix_active_sublayer_path
    if hasattr(bpy.types.Scene, "remix_project_root_display"): 
        del bpy.types.Scene.remix_project_root_display
    if hasattr(bpy.types.Scene, "remix_new_sublayer_name"): 
        del bpy.types.Scene.remix_new_sublayer_name
    if hasattr(bpy.types.Scene, "remix_anchor_object_target"): 
        del bpy.types.Scene.remix_anchor_object_target
    if hasattr(bpy.types.Scene, "remix_game_name"):
        del bpy.types.Scene.remix_game_name
    if hasattr(bpy.types.Scene, "remix_export_scale"):
        del bpy.types.Scene.remix_export_scale
    if hasattr(bpy.types.Scene, "remix_auto_apply_transforms"):
        del bpy.types.Scene.remix_auto_apply_transforms
    if hasattr(bpy.types.Scene, "remix_reuse_existing_textures"):
        del bpy.types.Scene.remix_reuse_existing_textures
    if hasattr(bpy.types.Scene, "remix_hide_original_mesh"):
        del bpy.types.Scene.remix_hide_original_mesh
    if hasattr(bpy.types.Scene, "remix_custom_mesh_dir"):
        del bpy.types.Scene.remix_custom_mesh_dir
    if hasattr(bpy.types.Scene, "remix_custom_texture_dir"):
        del bpy.types.Scene.remix_custom_texture_dir
    if hasattr(bpy.types.Scene, "remix_show_assets_not_in_scene"):
        del bpy.types.Scene.remix_show_assets_not_in_scene
    # if hasattr(bpy.types.Scene, "_remix_loaded_sublayers"): # Clean up temp storage
    #     del bpy.types.Scene._remix_loaded_sublayers
    if hasattr(bpy.types.Scene, "_remix_sublayers_ordered"): # Clean up temp storage
         del bpy.types.Scene["_remix_sublayers_ordered"]
    
    # --- Clean up Capture Properties ---
    if hasattr(bpy.types.Scene, "remix_capture_folder_path"):
        del bpy.types.Scene.remix_capture_folder_path
    if hasattr(bpy.types.Scene, "remix_capture_scene_scale"):
        del bpy.types.Scene.remix_capture_scene_scale
    if hasattr(bpy.types.Scene, "remix_capture_import_materials"):
        del bpy.types.Scene.remix_capture_import_materials
    if hasattr(bpy.types.Scene, "remix_capture_import_lights"):
        del bpy.types.Scene.remix_capture_import_lights
    if hasattr(bpy.types.Scene, "_remix_available_captures"):
        del bpy.types.Scene["_remix_available_captures"]
    if hasattr(bpy.types.Scene, "_remix_batch_selected_captures"):
        del bpy.types.Scene["_remix_batch_selected_captures"] 
    
    # --- Clean up UIList Properties ---
    if hasattr(bpy.types.Scene, "remix_captures"):
        del bpy.types.Scene.remix_captures
    if hasattr(bpy.types.Scene, "remix_captures_index"):
        del bpy.types.Scene.remix_captures_index
    if hasattr(bpy.types.Scene, "remix_project_root"):
        del bpy.types.Scene.remix_project_root
    if hasattr(bpy.types.Scene, "remix_last_imported_camera"):
        del bpy.types.Scene.remix_last_imported_camera
    if hasattr(bpy.types.Scene, "remix_sublayer_file"):
        del bpy.types.Scene.remix_sublayer_file