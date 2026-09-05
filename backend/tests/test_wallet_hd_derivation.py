"""BIP-39/BIP-32 multi-chain HD wallet derivation (backend/hd_wallet.py)
and its wiring into routers/wallets.py::create_wallet -- the real
production wallet-creation path, so these assert both the crypto (real
mnemonic, deterministic derivation, correct sealing) and that existing
("legacy") wallet rows are never touched by the new scheme.
"""
import hashlib

import pytest

from backend import hd_wallet
from backend.crypto_utils import decrypt_key_for_agent, encrypt_key_for_agent
from backend.db import get_db


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


async def _agent_with_id(fresh_agent):
    agent = await fresh_agent()
    api_key_hash = hashlib.sha256(agent["api_key"].encode()).hexdigest()
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT id FROM agents WHERE api_key=?", (api_key_hash,)
        )).fetchone()
        agent["id"] = row[0]
    return agent


# ── Pure derivation logic ────────────────────────────────────────────────

def test_generate_mnemonic_is_real_bip39():
    m = hd_wallet.generate_mnemonic()
    assert len(m.split()) == 24
    assert hd_wallet.validate_mnemonic(m)


def test_generate_mnemonic_is_random_each_call():
    assert hd_wallet.generate_mnemonic() != hd_wallet.generate_mnemonic()


def test_validate_mnemonic_rejects_garbage():
    assert not hd_wallet.validate_mnemonic("not a real seed phrase at all")


def test_solana_derivation_matches_known_test_vector():
    """Regression-pins the exact derivation path/method (m/44'/501'/0'/0'
    via solders' SLIP-0010 implementation) against the standard BIP-39 test
    mnemonic -- if this ever changes, every previously-created wallet
    becomes unrecoverable from its stored mnemonic, so any accidental
    change here must fail loudly."""
    test_mnemonic = (
        "abandon abandon abandon abandon abandon abandon abandon abandon "
        "abandon abandon abandon abandon abandon abandon abandon abandon "
        "abandon abandon abandon abandon abandon abandon abandon art"
    )
    wallet = hd_wallet.derive_multichain_wallet(test_mnemonic)
    assert str(wallet.solana_keypair.pubkey()) == "3Cy3YNTFywCmxoxt8n7UH6hg6dLo5uACowX3CFceaSnx"


def test_derive_multichain_wallet_is_deterministic():
    m = hd_wallet.generate_mnemonic()
    a = hd_wallet.derive_multichain_wallet(m)
    b = hd_wallet.derive_multichain_wallet(m)
    assert str(a.solana_keypair.pubkey()) == str(b.solana_keypair.pubkey())
    assert a.chain_addresses == b.chain_addresses


def test_derive_multichain_wallet_covers_other_chains():
    m = hd_wallet.generate_mnemonic()
    wallet = hd_wallet.derive_multichain_wallet(m)
    for chain in ("ethereum", "bitcoin", "cosmos", "sui", "aptos"):
        assert chain in wallet.chain_addresses
        assert wallet.chain_addresses[chain]
    # Ethereum addresses are always 0x + 40 hex chars.
    assert wallet.chain_addresses["ethereum"].startswith("0x")
    assert len(wallet.chain_addresses["ethereum"]) == 42


def test_different_mnemonics_derive_different_wallets():
    a = hd_wallet.derive_multichain_wallet(hd_wallet.generate_mnemonic())
    b = hd_wallet.derive_multichain_wallet(hd_wallet.generate_mnemonic())
    assert str(a.solana_keypair.pubkey()) != str(b.solana_keypair.pubkey())


# ── Sealing (same AES-256-GCM path private keys already use) ────────────

def test_mnemonic_seals_and_round_trips_like_a_private_key():
    mnemonic = hd_wallet.generate_mnemonic()
    agent = {"id": 42, "api_key": "vantage_test_key_abc"}
    sealed = encrypt_key_for_agent(mnemonic, agent)
    assert mnemonic not in sealed
    assert decrypt_key_for_agent(sealed, agent) == mnemonic


def test_mnemonic_sealed_under_wrong_agent_key_fails():
    mnemonic = hd_wallet.generate_mnemonic()
    agent = {"id": 42, "api_key": "vantage_test_key_abc"}
    sealed = encrypt_key_for_agent(mnemonic, agent)
    wrong_agent = {"id": 42, "api_key": "vantage_wrong_key"}
    with pytest.raises(Exception):
        decrypt_key_for_agent(sealed, wrong_agent)


# ── End-to-end via the real HTTP endpoint ────────────────────────────────

@pytest.mark.asyncio
async def test_create_wallet_generates_real_mnemonic_and_sealed_storage(client, fresh_agent):
    agent = await _agent_with_id(fresh_agent)
    r = await client.post(
        f"/api/agents/{agent['id']}/wallets",
        headers=_h(agent),
        json={"type": "custom", "name": "hd-test-wallet"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mnemonic_stored"] is True
    assert body["mnemonic_exposed"] is False
    assert "mnemonic" not in body  # never returned over the API
    assert body["address"]
    for chain in ("ethereum", "bitcoin", "cosmos", "sui", "aptos"):
        assert chain in body["chain_addresses"]

    async with get_db() as db:
        row = await (await db.execute(
            "SELECT derivation_scheme, mnemonic_encrypted, address, chain_addresses "
            "FROM agent_wallets WHERE agent_id=? AND name='hd-test-wallet'",
            (agent["id"],),
        )).fetchone()
    assert row[0] == "bip39-slip10-multichain"
    assert row[1]  # sealed mnemonic present
    assert body["mnemonic_stored"] is True
    # Sealed value round-trips to a real, valid 24-word mnemonic that
    # actually re-derives this same wallet's address.
    decrypted = decrypt_key_for_agent(row[1], agent)
    assert hd_wallet.validate_mnemonic(decrypted)
    rederived = hd_wallet.derive_multichain_wallet(decrypted)
    assert str(rederived.solana_keypair.pubkey()) == row[2]


@pytest.mark.asyncio
async def test_create_wallet_can_still_sign_with_derived_key(client, fresh_agent):
    """The derived Solana keypair isn't just stored -- it's the same key
    /sign transacts with, same as the old bare-Keypair() wallets."""
    agent = await _agent_with_id(fresh_agent)
    created = await client.post(
        f"/api/agents/{agent['id']}/wallets",
        headers=_h(agent),
        json={"type": "custom", "name": "hd-sign-test"},
    )
    wallet_id = created.json()["wallet_id"]

    r = await client.post(
        f"/api/agents/{agent['id']}/wallets/{wallet_id}/sign",
        headers=_h(agent),
        json={"transaction": {"foo": "bar"}, "intent": "test"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["signer_address"] == created.json()["address"]


@pytest.mark.asyncio
async def test_legacy_wallet_rows_are_not_touched_by_migration(client, fresh_agent):
    """Existing rows created before this change have no mnemonic and must
    stay exactly as they were -- the schema migration is additive-only."""
    agent = await _agent_with_id(fresh_agent)
    from backend.routers.wallets import init_wallet_tables
    await init_wallet_tables()
    async with get_db() as db:
        await db.execute(
            """INSERT INTO agent_wallets (id, agent_id, type, address, private_key_encrypted, name, created_at)
               VALUES (?, ?, 'custom', 'LegacyAddr1111111111111111111111111111111', 'legacy-ciphertext', 'legacy-wallet', datetime('now'))""",
            (f"wal_legacy_{agent['id']}", agent["id"]),
        )
        await db.commit()

    r = await client.get(f"/api/agents/{agent['id']}/wallets", headers=_h(agent))
    assert r.status_code == 200, r.text
    legacy = next(w for w in r.json()["wallets"] if w["name"] == "legacy-wallet")
    assert legacy["address"] == "LegacyAddr1111111111111111111111111111111"

    async with get_db() as db:
        row = await (await db.execute(
            "SELECT derivation_scheme, mnemonic_encrypted, private_key_encrypted FROM agent_wallets WHERE id=?",
            (f"wal_legacy_{agent['id']}",),
        )).fetchone()
    assert row[0] == "legacy"  # column default, untouched
    assert row[1] is None      # no mnemonic ever existed for this row
    assert row[2] == "legacy-ciphertext"  # original private key untouched
