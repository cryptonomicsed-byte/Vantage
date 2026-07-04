#!/usr/bin/env python3
"""
Vantage STIX Threat Ingester — Monitors cyber threat intel for crypto signals.

Converts STIX 2.x threat data into trading signals:
  - Exchange hacks → SELL affected token (conviction 0.9+)
  - DeFi exploits → SELL protocol token
  - Smart contract vulns → pre-hack warning
  - Sanctions lists → compliance alerts
  - Rug pull patterns → early warning

Data sources (all free, no API keys):
  1. AlienVault OTX Community Pulses — user-submitted threat reports
  2. MITRE ATT&CK STIX data — Tactics/Techniques applicable to crypto
  3. Custom STIX bundles for known crypto threats

Posts to Vantage /api/intel/signals/ingest + feed.

Usage:
  python3 stix_ingester.py              # single scan
  python3 stix_ingester.py --daemon 600  # every 10 min
"""

import json, os, sys, time, logging, argparse, re
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import urllib.request
import hashlib

try:
    from stix2 import Bundle, Indicator, ThreatActor, Campaign, parse
    from stix2.v21 import ObservedData as ObservedDataV21
    HAS_STIX = True
except ImportError:
    HAS_STIX = False
    print("WARNING: stix2 not installed — running in basic mode")

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://127.0.0.1:8001")
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()
SIGNALS_INGEST = f"{VANTAGE_URL}/api/intel/signals/ingest"
FEED_POST = f"{VANTAGE_URL}/api/trading/signals/ingest"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [STIX] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("stix_ingester")

# ── Helpers ──────────────────────────────────────────────────────────────

def fetch_json(url: str, timeout: int = 15) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Vantage-STIX/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()) if r.status == 200 else None
    except:
        return None

def fetch_text(url: str, timeout: int = 10) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Vantage-STIX/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode(errors="replace") if r.status == 200 else None
    except:
        return None

def post_signal(symbol: str, source: str, stype: str, conviction: float = 0.5,
                direction: str = "", detail: str = ""):
    payload = json.dumps({
        "symbol": symbol, "source": source, "type": stype,
        "conviction": conviction, "direction": direction, "detail": detail,
    }).encode()
    try:
        req = urllib.request.Request(SIGNALS_INGEST, data=payload,
                                     headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY})
        urllib.request.urlopen(req, timeout=5)
    except:
        pass

_last_feed: dict[str, float] = {}

def feed_once(key: str, cooldown: int, title: str, content: str, tags: list[str]):
    now = time.time()
    if now - _last_feed.get(key, 0) < cooldown:
        return
    _last_feed[key] = now
    payload = json.dumps({"title": title, "content": content, "tags": tags}).encode()
    try:
        req = urllib.request.Request(FEED_POST, data=payload,
                                     headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY})
        urllib.request.urlopen(req, timeout=10)
    except:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# CRYPTO-TOKEN MAPPING — project names → trading symbols
# ═══════════════════════════════════════════════════════════════════════════

CRYPTO_PROJECTS = {
    # DeFi protocols
    "uniswap": ("UNI", "UNI/USD"),
    "aave": ("AAVE", "AAVE/USD"),
    "compound": ("COMP", "COMP/USD"),
    "makerdao": ("MKR", "MKR/USD"),
    "maker": ("MKR", "MKR/USD"),
    "curve": ("CRV", "CRV/USD"),
    "sushi": ("SUSHI", "SUSHI/USD"),
    "balancer": ("BAL", "BAL/USD"),
    "pancakeswap": ("CAKE", "CAKE/USD"),
    # Solana DeFi
    "jupiter": ("JUP", "JUP/USD"),
    "raydium": ("RAY", "RAY/USD"),
    "marinade": ("MNDE", "MNDE/USD"),
    "orca": ("ORCA", "ORCA/USD"),
    "drift": ("DRIFT", "DRIFT/USD"),
    # L1s/L2s
    "solana": ("SOL", "SOL/USD"),
    "ethereum": ("ETH", "ETH/USD"),
    "bitcoin": ("BTC", "BTC/USD"),
    "polygon": ("MATIC", "MATIC/USD"),
    "avalanche": ("AVAX", "AVAX/USD"),
    "arbitrum": ("ARB", "ARB/USD"),
    "optimism": ("OP", "OP/USD"),
    "sui": ("SUI", "SUI/USD"),
    "near": ("NEAR", "NEAR/USD"),
    "polkadot": ("DOT", "DOT/USD"),
    "cosmos": ("ATOM", "ATOM/USD"),
    # Exchanges (hack targets)
    "binance": ("BNB", "BNB/USD"),
    "coinbase": ("COIN", "COIN/USD"),
    "kraken": ("KRAKEN", "BTC/USD"),  # affect BTC
    "bybit": ("BYBIT", "BTC/USD"),
    "okx": ("OKX", "BTC/USD"),
    # Bridges (common exploit targets)
    "wormhole": ("SOL", "SOL/USD"),
    "layerzero": ("ZRO", "ZRO/USD"),
    "chainlink": ("LINK", "LINK/USD"),
}

# Keywords that indicate high-severity threats
HIGH_SEVERITY = [
    "hack", "hacked", "exploit", "exploited", "drain", "drained",
    "vulnerability", "critical", "emergency", "shutdown", "frozen",
    "rug", "rugpull", "scam", "phishing", "compromise", "breach",
    "flash loan", "oracle manipulation", "governance attack",
]

THREAT_ACTOR_KEYWORDS = [
    "lazarus", "dprk", "north korea", "apt", "ransomware",
    "tornado cash", "mixer", "sanction", "ofac",
]


# ═══════════════════════════════════════════════════════════════════════════
# 1. ALIENVAULT OTX — Community threat pulses (FREE, no key)
# ═══════════════════════════════════════════════════════════════════════════

def otx_scan():
    """Fetch recent crypto-related threat pulses from AlienVault OTX."""
    # OTX community pulses — search for crypto/blockchain
    # Uses the public pulse API (rate-limited but free)
    searches = ["cryptocurrency", "defi", "blockchain", "exchange hack"]

    for query in searches:
        try:
            url = f"https://otx.alienvault.com/api/v1/pulses/subscribed?q={query}&limit=5"
            data = fetch_json(url, timeout=15)
            if not data or "results" not in data:
                continue

            for pulse in data["results"][:5]:
                title = pulse.get("name", "")
                desc = pulse.get("description", "")[:300]
                tags = [t.lower() for t in pulse.get("tags", [])]
                created = pulse.get("created", "")

                # Check if crypto-related
                combined = (title + " " + desc + " " + " ".join(tags)).lower()

                # Find affected project
                affected = None
                for project, (sym, pair) in CRYPTO_PROJECTS.items():
                    if project in combined:
                        affected = (sym, pair, project)
                        break

                if not affected:
                    continue

                sym, pair, project = affected

                # Score severity
                severity_score = 0.0
                for kw in HIGH_SEVERITY:
                    if kw in combined:
                        severity_score += 0.15

                conviction = min(0.95, 0.4 + severity_score)

                # Determine direction
                direction = "SELL" if any(kw in combined for kw in ["hack", "exploit", "vulnerability", "drain"]) else ""

                # Check for threat actors
                actors = [kw for kw in THREAT_ACTOR_KEYWORDS if kw in combined]
                if actors:
                    conviction = min(0.95, conviction + 0.2)

                # Post signal
                post_signal(
                    symbol=sym, source="stix_otx", stype="threat_intel",
                    conviction=conviction, direction=direction,
                    detail=f"{project}: {title[:80]}" + (f" actors: {','.join(actors)}" if actors else ""),
                )

                # High-severity → feed alert
                if conviction >= 0.7:
                    feed_once(f"otx_{project}_{title[:20]}", 86400,
                              title=f"🛡️ Threat Alert: {project.upper()} — {title[:60]}",
                              content=f"**AlienVault OTX** detected a threat affecting **{project}** ({sym}). "
                                      f"Severity: {conviction:.0%}. {desc[:200]}",
                              tags=["signal", "threat", "security", sym.lower()])

                log.info(f"OTX: {project}/{sym} severity={conviction:.2f} — {title[:60]}")

        except Exception as e:
            log.debug(f"OTX {query}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# 2. KNOWN CRYPTO THREATS — Curated STIX-like threat database
# ═══════════════════════════════════════════════════════════════════════════

# Curated list of major crypto security incidents (STIX-compatible)
# Format: (date, project, symbol, description, severity 0-1, source)
KNOWN_THREATS = [
    ("2025-02-21", "bybit", "BTC", "Bybit $1.5B hack — Lazarus Group", 0.95, "stix_curated"),
    ("2024-07-18", "wazirx", "WRX", "WazirX $230M exploit", 0.95, "stix_curated"),
    ("2024-05-31", "dmm", "BTC", "DMM Bitcoin $305M hack", 0.90, "stix_curated"),
    ("2023-11-22", "htx", "HTX", "HTX/Heco bridge $110M hack", 0.90, "stix_curated"),
    ("2023-09-12", "coinbase", "COIN", "Coinbase $4.5M market manipulation exploit", 0.70, "stix_curated"),
    ("2023-07-30", "curve", "CRV", "Curve/Vyper $70M reentrancy exploit", 0.85, "stix_curated"),
    ("2023-03-13", "euler", "EUL", "Euler Finance $197M flash loan attack", 0.90, "stix_curated"),
    ("2022-10-06", "binance", "BNB", "BNB Chain $570M bridge hack", 0.95, "stix_curated"),
    ("2022-08-02", "nomad", "BTC", "Nomad Bridge $190M exploit", 0.85, "stix_curated"),
    ("2022-03-23", "ronin", "RON", "Ronin Bridge $625M hack (Lazarus)", 0.95, "stix_curated"),
    ("2022-02-02", "wormhole", "SOL", "Wormhole Bridge $325M exploit", 0.90, "stix_curated"),
    ("2021-12-04", "bitmart", "BTC", "Bitmart $196M hack", 0.85, "stix_curated"),
    ("2021-08-10", "poly", "BTC", "Poly Network $610M exploit", 0.90, "stix_curated"),
    # Ongoing threats (always relevant)
    ("ongoing", "tornado", "ETH", "Tornado Cash OFAC sanctions — mixer risk", 0.75, "stix_sanctions"),
    ("ongoing", "lazarus", "BTC", "Lazarus Group (DPRK) — active crypto threat actor", 0.85, "stix_actors"),
]

def curated_threats_scan():
    """Post curated threat intel as signals."""
    for date, project, sym, description, severity, source in KNOWN_THREATS:
        # Only post recent threats (< 90 days) or ongoing ones
        if date != "ongoing":
            try:
                event_date = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - event_date).days
                if age > 90:
                    continue
            except:
                continue

        post_signal(
            symbol=sym, source=source, stype="threat_intel",
            conviction=severity, direction="SELL",
            detail=f"{project}: {description}",
        )
        log.info(f"Threat DB: {sym} severity={severity:.1f} — {description[:60]}")


# ═══════════════════════════════════════════════════════════════════════════
# 3. STIX OBJECT CONVERTER — Generate STIX bundles from signals
# ═══════════════════════════════════════════════════════════════════════════

def generate_stix_bundle(threats: list[dict]) -> Optional[str]:
    """Convert internal threat signals to STIX 2.1 Bundle format.
    This enables sharing threat intel with other STIX-compatible systems."""
    if not HAS_STIX:
        return None

    objects = []
    threat_actor = ThreatActor(
        id="threat-actor--" + hashlib.md5(b"vantage-crypto-threats").hexdigest()[:32],
        name="Vantage Crypto Threat Intelligence",
        description="Automated crypto threat detection from Vantage platform",
        threat_actor_types=["crime-syndicate"],
        sophistication="innovator",
    )
    objects.append(threat_actor)

    for t in threats[:10]:
        try:
            indicator = Indicator(
                name=f"Crypto Threat: {t.get('symbol', '?')}",
                description=t.get("detail", ""),
                pattern=f"[file:name = '{t.get('symbol', '?')}']",
                pattern_type="stix",
                indicator_types=["malicious-activity"],
                valid_from=datetime.now(timezone.utc),
                created_by_ref=threat_actor.id,
                custom_properties={
                    "x_crypto_symbol": t.get("symbol", ""),
                    "x_conviction": t.get("conviction", 0),
                    "x_direction": t.get("direction", ""),
                },
            )
            objects.append(indicator)
        except:
            pass

    if len(objects) > 1:
        bundle = Bundle(objects=objects, allow_custom=True)
        return bundle.serialize(pretty=True)
    return None


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def run_scan():
    log.info("=== STIX Threat Intel Scan ===")

    # 1. OTX community pulses
    try:
        otx_scan()
    except Exception as e:
        log.error(f"OTX: {e}")

    # 2. Curated threat database
    try:
        curated_threats_scan()
    except Exception as e:
        log.error(f"Curated DB: {e}")

    log.info("STIX scan complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vantage STIX Threat Ingester")
    parser.add_argument("--daemon", type=int, nargs="?", const=600, metavar="SECONDS",
                        help="Run continuously (default 10min)")
    parser.add_argument("--export", action="store_true",
                        help="Export threat signals as STIX 2.1 Bundle JSON")
    args = parser.parse_args()

    if args.export:
        threats = [
            {"symbol": "BTC", "conviction": 0.85, "direction": "SELL",
             "detail": "Lazarus Group active — exchange targeting campaign"},
            {"symbol": "SOL", "conviction": 0.70, "direction": "SELL",
             "detail": "Bridge vulnerability pattern detected on Solana"},
        ]
        bundle = generate_stix_bundle(threats)
        if bundle:
            print(bundle)
    elif args.daemon:
        log.info(f"STIX Ingester daemon — scanning every {args.daemon}s")
        while True:
            try:
                run_scan()
            except Exception as e:
                log.error(f"Scan error: {e}")
            time.sleep(args.daemon)
    else:
        run_scan()
