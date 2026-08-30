"""Tests for POST /api/trading/wallets/{id}/onramp -- the MoonPay
debit-card on-ramp endpoint. Real wallet lookup + ownership scoping is
exercised through the full API; the URL-signing math itself is covered
separately in test_moonpay_client.py (byte-exact against MoonPay's own
documented reference example).
"""
import pytest

from backend import moonpay_client as mp


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("MOONPAY_API_KEY", raising=False)
    monkeypatch.delenv("MOONPAY_SECRET_KEY", raising=False)
    yield


@pytest.mark.asyncio
async def test_onramp_503_when_moonpay_not_configured(client, fresh_agent):
    """Real, honest failure -- never a fake/unsigned URL when unconfigured."""
    h = _h(await fresh_agent())
    rw = await client.post("/api/trading/wallets", headers=h,
                            json={"label": "sol-1", "chain": "solana", "address": "So" + "1" * 42})
    wallet_id = rw.json()["id"]

    r = await client.post(f"/api/trading/wallets/{wallet_id}/onramp", headers=h)
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_onramp_returns_real_signed_url_when_configured(client, fresh_agent, monkeypatch):
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_test_realkey")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_test_realsecret")
    h = _h(await fresh_agent())
    real_address = "So11111111111111111111111111111111111111112"
    rw = await client.post("/api/trading/wallets", headers=h,
                            json={"label": "sol-1", "chain": "solana", "address": real_address})
    wallet_id = rw.json()["id"]

    r = await client.post(f"/api/trading/wallets/{wallet_id}/onramp", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["wallet_id"] == wallet_id
    assert d["wallet_address"] == real_address
    assert d["environment"] == "sandbox"
    assert d["url"].startswith("https://buy-sandbox.moonpay.com?")
    assert f"walletAddress={real_address}" in d["url"]
    assert "signature=" in d["url"]

    # The returned signature must be independently recomputable -- proves
    # this isn't a hardcoded/fake signature string.
    query_and_sig = d["url"].split("?", 1)[1]
    query_part, sig_part = query_and_sig.rsplit("&signature=", 1)
    from urllib.parse import unquote
    assert unquote(sig_part) == mp._sign_query("?" + query_part, "sk_test_realsecret")


@pytest.mark.asyncio
async def test_onramp_rejects_non_solana_wallet(client, fresh_agent, monkeypatch):
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_test_realkey")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_test_realsecret")
    h = _h(await fresh_agent())
    rw = await client.post("/api/trading/wallets", headers=h,
                            json={"label": "btc-1", "chain": "bitcoin", "address": "bc1qtest"})
    wallet_id = rw.json()["id"]

    r = await client.post(f"/api/trading/wallets/{wallet_id}/onramp", headers=h)
    assert r.status_code == 400
    assert "solana" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_onramp_404_for_missing_wallet(client, registered_agent):
    r = await client.post("/api/trading/wallets/999999/onramp", headers=_h(registered_agent))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_onramp_404_for_another_agents_wallet(client, fresh_agent, monkeypatch):
    """Ownership-scoped like every other wallet endpoint -- agent A cannot
    generate an on-ramp URL for agent B's wallet."""
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_test_realkey")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_test_realsecret")
    a = await fresh_agent()
    b = await fresh_agent()
    rw = await client.post("/api/trading/wallets", headers=_h(a),
                            json={"label": "sol-1", "chain": "solana", "address": "So" + "1" * 42})
    wallet_id = rw.json()["id"]

    r = await client.post(f"/api/trading/wallets/{wallet_id}/onramp", headers=_h(b))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_onramp_custom_currency_and_fiat_amount(client, fresh_agent, monkeypatch):
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_test_realkey")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_test_realsecret")
    h = _h(await fresh_agent())
    rw = await client.post("/api/trading/wallets", headers=h,
                            json={"label": "sol-1", "chain": "solana", "address": "So" + "1" * 42})
    wallet_id = rw.json()["id"]

    r = await client.post(
        f"/api/trading/wallets/{wallet_id}/onramp?currency=USDC_SOL&fiat_amount=250&fiat_currency=eur",
        headers=h,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert "currencyCode=usdc_sol" in d["url"]
    assert "baseCurrencyAmount=250" in d["url"]
    assert "baseCurrencyCode=eur" in d["url"]


@pytest.mark.asyncio
async def test_onramp_requires_agent_key(client, fresh_agent, monkeypatch):
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_test_realkey")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_test_realsecret")
    h = _h(await fresh_agent())
    rw = await client.post("/api/trading/wallets", headers=h,
                            json={"label": "sol-1", "chain": "solana", "address": "So" + "1" * 42})
    wallet_id = rw.json()["id"]

    r = await client.post(f"/api/trading/wallets/{wallet_id}/onramp")
    assert r.status_code in (401, 403)
