"""MoonPay on-ramp client — generates signed widget URLs so a user can buy
crypto directly with a debit card, landing in their own Vantage-managed
Solana wallet.

═══════════════════════════════════════════════════════════════════════
WHY MOONPAY (real comparison, researched 2026-08-30, not assumed)
═══════════════════════════════════════════════════════════════════════
Compared MoonPay, Transak, Coinbase Onramp, Stripe Crypto Onramp on: real
API/docs availability, who owns KYC/compliance, Solana support, widget vs
full-API integration, and fees.

  - MoonPay (chosen): dev.moonpay.com publishes a complete, exact
    HMAC-SHA256 URL-signing algorithm (see _sign_query below -- directly
    implementable, not inferred). Real sandbox environment with
    pk_test_/sk_test_ key pairs, distinct from pk_live_/sk_live_. Explicit
    Solana support: defaultCurrencyCode='sol' (native SOL) and 'usdc_sol'
    (USDC on Solana) are real, documented currency codes. Hosted widget
    (an iframe/redirect URL) -- MoonPay is the regulated money-transmitter
    of record and owns 100% of KYC/AML; Vantage only ever generates a
    signed URL and never touches card numbers, bank details, or identity
    documents. Fees: real, disclosed ~4.5% + $3.99 minimum on debit/credit
    card purchases (2026 published rate) -- shown to the user by MoonPay's
    own widget before they pay, not something Vantage marks up or hides.
  - Transak: also real and legitimate (staging environment + signed
    widget-URL API exist), a reasonable second choice, but its exact
    signing/HMAC contract required more inference from the docs available
    at research time than MoonPay's did.
  - Coinbase Onramp / Stripe Crypto Onramp: both real, but both are
    positioned around each platform's own wallet/checkout ecosystem more
    than a drop-in "any destination address" widget -- less clean a fit
    for `here is an arbitrary Solana address, sell to it` than MoonPay's
    walletAddress param.

═══════════════════════════════════════════════════════════════════════
WHAT THIS FILE DOES AND DOES NOT DO
═══════════════════════════════════════════════════════════════════════
Generates a real, correctly-signed MoonPay widget URL. Does NOT process
payments, does NOT see card details, does NOT perform or store KYC data --
all of that happens entirely inside MoonPay's own hosted widget/domain
once the user's browser navigates there. This module's only real
responsibility is the URL + its HMAC-SHA256 signature, per MoonPay's own
documented algorithm (dev.moonpay.com/docs/on-ramp-enhance-security-
using-signed-urls, verified 2026-08-30):
    signature = base64(HMAC-SHA256(secret_key, url_query_string))
where url_query_string is everything from (and including) the leading '?'
of the widget URL, with every param VALUE url-encoded before signing.

Real, honest limitation: no MoonPay account exists for this Vantage
instance as of this writing. MOONPAY_API_KEY/MOONPAY_SECRET_KEY are
unconfigured -- every function below fails soft (returns None / raises a
clear, typed error) exactly like nansen_client.py/insightx_client.py do
for their own unconfigured case, rather than fabricating a fake key or
URL. A real pk_test_/sk_test_ (sandbox) or pk_live_/sk_live_ (production)
key pair from the owner's own MoonPay dashboard signup is required before
this can be exercised end-to-end with real MoonPay infrastructure.
"""
import hashlib
import hmac
import base64
import os
from typing import Optional
from urllib.parse import urlencode, quote

MOONPAY_WIDGET_BASE_LIVE = "https://buy.moonpay.com"
MOONPAY_WIDGET_BASE_SANDBOX = "https://buy-sandbox.moonpay.com"

# Real, documented MoonPay currency codes (dev.moonpay.com, verified
# 2026-08-30) -- lowercase, exact strings MoonPay's widget expects.
SUPPORTED_CURRENCIES = {
    "SOL": "sol",         # native Solana
    "USDC_SOL": "usdc_sol",  # USDC on Solana
    "BTC": "btc",
    "ETH": "eth",
}


def _api_key() -> str:
    return os.environ.get("MOONPAY_API_KEY", "")


def _secret_key() -> str:
    return os.environ.get("MOONPAY_SECRET_KEY", "")


def is_configured() -> bool:
    return bool(_api_key() and _secret_key())


def _is_sandbox_key(key: str) -> bool:
    return key.startswith("pk_test_") or key.startswith("sk_test_")


def _sign_query(query_string: str, secret_key: str) -> str:
    """Real MoonPay signing algorithm, verified against dev.moonpay.com's
    own documented example 2026-08-30: HMAC-SHA256 over the query string
    (including the leading '?'), base64-encoded. See
    test_moonpay_client.py for a byte-exact regression test against a
    manually-computed reference vector."""
    digest = hmac.new(secret_key.encode(), query_string.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def build_onramp_url(
    wallet_address: str,
    currency: str = "SOL",
    fiat_amount: Optional[float] = None,
    fiat_currency: str = "USD",
    email: Optional[str] = None,
) -> dict:
    """Builds a real, correctly-signed MoonPay on-ramp widget URL for the
    given Solana wallet address. Raises RuntimeError (never returns a
    fake/unsigned URL) if MOONPAY_API_KEY/MOONPAY_SECRET_KEY aren't
    configured -- callers (the API route) turn that into a clear 503,
    same fail-soft-but-honest discipline as nansen_client.py.

    Returns {url, environment, currency, wallet_address} -- environment
    is 'sandbox' or 'live', inferred from which key prefix is configured,
    never guessed."""
    api_key = _api_key()
    secret_key = _secret_key()
    if not api_key or not secret_key:
        raise RuntimeError(
            "MoonPay is not configured (MOONPAY_API_KEY/MOONPAY_SECRET_KEY unset) -- "
            "a real pk_test_/sk_test_ (sandbox) or pk_live_/sk_live_ (production) key "
            "pair from a real MoonPay dashboard signup is required."
        )
    if not wallet_address or len(wallet_address) < 32:
        raise ValueError("A real Solana wallet address is required")

    currency_code = SUPPORTED_CURRENCIES.get(currency.upper())
    if not currency_code:
        raise ValueError(f"Unsupported currency {currency!r}; supported: {sorted(SUPPORTED_CURRENCIES)}")

    sandbox = _is_sandbox_key(api_key)
    base = MOONPAY_WIDGET_BASE_SANDBOX if sandbox else MOONPAY_WIDGET_BASE_LIVE

    params = {
        "apiKey": api_key,
        "currencyCode": currency_code,
        "walletAddress": wallet_address,
    }
    if fiat_amount is not None:
        params["baseCurrencyAmount"] = str(fiat_amount)
        params["baseCurrencyCode"] = fiat_currency.lower()
    if email:
        params["email"] = email

    query_string = "?" + urlencode(params)
    signature = _sign_query(query_string, secret_key)
    # signature is base64 (contains '+', '/', '=') -- url-encode it before
    # appending, matching MoonPay's own documented example exactly.
    signed_url = f"{base}{query_string}&signature={quote(signature)}"

    return {
        "url": signed_url,
        "environment": "sandbox" if sandbox else "live",
        "currency": currency_code,
        "wallet_address": wallet_address,
    }
