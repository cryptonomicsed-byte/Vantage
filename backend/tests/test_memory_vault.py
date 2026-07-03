"""OKF v0.1 conformance of the per-agent memory vault on-disk bundle."""
import json

import pytest
import yaml

from backend.memory_vault import MemoryVault, OKF_RESERVED_FILENAMES, OKF_VERSION


async def _vault_for(fresh_agent) -> MemoryVault:
    import aiosqlite
    from backend.db import DB_PATH

    agent = await fresh_agent()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT id FROM agents WHERE name=?", (agent["name"],)
        )).fetchone()
    return MemoryVault(row[0], agent["name"])


def _frontmatter(text: str) -> dict:
    assert text.startswith("---\n")
    _, fm_block, _ = text.split("---", 2)
    return yaml.safe_load(fm_block) or {}


@pytest.mark.asyncio
async def test_write_note_requires_type(fresh_agent):
    vault = await _vault_for(fresh_agent)
    with pytest.raises(AssertionError):
        vault._write_note(vault.vault_path / "knowledge" / "no_type.md", {"title": "x"}, "body")


@pytest.mark.asyncio
async def test_write_note_is_valid_yaml_even_with_colon_in_values(fresh_agent):
    """A title like 'BTC: Bullish Setup' must round-trip through a REAL YAML
    parser, not just Vantage's own hand-rolled frontmatter splitter."""
    vault = await _vault_for(fresh_agent)
    path = vault.vault_path / "knowledge" / "colon_test.md"
    vault._write_note(path, {"type": "Knowledge Triple", "title": "BTC: Bullish Setup"}, "body")
    fm = _frontmatter(path.read_text(encoding="utf-8"))
    assert fm["title"] == "BTC: Bullish Setup"
    assert fm["type"] == "Knowledge Triple"


@pytest.mark.asyncio
async def test_full_sync_produces_okf_conformant_bundle(fresh_agent):
    vault = await _vault_for(fresh_agent)
    await vault.full_sync()

    root_index = vault.vault_path / "index.md"
    log_file = vault.vault_path / "log.md"
    assert root_index.exists()
    assert log_file.exists()

    # Bundle-root index.md: only `okf_version` in frontmatter (OKF §11).
    root_fm = _frontmatter(root_index.read_text(encoding="utf-8"))
    assert root_fm == {"okf_version": OKF_VERSION}

    # log.md: newest-first dated headings, no frontmatter.
    log_text = log_file.read_text(encoding="utf-8")
    assert log_text.startswith("## ")
    assert not log_text.startswith("---")

    # Per-directory index.md files: NO frontmatter at all.
    for sub in ["broadcasts", "knowledge", "traces", "conversations", "skills", "projects", "trades"]:
        dir_index = vault.vault_path / sub / "index.md"
        assert dir_index.exists(), f"missing {sub}/index.md"
        assert not dir_index.read_text(encoding="utf-8").startswith("---")

    # Every non-reserved concept file has a parseable, non-empty `type` (OKF conformance rule).
    for md_file in vault.vault_path.rglob("*.md"):
        if md_file.name in OKF_RESERVED_FILENAMES or md_file.name == "README.md":
            continue
        if md_file.parent.name in ("workspace",) or md_file.name == "SOUL.md":
            continue  # Layer-1 workspace docs, deliberately out of scope (arbitrary OKF `type` values)
        fm = _frontmatter(md_file.read_text(encoding="utf-8"))
        assert fm.get("type"), f"{md_file} missing required OKF 'type'"

    # No stray README.md — replaced by index.md.
    assert not (vault.vault_path / "README.md").exists()


@pytest.mark.asyncio
async def test_galaxy_data_reads_node_kind_not_type(fresh_agent):
    """get_galaxy_data buckets on the `node_kind` extension key, independent
    of the real OKF `type` classification string."""
    vault = await _vault_for(fresh_agent)
    vault._write_note(
        vault.vault_path / "knowledge" / "star_test.md",
        {
            "id": "star_test", "type": "Skill", "title": "Test Star",
            "timestamp": "2026-01-01T00:00:00", "node_kind": "star",
            "galaxy_x": 1, "galaxy_y": 2, "galaxy_z": 3, "galaxy_size": 10,
            "constellation": "test",
        },
        "# Test Star",
    )
    data = vault.get_galaxy_data()
    star = next(s for s in data["stars"] if s["id"] == "star_test")
    assert star["created"] == "2026-01-01T00:00:00"  # timestamp on disk -> `created` in API output
    assert star["title"] == "Test Star"


@pytest.mark.asyncio
async def test_get_stats_counts_via_node_kind(fresh_agent):
    vault = await _vault_for(fresh_agent)
    await vault.full_sync()
    stats = await vault.get_stats()
    assert isinstance(stats["stars"], int)
    assert isinstance(stats["edges"], int)
    assert isinstance(stats["nebulae"], int)
