#!/usr/bin/env python3
"""
Poison Radar Daemon — on-chain poison attack scanner for Vantage.
Port of Cloakseed poisonRadar.js detection logic to Python.

Scans all tracked wallets from vantage.db for:
  - Dust attacks (< 0.001 ETH / 0.001 SOL)
  - Zero-value transfers with data (phishing/spam)
  - Suspicious token activity / unknown contracts
  - Rapid-fire transaction patterns

Posts findings as signals to /api/trading/signals/ingest with type='poison_alert'.

Usage: nohup python3 /opt/ares/poison_radar.py &
"""

import os
import sys
import time
import json
import sqlite3
import logging
import hashlib
from datetime import datetime, timezone

import requests
import urllib.parse

# ── Config ────────────────────────────────────────────────────────────────

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")
DB_PATH = os.environ.get("VANTAGE_DB", "/opt/ares/Vantage/data/vantage.db")
HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")

# Helius RPC URLs
HELIUS_SOLANA_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

# Etherscan free (no key for basic calls, but rate-limited)
ETHERSCAN_API = "https://api.etherscan.io/api"

SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "120"))  # seconds
SCAN_JITTER = int(os.environ.get("SCAN_JITTER", "15"))       # random jitter

# Cache: avoid re-alerting the same finding within CACHE_TTL seconds
CACHE_TTL = int(os.environ.get("CACHE_TTL", "3600"))  # 1 hour

# Known addresses (same as Cloakseed)
KNOWN_ADDRESSES = {
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",  # WBTC
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH
    "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
}

# Dust threshold: < 0.001 native tokens (~$1-2) = 1e15 wei / 1e6 lamports
DUST_THRESHOLD_WEI = 1_000_000_000_000_000       # 0.001 ETH in wei
DUST_THRESHOLD_LAMPORTS = 1_000_000               # 0.001 SOL in lamports

# Risk scoring thresholds (from poisonRadar.js)
RISK_DUST = 1
RISK_ZERO_VALUE_DATA = 3
RISK_UNKNOWN_CONTRACT = 2
RISK_RAPID_FIRE = 5
HIGH_RISK_THRESHOLD = 20
MEDIUM_RISK_THRESHOLD = 10

# ── Logging ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [poison_radar] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("poison_radar")

# ── Cache ─────────────────────────────────────────────────────────────────

# alert_cache: key = f"{chain}:{address}:{alert_type}", value = timestamp
alert_cache = {}

def cache_get(key):
    entry = alert_cache.get(key)
    if entry is None:
        return None
    if time.time() - entry > CACHE_TTL:
        del alert_cache[key]
        return None
    return entry

def cache_set(key):
    alert_cache[key] = time.time()
    # Prune old entries
    now = time.time()
    stale = [k for k, v in alert_cache.items() if now - v > CACHE_TTL]
    for k in stale:
        del alert_cache[k]

# ── Ethereum ──────────────────────────────────────────────────────────────

def fetch_ethereum_txs(address):
    """Fetch recent normal transactions for an ETH address using Etherscan free API."""
    try:
        params = {
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": 10,
            "sort": "desc",
        }
        url = f"{ETHERSCAN_API}?{urllib.parse.urlencode(params)}"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "1":
            # Could be rate limited or no txs
            return []
        return data.get("result", [])
    except Exception as e:
        log.warning(f"Etherscan fetch failed for {address}: {e}")
        return []

def fetch_ethereum_token_txs(address):
    """Fetch ERC-20 token transfers for an ETH address."""
    try:
        params = {
            "module": "account",
            "action": "tokentx",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": 10,
            "sort": "desc",
        }
        url = f"{ETHERSCAN_API}?{urllib.parse.urlencode(params)}"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "1":
            return []
        return data.get("result", [])
    except Exception as e:
        log.warning(f"Etherscan token fetch failed for {address}: {e}")
        return []

# ── Solana ────────────────────────────────────────────────────────────────

def fetch_solana_signatures(address):
    """Fetch recent transaction signatures for a Solana address using Helius RPC."""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [address, {"limit": 10}],
        }
        resp = requests.post(HELIUS_SOLANA_RPC, json=payload, timeout=15,
                           headers={"Content-Type": "application/json"})
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            log.warning(f"Helius RPC error for {address}: {data['error']}")
            return []
        return data.get("result", [])
    except Exception as e:
        log.warning(f"Helius fetch failed for {address}: {e}")
        return []

def fetch_solana_parsed_txs(signatures):
    """Fetch parsed transaction details for a list of Solana signatures."""
    if not signatures:
        return []
    try:
        sig_list = [s["signature"] if isinstance(s, dict) else s for s in signatures[:10]]
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [sig_list[0], {"maxSupportedTransactionVersion": 0}],
        }
        # Fetch one at a time to avoid overwhelming
        txs = []
        for sig in sig_list:
            payload["params"] = [sig, {"maxSupportedTransactionVersion": 0}]
            payload["id"] = hash(sig) & 0xFFFF
            try:
                resp = requests.post(HELIUS_SOLANA_RPC, json=payload, timeout=10,
                                   headers={"Content-Type": "application/json"})
                if resp.status_code == 200:
                    data = resp.json()
                    if "result" in data and data["result"]:
                        txs.append(data["result"])
            except Exception:
                pass
            time.sleep(0.2)  # Rate limit self
        return txs
    except Exception as e:
        log.warning(f"Solana tx detail fetch failed: {e}")
        return []

# ── Bitcoin (mempool.space) ───────────────────────────────────────────────

def fetch_bitcoin_txs(address):
    """Fetch recent transactions for a BTC address using mempool.space."""
    try:
        url = f"https://mempool.space/api/address/{address}/txs"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        txs = resp.json()
        return txs[:10] if isinstance(txs, list) else []
    except Exception as e:
        log.warning(f"Mempool fetch failed for {address}: {e}")
        return []

# ── Analysis Engine ───────────────────────────────────────────────────────

def is_known_address(addr):
    return addr.lower() in KNOWN_ADDRESSES

def analyze_ethereum_txs(normal_txs, token_txs, address):
    """Analyze Ethereum transactions for poison patterns."""
    risk_score = 0
    warnings = []
    patterns = {"dust": 0, "zeroValue": 0, "unknownContracts": 0, "rapidFire": 0, "suspiciousTokens": 0}

    # Analyze normal transactions
    for tx in normal_txs[:50]:
        value = int(tx.get("value", "0"))
        to_addr = tx.get("to", "").lower()
        input_data = tx.get("input", "0x")

        # Dust check: value > 0 but < 0.001 ETH
        if 0 < value < DUST_THRESHOLD_WEI:
            patterns["dust"] += 1
            risk_score += RISK_DUST

        # Zero-value with data (possible phishing/spam)
        if value == 0:
            patterns["zeroValue"] += 1
            if input_data and len(input_data) > 2:
                risk_score += RISK_ZERO_VALUE_DATA

        # Unknown contract interaction
        if to_addr and to_addr != address.lower() and not is_known_address(to_addr):
            patterns["unknownContracts"] += 1
            risk_score += RISK_UNKNOWN_CONTRACT

    # Analyze token transactions
    for tx in token_txs[:50]:
        token_value = int(tx.get("value", "0"))
        token_decimals = int(tx.get("tokenDecimal", "18"))
        token_symbol = tx.get("tokenSymbol", "???")
        contract_addr = tx.get("contractAddress", "").lower()

        # Dust token transfer (value < 1/1000 of unit after decimals)
        if 0 < token_value < 10 ** (token_decimals - 3):
            patterns["dust"] += 1
            risk_score += RISK_DUST

        # Unknown token contract
        if contract_addr and not is_known_address(contract_addr):
            patterns["suspiciousTokens"] += 1
            risk_score += RISK_UNKNOWN_CONTRACT

    # Rapid-fire check
    if len(normal_txs) + len(token_txs) > 50:
        patterns["rapidFire"] += 1
        risk_score += RISK_RAPID_FIRE

    # Build warnings
    if patterns["dust"] > 5:
        warnings.append(f"High dust activity ({patterns['dust']} small transfers)")
    if patterns["zeroValue"] > 10:
        warnings.append(f"Unusual zero-value transactions ({patterns['zeroValue']})")
    if patterns["unknownContracts"] > 20:
        warnings.append("Multiple unknown contract interactions")
    if patterns["suspiciousTokens"] > 5:
        warnings.append(f"Suspicious token activity ({patterns['suspiciousTokens']} unknown tokens)")
    if patterns["rapidFire"]:
        warnings.append("Rapid transaction pattern detected")

    risk = "high" if risk_score > HIGH_RISK_THRESHOLD else "medium" if risk_score > MEDIUM_RISK_THRESHOLD else "low"

    return {
        "status": "poisoned" if risk == "high" else "clean",
        "risk": risk,
        "riskScore": risk_score,
        "txCount": len(normal_txs) + len(token_txs),
        "suspiciousPatterns": patterns,
        "warnings": warnings,
        "message": ", ".join(warnings) if warnings else "Address appears clean",
    }

def analyze_solana_txs(signatures, parsed_txs, address):
    """Analyze Solana transactions for poison patterns.

    On Solana, we look for:
    - Spam tokens sent to wallet
    - Many small SOL transfers (dust)
    - High frequency of unknown program interactions
    """
    risk_score = 0
    warnings = []
    patterns = {"dust": 0, "zeroValue": 0, "unknownContracts": 0, "rapidFire": 0, "suspiciousTokens": 0}

    # Solana signatures give us a count and basic metadata
    sig_count = len(signatures)

    # Check for memo/comment spam (common dust attack vector on Solana)
    for sig in signatures[:50]:
        memo = sig.get("memo", "")
        err = sig.get("err", None)
        if memo and len(memo) > 5:
            # Memos with URLs or suspicious content
            if "http" in memo.lower() or "airdrop" in memo.lower() or "claim" in memo.lower():
                patterns["zeroValue"] += 1
                risk_score += RISK_ZERO_VALUE_DATA

    # Analyze parsed transactions for SOL transfer amounts
    for tx in parsed_txs[:10]:
        meta = tx.get("meta", {})
        pre_balances = meta.get("preBalances", [])
        post_balances = meta.get("postBalances", [])
        log_messages = meta.get("logMessages", [])

        # Check for token program interactions (spl-token)
        for log_line in log_messages:
            if "Transfer" in log_line and "spl-token" in str(log_messages).lower():
                patterns["suspiciousTokens"] += 1
                risk_score += RISK_UNKNOWN_CONTRACT
                break

        # Check SOL balance changes for dust
        for i in range(min(len(pre_balances), len(post_balances))):
            diff = abs(post_balances[i] - pre_balances[i])
            if 0 < diff < DUST_THRESHOLD_LAMPORTS:
                patterns["dust"] += 1
                risk_score += RISK_DUST
                break  # Count once per tx

    # Rapid-fire: many signatures
    if sig_count > 50:
        patterns["rapidFire"] += 1
        risk_score += RISK_RAPID_FIRE

    # Build warnings
    if patterns["dust"] > 5:
        warnings.append(f"High dust activity ({patterns['dust']} small transfers)")
    if patterns["zeroValue"] > 10:
        warnings.append(f"Unusual zero-value/spam transactions ({patterns['zeroValue']})")
    if patterns["suspiciousTokens"] > 5:
        warnings.append(f"Suspicious token activity ({patterns['suspiciousTokens']})")
    if patterns["rapidFire"]:
        warnings.append("Rapid transaction pattern detected")

    risk = "high" if risk_score > HIGH_RISK_THRESHOLD else "medium" if risk_score > MEDIUM_RISK_THRESHOLD else "low"

    return {
        "status": "poisoned" if risk == "high" else "clean",
        "risk": risk,
        "riskScore": risk_score,
        "txCount": sig_count,
        "suspiciousPatterns": patterns,
        "warnings": warnings,
        "message": ", ".join(warnings) if warnings else "Address appears clean",
    }

def analyze_bitcoin_txs(txs, address):
    """Analyze Bitcoin transactions for dust patterns."""
    risk_score = 0
    warnings = []
    patterns = {"dust": 0, "zeroValue": 0, "unknownContracts": 0, "rapidFire": 0}

    for tx in txs[:50]:
        # Check outputs for dust (very small value outputs)
        for vout in tx.get("vout", []):
            value_sats = vout.get("value", 0)
            # Dust: < 546 satoshis (standard dust limit)
            # Or very small amounts < 1000 sats
            if 0 < value_sats < 1000:
                patterns["dust"] += 1
                risk_score += RISK_DUST

        # Check for OP_RETURN spam
        for vout in tx.get("vout", []):
            if vout.get("scriptpubkey_type") == "op_return":
                patterns["zeroValue"] += 1
                risk_score += RISK_ZERO_VALUE_DATA

    if len(txs) > 50:
        patterns["rapidFire"] += 1
        risk_score += RISK_RAPID_FIRE

    if patterns["dust"] > 5:
        warnings.append(f"High dust activity ({patterns['dust']} small UTXOs)")
    if patterns["zeroValue"] > 10:
        warnings.append(f"OP_RETURN spam ({patterns['zeroValue']})")
    if patterns["rapidFire"]:
        warnings.append("Rapid transaction pattern detected")

    risk = "high" if risk_score > HIGH_RISK_THRESHOLD else "medium" if risk_score > MEDIUM_RISK_THRESHOLD else "low"

    return {
        "status": "poisoned" if risk == "high" else "clean",
        "risk": risk,
        "riskScore": risk_score,
        "txCount": len(txs),
        "suspiciousPatterns": patterns,
        "warnings": warnings,
        "message": ", ".join(warnings) if warnings else "Address appears clean",
    }

# ── Signal Ingest ─────────────────────────────────────────────────────────

def post_poison_alert(wallet_row, report):
    """Post a poison alert to Vantage trading signals."""
    chain = wallet_row[1]  # chain
    address = wallet_row[2]  # address
    label = wallet_row[3] or address[:12]

    # Only alert on medium+ risk
    if report["risk"] == "low":
        return

    cache_key = f"{chain}:{address}:poison_{report['risk']}"
    if cache_get(cache_key):
        log.debug(f"Suppressed duplicate alert for {chain}:{address}")
        return

    payload = {
        "symbol": f"{chain.upper()}:{address[:8]}",
        "direction": "NEUTRAL",
        "conviction": min(report["riskScore"] / 40.0, 1.0),  # Normalize to 0-1
        "chain": chain,
        "source": "poison_radar",
        "type": "poison_alert",
        "detail": {
            "wallet_address": address,
            "wallet_label": label,
            "chain": chain,
            "risk_level": report["risk"],
            "risk_score": report["riskScore"],
            "warnings": report["warnings"],
            "patterns": report["suspiciousPatterns"],
            "tx_count": report["txCount"],
        },
    }

    try:
        url = f"{VANTAGE_URL}/api/trading/signals/ingest"
        headers = {
            "Content-Type": "application/json",
            "X-Agent-Key": VANTAGE_KEY,
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            log.info(f"Alert posted: {chain}:{address} risk={report['risk']} score={report['riskScore']}")
            cache_set(cache_key)
            return True
        else:
            log.warning(f"Signal ingest returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.error(f"Failed to post alert for {address}: {e}")
    return False

# ── Single Address Check ──────────────────────────────────────────────────

def check_single_address(chain, address):
    """Check a single address for poison patterns (used by API endpoint)."""
    if chain.lower() in ("eth", "ethereum"):
        # Validate ETH address format
        if not (address.startswith("0x") and len(address) == 42):
            return {"error": "Invalid Ethereum address format"}

        normal_txs = fetch_ethereum_txs(address)
        token_txs = fetch_ethereum_token_txs(address)
        report = analyze_ethereum_txs(normal_txs, token_txs, address)
        return report

    elif chain.lower() in ("sol", "solana"):
        # Basic Solana address validation (base58, 32-44 chars)
        if len(address) < 32 or len(address) > 44:
            return {"error": "Invalid Solana address format"}

        signatures = fetch_solana_signatures(address)
        parsed_txs = fetch_solana_parsed_txs(signatures)
        report = analyze_solana_txs(signatures, parsed_txs, address)
        return report

    elif chain.lower() in ("btc", "bitcoin"):
        if not (len(address) >= 26 and len(address) <= 35):
            return {"error": "Invalid Bitcoin address format"}

        txs = fetch_bitcoin_txs(address)
        report = analyze_bitcoin_txs(txs, address)
        return report

    else:
        return {"error": f"Unsupported chain: {chain}"}

# ── Main Scan Loop ────────────────────────────────────────────────────────

def get_tracked_wallets():
    """Read all tracked wallets from vantage.db."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, chain, address, label FROM tracked_wallets ORDER BY chain, id"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.error(f"Failed to read tracked wallets: {e}")
        return []

def scan_wallet(wallet):
    """Scan a single wallet and post alerts if needed."""
    chain = wallet["chain"].lower()
    address = wallet["address"]

    try:
        if chain in ("eth", "ethereum"):
            normal_txs = fetch_ethereum_txs(address)
            token_txs = fetch_ethereum_token_txs(address)
            report = analyze_ethereum_txs(normal_txs, token_txs, address)

        elif chain in ("sol", "solana"):
            signatures = fetch_solana_signatures(address)
            parsed_txs = fetch_solana_parsed_txs(signatures)
            report = analyze_solana_txs(signatures, parsed_txs, address)

        elif chain in ("btc", "bitcoin"):
            txs = fetch_bitcoin_txs(address)
            report = analyze_bitcoin_txs(txs, address)

        else:
            log.debug(f"Unsupported chain {chain} for {address}, skipping")
            return

        # Use wallet row as tuple for post_poison_alert compatibility
        wallet_tuple = (wallet["id"], wallet["chain"], wallet["address"], wallet.get("label", ""))
        post_poison_alert(wallet_tuple, report)

    except Exception as e:
        log.error(f"Error scanning {chain}:{address}: {e}")

def main_loop():
    """Main scanning loop."""
    log.info("=== Poison Radar Daemon starting ===")
    log.info(f"Vantage URL: {VANTAGE_URL}")
    log.info(f"DB path: {DB_PATH}")
    log.info(f"Scan interval: {SCAN_INTERVAL}s")
    log.info(f"Helius RPC: configured")

    import random

    while True:
        try:
            wallets = get_tracked_wallets()
            log.info(f"Scanning {len(wallets)} tracked wallets...")

            for wallet in wallets:
                scan_wallet(wallet)
                # Small delay between wallets to respect rate limits
                time.sleep(0.5)

            log.info(f"Scan complete. {len(wallets)} wallets checked. "
                     f"Next scan in {SCAN_INTERVAL}s")

        except KeyboardInterrupt:
            log.info("Shutting down...")
            break
        except Exception as e:
            log.error(f"Scan loop error: {e}")

        # Sleep with jitter
        jitter = random.randint(0, SCAN_JITTER)
        time.sleep(SCAN_INTERVAL + jitter)

if __name__ == "__main__":
    main_loop()
