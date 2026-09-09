#!/usr/bin/env python3
"""Build the Russian PAK using the user's installed, matching game build."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / '.build'
LOCRES_PATH = 'Tale/Content/Localization/Game/en/Game.locres'
PATCH_PATH = Path('Tale/Content/Paks/pakchunk99-Russian_P.pak')
TOOLS = {
    'repak.zip': (
        'https://github.com/trumank/repak/releases/download/v0.2.3/repak_cli-x86_64-pc-windows-msvc.zip',
        '6720d602144d75df477a99d5bedb6ea780997546afc335901d4937cafeaa73fa',
    ),
    'UnrealLocres.exe': (
        'https://github.com/akintos/UnrealLocres/releases/download/1.1.1/UnrealLocres.exe',
        'b961927f92a8bca928d378a8430e5ea36fed300500b228b184b8d6b61ebb22a1',
    ),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_pairs)


def tokens(value: str, pattern: str) -> Counter:
    return Counter(re.findall(pattern, value))


def validate(rows: list[dict], translations: dict[str, str]) -> dict:
    keys = [row['key'] for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate source keys')
    if set(keys) != set(translations):
        missing = sorted(set(keys) - set(translations))
        extra = sorted(set(translations) - set(keys))
        raise ValueError(f'Translation keys do not match: missing={missing}, extra={extra}')
    errors = []
    unchanged = []
    newline_changes = []
    number_changes = []
    for row in rows:
        key, source = row['key'], row['source']
        target = translations[key]
        if not isinstance(target, str) or not target.strip():
            errors.append(f'{key}: empty or non-string target')
            continue
        if '\ufffd' in target or '\x00' in target:
            errors.append(f'{key}: invalid replacement/NUL character')
        for label, pattern in [('placeholders', r'\{[^{}]*\}'), ('rich text tags', r'<[^>]*>')]:
            if tokens(source, pattern) != tokens(target, pattern):
                errors.append(f'{key}: changed {label}')
        if source.count('\n') != target.count('\n'):
            newline_changes.append(key)
        clean_source = re.sub(r'<[^>]*>|\{[^{}]*\}', '', source)
        clean_target = re.sub(r'<[^>]*>|\{[^{}]*\}', '', target)
        if tokens(clean_source, r'\d+(?:[.,]\d+)*') != tokens(clean_target, r'\d+(?:[.,]\d+)*'):
            number_changes.append(key)
        if source == target:
            unchanged.append(key)
    if errors:
        raise ValueError('\n'.join(errors))
    return {
        'entries': len(rows),
        'placeholder_and_tag_errors': 0,
        'unchanged_keys': unchanged,
        'changed_newline_keys': newline_changes,
        'changed_literal_number_keys': number_changes,
    }


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding='utf-8-sig', newline='') as file:
        rows = list(csv.DictReader(file))
    # UnrealLocres' CSV reader reconstructs multiline fields with Windows CRLF.
    # Compare text using LF while still preserving every line break and character.
    for row in rows:
        for column in ('source', 'target'):
            if column in row:
                row[column] = row[column].replace('\r\n', '\n')
    return rows


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def obtain_tools() -> tuple[Path, Path]:
    tool_dir = BUILD / 'tools'
    tool_dir.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in TOOLS.items():
        path = tool_dir / name
        if not path.exists():
            print(f'Downloading {name} from its original GitHub release...', flush=True)
            request = urllib.request.Request(url, headers={'User-Agent': 'hellcard-ii-russian-build'})
            with urllib.request.urlopen(request, timeout=90) as response:
                path.write_bytes(response.read())
        if sha256(path) != expected:
            raise ValueError(f'Checksum mismatch: {name}. Do not run this file.')
    with zipfile.ZipFile(tool_dir / 'repak.zip') as archive:
        root = (tool_dir / 'repak').resolve()
        for member in archive.infolist():
            target = (root / member.filename).resolve()
            if not target.is_relative_to(root):
                raise ValueError('Unsafe archive path')
        archive.extractall(root)
    return tool_dir / 'repak/repak.exe', tool_dir / 'UnrealLocres.exe'


def main():
    global BUILD
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', required=True, type=Path, help='Installed HELLCARD II Playtest directory')
    parser.add_argument('--work-dir', type=Path, default=BUILD, help='Intermediate files and downloaded tools')
    args = parser.parse_args()
    BUILD = args.work_dir.resolve()
    info = load_json(ROOT / 'build-info.json')
    game = args.game.resolve()
    source_pak = game / 'Tale/Content/Paks/pakchunk0-Windows.pak'
    if not source_pak.is_file():
        raise ValueError(f'Game PAK not found: {source_pak}')
    repak, locres = obtain_tools()
    extract = BUILD / 'extracted'
    run(repak, 'unpack', source_pak, '-o', extract, '-i', LOCRES_PATH, '--force')
    original = extract / LOCRES_PATH
    if sha256(original) != info['source_locres_sha256']:
        raise ValueError('Game localization has changed. This translation targets Steam build ' + info['steam_build_id'])
    source_csv = BUILD / 'source.csv'
    run(locres, 'export', original, '-o', source_csv)
    rows = read_csv(source_csv)
    translations = load_json(ROOT / 'translations/ru.json')
    report = validate(rows, translations)
    translated_csv = BUILD / 'translated.csv'
    with translated_csv.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=['key', 'source', 'target'])
        writer.writeheader()
        writer.writerows(dict(row, target=translations[row['key']]) for row in rows)
    stage = BUILD / 'stage'
    destination = stage / 'Tale/Content/Localization/Game/ru/Game.locres'
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(locres, 'import', original, translated_csv, '-o', destination)
    verify_csv = BUILD / 'roundtrip.csv'
    run(locres, 'export', destination, '-o', verify_csv)
    actual = {row['key']: row['source'] for row in read_csv(verify_csv)}
    if actual != translations:
        raise ValueError('Compiled locres did not round-trip exactly')
    stage_files = [p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()]
    if stage_files != ['Tale/Content/Localization/Game/ru/Game.locres']:
        raise ValueError(f'Unexpected stage files: {stage_files}')
    patch = ROOT / PATCH_PATH
    patch.parent.mkdir(parents=True, exist_ok=True)
    run(repak, 'pack', stage, patch, '--version', 'V11')
    verify = BUILD / 'verify'
    run(repak, 'unpack', patch, '-o', verify, '--force')
    if sha256(verify / destination.relative_to(stage)) != sha256(destination):
        raise ValueError('PAK round-trip failed')
    info['patch_sha256'] = sha256(patch)
    info['translated_entries'] = report['entries']
    (ROOT / 'build-info.json').write_text(json.dumps(info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report['locres_roundtrip'] = 'pass'
    report['pak_roundtrip'] = 'pass'
    (ROOT / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Built {patch.name}: {report["entries"]} strings; SHA256 {info["patch_sha256"]}')


if __name__ == '__main__':
    main()
