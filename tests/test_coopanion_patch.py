"""Local speech patches must not modify a package store through hardlinks."""
import importlib.util
from pathlib import Path
import sys

import pytest


PATCH = Path(__file__).resolve().parents[1] / 'tools/patch_coopanion_chat.py'
spec = importlib.util.spec_from_file_location('coopanion_chat_patch', PATCH)
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def test_patch_breaks_hardlinks_and_is_idempotent(tmp_path, monkeypatch):
    source = tmp_path / 'resources/app/node_modules/cortico-world-desktop-pet/src'
    source.mkdir(parents=True)
    original = ("const { beats, dropped } = parseScript(script, this.vocab());\n"
                "const question = typeof args.question === 'string' ? args.question.trim() : '';\n")
    store = tmp_path / 'store-world.ts'
    store.write_text(original, encoding='utf-8')
    world = source / 'world.ts'
    world.hardlink_to(store)
    extension = tmp_path / 'data/extensions/node_modules/cortico-world-dafeiyu-sync'
    extension.mkdir(parents=True)
    shared = tmp_path / 'store-index.mjs'
    shared.write_bytes(b'old extension')
    (extension / 'index.mjs').hardlink_to(shared)
    monkeypatch.setattr(sys, 'argv', [str(PATCH), str(tmp_path)])

    patch.main()
    assert store.read_text(encoding='utf-8') == original
    assert shared.read_bytes() == b'old extension'
    assert not world.samefile(store)
    assert 'shortBeats(originalBeats)' in world.read_text(encoding='utf-8')
    assert world.with_suffix('.ts.before-short-chat').read_text(encoding='utf-8') == original
    snapshot = world.read_bytes(), world.stat().st_mtime_ns
    patch.main()
    assert (world.read_bytes(), world.stat().st_mtime_ns) == snapshot


def test_changed_upstream_fails_without_modifying_source(tmp_path, monkeypatch):
    source = tmp_path / 'resources/app/node_modules/cortico-world-desktop-pet/src'
    source.mkdir(parents=True)
    world = source / 'world.ts'
    world.write_bytes(b'new upstream layout')
    monkeypatch.setattr(sys, 'argv', [str(PATCH), str(tmp_path)])
    with pytest.raises(ValueError, match='Upstream changed'):
        patch.main()
    assert world.read_bytes() == b'new upstream layout'
