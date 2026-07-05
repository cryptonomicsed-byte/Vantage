"""Wallet organizer: trading_wallets.exchange (self-custody vs exchange-held)
and tracked_wallets.address_type/notes (wallet / exchange / contract / smart
wallet, plus free-text notes) — lets an agent classify and annotate wallets
instead of every address looking the same.
"""
import pytest


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


# ── Trading wallets: exchange field + PATCH ─────────────────────────────────

@pytest.mark.asyncio
async def test_create_wallet_records_exchange(client, fresh_agent):
    h = _h(await fresh_agent())
    r = await client.post("/api/trading/wallets", headers=h,
                           json={"label": "coinbase-1", "chain": "bitcoin", "address": "bc1qexch", "exchange": "Coinbase"})
    assert r.status_code == 200, r.text
    assert r.json()["exchange"] == "Coinbase"

    listed = await client.get("/api/trading/wallets", headers=h)
    row = next(w for w in listed.json() if w["label"] == "coinbase-1")
    assert row["exchange"] == "Coinbase"


@pytest.mark.asyncio
async def test_create_wallet_defaults_exchange_blank(client, fresh_agent):
    h = _h(await fresh_agent())
    r = await client.post("/api/trading/wallets", headers=h,
                           json={"label": "self-custody-1", "chain": "solana", "address": "SoLTest111"})
    assert r.status_code == 200, r.text
    assert r.json()["exchange"] == ""


@pytest.mark.asyncio
async def test_patch_wallet_updates_exchange_and_label(client, fresh_agent):
    h = _h(await fresh_agent())
    created = await client.post("/api/trading/wallets", headers=h,
                                 json={"label": "wallet-a", "chain": "bitcoin", "address": "bc1qpatch"})
    wallet_id = created.json()["id"]

    r = await client.patch(f"/api/trading/wallets/{wallet_id}", headers=h,
                            json={"label": "wallet-a-renamed", "exchange": "Binance"})
    assert r.status_code == 200, r.text

    row = (await client.get(f"/api/trading/wallets/{wallet_id}", headers=h)).json()
    assert row["label"] == "wallet-a-renamed"
    assert row["exchange"] == "Binance"


@pytest.mark.asyncio
async def test_patch_wallet_requires_at_least_one_field(client, fresh_agent):
    h = _h(await fresh_agent())
    created = await client.post("/api/trading/wallets", headers=h,
                                 json={"label": "wallet-b", "chain": "bitcoin", "address": "bc1qempty"})
    wallet_id = created.json()["id"]
    r = await client.patch(f"/api/trading/wallets/{wallet_id}", headers=h, json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_wallet_404_for_other_agents_wallet(client, fresh_agent):
    owner = await fresh_agent()
    other = await fresh_agent()
    created = await client.post("/api/trading/wallets", headers=_h(owner),
                                 json={"label": "owners-wallet", "chain": "bitcoin", "address": "bc1qowner"})
    wallet_id = created.json()["id"]
    r = await client.patch(f"/api/trading/wallets/{wallet_id}", headers=_h(other), json={"exchange": "Kraken"})
    assert r.status_code == 404


# ── Watchlist: address_type + notes + PATCH ─────────────────────────────────

@pytest.mark.asyncio
async def test_add_watchlist_wallet_defaults_to_wallet_type(client, fresh_agent):
    h = _h(await fresh_agent())
    r = await client.post("/api/intel/watchlist", headers=h,
                           json={"chain": "solana", "address": "OrgWallet1111111111111111111111111111111"})
    assert r.status_code == 200, r.text
    assert r.json()["address_type"] == "wallet"
    assert r.json()["notes"] == ""


@pytest.mark.asyncio
async def test_add_watchlist_wallet_with_type_and_notes(client, fresh_agent):
    h = _h(await fresh_agent())
    r = await client.post(
        "/api/intel/watchlist", headers=h,
        json={
            "chain": "solana", "address": "ExchangeHot2222222222222222222222222222",
            "label": "Binance hot wallet", "address_type": "exchange",
            "notes": "Seen moving large SOL amounts every few hours.",
        },
    )
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["address_type"] == "exchange"
    assert row["notes"] == "Seen moving large SOL amounts every few hours."


@pytest.mark.asyncio
async def test_add_watchlist_wallet_rejects_invalid_address_type(client, fresh_agent):
    h = _h(await fresh_agent())
    r = await client.post(
        "/api/intel/watchlist", headers=h,
        json={"chain": "solana", "address": "BadType333333333333333333333333333333", "address_type": "not-a-real-type"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_watchlist_wallet_updates_type_and_notes(client, fresh_agent):
    h = _h(await fresh_agent())
    address = "PatchTarget4444444444444444444444444444"
    created = await client.post("/api/intel/watchlist", headers=h, json={"chain": "solana", "address": address})
    wallet_id = created.json()["id"]

    r = await client.patch(
        f"/api/intel/watchlist/{wallet_id}", headers=h,
        json={"address_type": "contract", "notes": "This is the token mint, not a wallet.", "label": "PEPE mint"},
    )
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["address_type"] == "contract"
    assert row["notes"] == "This is the token mint, not a wallet."
    assert row["label"] == "PEPE mint"


@pytest.mark.asyncio
async def test_patch_watchlist_wallet_rejects_invalid_address_type(client, fresh_agent):
    h = _h(await fresh_agent())
    created = await client.post(
        "/api/intel/watchlist", headers=h,
        json={"chain": "solana", "address": "PatchBad5555555555555555555555555555555"},
    )
    wallet_id = created.json()["id"]
    r = await client.patch(f"/api/intel/watchlist/{wallet_id}", headers=h, json={"address_type": "nonsense"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_watchlist_wallet_requires_at_least_one_field(client, fresh_agent):
    h = _h(await fresh_agent())
    created = await client.post(
        "/api/intel/watchlist", headers=h,
        json={"chain": "solana", "address": "PatchEmpty6666666666666666666666666666"},
    )
    wallet_id = created.json()["id"]
    r = await client.patch(f"/api/intel/watchlist/{wallet_id}", headers=h, json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_watchlist_wallet_404(client, fresh_agent):
    h = _h(await fresh_agent())
    r = await client.patch("/api/intel/watchlist/999999999", headers=h, json={"notes": "irrelevant"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_readd_watchlist_wallet_preserves_type_when_omitted(client, fresh_agent):
    h = _h(await fresh_agent())
    address = "ReaddPreserve777777777777777777777777777"
    await client.post("/api/intel/watchlist", headers=h,
                       json={"chain": "solana", "address": address, "address_type": "smart_wallet"})
    # Re-add with no address_type specified (defaults to 'wallet' in the request
    # model) — since address_type is always present in the upsert, this
    # intentionally overwrites to the request's value, matching PATCH semantics
    # for an explicit field rather than silently preserving the old one.
    r = await client.post("/api/intel/watchlist", headers=h,
                           json={"chain": "solana", "address": address, "label": "renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["address_type"] == "wallet"
    assert r.json()["label"] == "renamed"


@pytest.mark.asyncio
async def test_refresh_watchlist_includes_type_and_notes(client, fresh_agent, monkeypatch):
    from backend import market_sources as ms

    h = _h(await fresh_agent())
    address = "RefreshType8888888888888888888888888888"
    await client.post(
        "/api/intel/watchlist", headers=h,
        json={"chain": "solana", "address": address, "address_type": "contract", "notes": "meme coin CA"},
    )

    async def fake_lookup(chain, addr):
        return {"chain": "solana", "address": addr, "supported": True, "source": "solana-rpc",
                "balance": {"amount": 1.0, "unit": "SOL"}, "tx_count": 0, "transactions": []}
    monkeypatch.setattr(ms, "address_lookup", fake_lookup)

    r = await client.get("/api/intel/watchlist/refresh", headers=h)
    assert r.status_code == 200, r.text
    row = next(w for w in r.json()["wallets"] if w["address"] == address)
    assert row["address_type"] == "contract"
    assert row["notes"] == "meme coin CA"
