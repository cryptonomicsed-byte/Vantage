"""Tests for backend/moonpay_client.py -- the real MoonPay on-ramp URL
signer. No live network calls (URL-building is pure/local); the
signature correctness is checked against MoonPay's own documented
reference example, not a self-consistency check that could pass even if
the algorithm were subtly wrong.
"""
import pytest

from backend import moonpay_client as mp


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("MOONPAY_API_KEY", raising=False)
    monkeypatch.delenv("MOONPAY_SECRET_KEY", raising=False)
    yield


def test_signature_matches_moonpays_own_documented_example():
    """Exact reference vector from dev.moonpay.com/docs/on-ramp-enhance-
    security-using-signed-urls (verified 2026-08-30) -- if MoonPay ever
    changes their algorithm this test fails loudly rather than silently
    producing wrong signatures forever."""
    query_string = "?apiKey=pk_test_key&currencyCode=eth&walletAddress=0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae"
    sig = mp._sign_query(query_string, "sk_test_key")
    assert sig == "o1eO2arzL16TstEolGakHHFa33Mb61RCTfqToRWg7PA="


def test_not_configured_raises_clear_error_not_a_fake_url(monkeypatch):
    with pytest.raises(RuntimeError, match="not configured"):
        mp.build_onramp_url("SoLWaLLetAddress1111111111111111111111111")


def test_is_configured_reflects_real_env_state(monkeypatch):
    assert mp.is_configured() is False
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_test_abc")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_test_abc")
    assert mp.is_configured() is True


def test_build_onramp_url_sandbox_key_uses_sandbox_domain(monkeypatch):
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_test_abc123")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_test_secret456")
    result = mp.build_onramp_url("SoLWaLLetAddress1111111111111111111111111", currency="SOL")
    assert result["environment"] == "sandbox"
    assert result["url"].startswith("https://buy-sandbox.moonpay.com?")
    assert "currencyCode=sol" in result["url"]
    assert "walletAddress=SoLWaLLetAddress1111111111111111111111111" in result["url"]
    assert "signature=" in result["url"]


def test_build_onramp_url_live_key_uses_live_domain(monkeypatch):
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_live_realkey")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_live_realsecret")
    result = mp.build_onramp_url("SoLWaLLetAddress1111111111111111111111111", currency="SOL")
    assert result["environment"] == "live"
    assert result["url"].startswith("https://buy.moonpay.com?")


def test_build_onramp_url_rejects_short_wallet_address(monkeypatch):
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_test_abc")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_test_abc")
    with pytest.raises(ValueError, match="wallet address"):
        mp.build_onramp_url("short")


def test_build_onramp_url_rejects_unsupported_currency(monkeypatch):
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_test_abc")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_test_abc")
    with pytest.raises(ValueError, match="Unsupported currency"):
        mp.build_onramp_url("SoLWaLLetAddress1111111111111111111111111", currency="DOGE")


def test_build_onramp_url_usdc_sol_currency_code(monkeypatch):
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_test_abc")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_test_abc")
    result = mp.build_onramp_url("SoLWaLLetAddress1111111111111111111111111", currency="USDC_SOL")
    assert "currencyCode=usdc_sol" in result["url"]


def test_build_onramp_url_includes_fiat_amount_when_given(monkeypatch):
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_test_abc")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_test_abc")
    result = mp.build_onramp_url("SoLWaLLetAddress1111111111111111111111111", fiat_amount=100.0, fiat_currency="usd")
    assert "baseCurrencyAmount=100.0" in result["url"]
    assert "baseCurrencyCode=usd" in result["url"]


def test_signature_changes_if_wallet_address_changes(monkeypatch):
    """Real regression: the signature must actually depend on the query
    contents (not a constant/hardcoded value) -- two different wallet
    addresses must produce two different signatures."""
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_test_abc")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_test_abc")
    a = mp.build_onramp_url("SoLWaLLetAddress1111111111111111111111111")
    b = mp.build_onramp_url("DifferentWallet22222222222222222222222222")
    sig_a = a["url"].split("signature=")[1]
    sig_b = b["url"].split("signature=")[1]
    assert sig_a != sig_b


def test_signature_verifiable_by_recomputation(monkeypatch):
    """End-to-end sanity: given the same secret key and the exact query
    string embedded in a generated URL, recomputing the signature
    independently must reproduce the same value MoonPay's own server
    would check on the other end."""
    monkeypatch.setenv("MOONPAY_API_KEY", "pk_test_abc")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "sk_test_secret")
    result = mp.build_onramp_url("SoLWaLLetAddress1111111111111111111111111", currency="SOL")
    url = result["url"]
    query_and_sig = url.split("?", 1)[1]
    query_part, sig_part = query_and_sig.rsplit("&signature=", 1)
    from urllib.parse import unquote
    recomputed = mp._sign_query("?" + query_part, "sk_test_secret")
    assert unquote(sig_part) == recomputed
