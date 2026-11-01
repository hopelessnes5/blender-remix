"""Analyze or clean legacy exporter texture suffixes in one Remix mod folder.

Run with Blender's Python (for its bundled pxr):
  blender --background --python tools/clean_mod_texture_suffixes.py -- MOD_DIR
  blender --background --python tools/clean_mod_texture_suffixes.py -- MOD_DIR --apply

Only matching DDS names and their references in text USD layers are changed.
Capture layers are never modified. Binary USD layers block changes because
they could contain references that cannot safely be updated as text.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime
from zipfile import ZIP_DEFLATED, ZipFile

from pxr import Sdf, Tf

SUFFIX = re.compile(r'(?:\.[0-9a-f]{16}\.oct)?\.[anrmeso]\.rtex(?=\.dds$)', re.IGNORECASE)
TOKENS = re.compile(r'@([^@\r\n]+)@|"([^"\r\n]*)"')
BACKUPS = '.suffix-cleanup-backups'


def cleaned_name(name):
    return SUFFIX.sub('', name)


def resolve_reference(layer_path, value, root):
    """Resolve a local, unescaped relative/absolute file reference inside root."""
    if not value or '://' in value or '\n' in value:
        return None
    candidate = Path(value.replace('\\', '/'))
    if not candidate.is_absolute():
        candidate = layer_path.parent / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def build_plan(folder):
    root = Path(folder).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('The mod path must be a directory')
    files = []
    for path in root.rglob('*'):
        if BACKUPS in path.relative_to(root).parts:
            continue
        if path.is_symlink():
            raise ValueError(f'Symbolic links are not supported: {path}')
        if path.is_file():
            files.append(path)
    candidates = {}
    occupied = {str(p).casefold(): p for p in files}
    destinations = {}
    for path in files:
        name = cleaned_name(path.name)
        if path.suffix.lower() != '.dds' or name == path.name:
            continue
        dest = path.with_name(name)
        key = str(dest).casefold()
        if key in occupied or key in destinations:
            raise ValueError(f'Name collision: {path} -> {dest}')
        candidates[path] = dest
        destinations[key] = path

    layers = []
    for path in files:
        if path.suffix.lower() not in ('.usd', '.usda', '.usdc'):
            continue
        with path.open('rb') as stream:
            header = stream.read(16)
        if not header.startswith(b'#usda'):
            raise ValueError(f'Binary or unknown USD layer cannot be checked safely: {path}')
        layer = Sdf.Layer.FindOrOpen(str(path))
        if not layer:
            raise ValueError(f'Cannot read USD layer: {path}')
        raw = path.read_bytes()
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError(f'USD layer is not UTF-8: {path}') from exc
        layers.append((path, content, layer.customLayerData.get('lightspeed_layer_type') == 'capture'))

    # Never rename a texture that a capture layer in the mod tree references.
    for path, content, is_capture in layers:
        if not is_capture:
            continue
        for match in TOKENS.finditer(content):
            value = match.group(1) if match.group(1) is not None else match.group(2)
            referenced = resolve_reference(path, value, root)
            if referenced in candidates:
                raise ValueError(f'Capture layer references a texture proposed for rename: {path} -> {referenced}')

    edits = {}
    for path, content, is_capture in layers:
        if is_capture:
            continue
        def rewrite(match):
            value = match.group(1) if match.group(1) is not None else match.group(2)
            referenced = resolve_reference(path, value, root)
            destination = candidates.get(referenced)
            if destination is None:
                return match.group(0)
            new_value = value[:-len(referenced.name)] + destination.name
            delimiter = '@' if match.group(1) is not None else '"'
            return delimiter + new_value + delimiter
        updated = TOKENS.sub(rewrite, content)
        if updated != content:
            edits[path] = updated.encode('utf-8')
    return root, candidates, edits


def apply_plan(root, renames, edits):
    if not renames and not edits:
        return None
    backup_dir = root / BACKUPS
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup_path = backup_dir / f'before_{stamp}.zip'
    originals = sorted(set(renames) | set(edits))
    with ZipFile(backup_path, 'x', compression=ZIP_DEFLATED) as archive:
        for path in originals:
            archive.write(path, path.relative_to(root).as_posix())
        archive.writestr('_cleanup_manifest.json', json.dumps({
            'renames': {str(a.relative_to(root)): str(b.relative_to(root)) for a, b in renames.items()},
            'edited_layers': [str(p.relative_to(root)) for p in edits],
        }, ensure_ascii=False, indent=2))
    with ZipFile(backup_path) as archive:
        failed = archive.testzip()
        if failed:
            raise OSError(f'Backup verification failed: {failed}')
    moved = []
    try:
        for src, dst in renames.items():
            if dst.exists():
                raise FileExistsError(dst)
            src.rename(dst)
            moved.append((src, dst))
        for path, data in edits.items():
            path.write_bytes(data)
    except (OSError, ValueError):
        for src, dst in reversed(moved):
            if dst.exists() and not src.exists():
                dst.rename(src)
        with ZipFile(backup_path) as archive:
            for path in originals:
                path.write_bytes(archive.read(path.relative_to(root).as_posix()))
        raise
    return backup_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mod_dir', help='Root directory of one mod')
    parser.add_argument('--apply', action='store_true', help='Create a backup and apply the plan')
    args = parser.parse_args(argv)
    try:
        root, renames, edits = build_plan(args.mod_dir)
        for src, dst in renames.items():
            print(f'RENAME {src.relative_to(root)} -> {dst.relative_to(root)}')
        for path in edits:
            print(f'UPDATE {path.relative_to(root)}')
            old = path.read_text(encoding='utf-8').splitlines()
            new = edits[path].decode('utf-8').splitlines()
            for line in difflib.unified_diff(old, new, fromfile=str(path.relative_to(root)), tofile=str(path.relative_to(root)), lineterm=''):
                print(line)
        print(f'{len(renames)} texture(s), {len(edits)} layer(s)')
        if args.apply:
            backup = apply_plan(root, renames, edits)
            print(f'Backup: {backup}' if backup else 'Nothing to change')
        else:
            print('Analysis only; pass --apply to write changes.')
        return 0
    except (OSError, ValueError, RuntimeError, Tf.ErrorException) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]
    sys.exit(main(args))
