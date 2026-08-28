from pathlib import Path

import pytest

from app.entities import EntityCatalog, EntityManifestError


def test_unreadable_entity_manifest_raises_safe_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = tmp_path / "vault"
    manifest = vault / "_system" / "entities.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "entities:\n  alpha-entity:\n    label: Synthetic\n    flags: []\n",
        encoding="utf-8",
    )
    original_read_text = Path.read_text
    denied: list[Path] = []

    def deny_manifest(path: Path, *args, **kwargs) -> str:
        if path == manifest:
            denied.append(path)
            raise PermissionError("private marker")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_manifest)

    with pytest.raises(EntityManifestError) as raised:
        EntityCatalog.load(vault)

    assert denied == [manifest]
    assert type(raised.value) is EntityManifestError
    assert str(raised.value) == "entities manifest could not be read"
    assert "private marker" not in str(raised.value)
    assert str(manifest) not in str(raised.value)


def test_invalid_utf8_entity_manifest_raises_safe_typed_error(tmp_path: Path):
    manifest = tmp_path / "_system" / "entities.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b"\xff")

    with pytest.raises(EntityManifestError) as raised:
        EntityCatalog.load(tmp_path)

    assert type(raised.value) is EntityManifestError
    assert str(raised.value) == "entities manifest could not be read"
    assert str(manifest) not in str(raised.value)
