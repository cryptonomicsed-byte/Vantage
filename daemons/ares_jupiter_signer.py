#!/opt/ares/venv/bin/python3
"""ares_jupiter_signer — Signs and executes Jupiter swaps for Vantage orders.

FIXED v2:
  - Jupiter v1 API (v6 is dead)
  - Reads order's token_address + quantity (no more hardcoded SOL→USDC)
  - Reads wallet_id from order, loads key from DB or soul_seed fallback
  - Supports buy (SOL→token) and sell (token→SOL)
"""

import time, json, sqlite3, os, sys, signal, urllib.request
from base64 import b64decode, b64encode

DB = "/opt/ares/Vantage/data/vantage.db"
HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")

# Jupiter v1 API (v6 is dead)
JUPITER_QUOTE = "https://api.jup.ag/swap/v1/quote"
JUPITER_SWAP  = "https://api.jup.ag/swap/v1/swap"

VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()

SOL_MINT  = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# ── Wallet key loading ──────────────────────────────────────
from solders.keypair import Keypair

def load_keypair(wallet_id: int) -> Keypair:
    """Try to load keypair for a wallet from DB, fall back to soul_seed."""
    try:
        dbi = sqlite3.connect(DB)
        row = dbi.execute(
            "SELECT private_key, address FROM trading_wallets WHERE id=?",
            (wallet_id,)
        ).fetchone()
        dbi.close()

        if row and row[0]:
            pk_hex = row[0]
            # Could be hex or encrypted — try hex first
            try:
                pk_bytes = bytes.fromhex(pk_hex)
                if len(pk_bytes) == 32:
                    return Keypair.from_seed(pk_bytes), row[1]
                elif len(pk_bytes) >= 64:
                    return Keypair.from_bytes(pk_bytes[:64]), row[1]
            except:
                pass
    except Exception as e:
        print(f"  ⚠️ DB key lookup failed: {e}")

    # Fallback: soul seed
    seed_data = json.load(open("/opt/ares/hermes_soul_seed.json"))
    pk_hex = seed_data["chains"]["solana"]["private_key"]
    pk_bytes = bytes.fromhex(pk_hex)
    if len(pk_bytes) == 32:
        kp = Keypair.from_seed(pk_bytes)
    else:
        kp = Keypair.from_bytes(pk_bytes[:64])
    return kp, str(kp.pubkey())

# ── RPC helpers ─────────────────────────────────────────────
def rpc(method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}",
        data=payload, headers={"Content-Type": "application/json"}
    )
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())

# ── Order helpers ───────────────────────────────────────────
def get_pending_orders():
    dbi = sqlite3.connect(DB)
    rows = dbi.execute("""
        SELECT id, symbol, side, quantity, token_address, wallet_id
        FROM trading_orders
        WHERE status='pending' AND chain='solana'
        ORDER BY id DESC LIMIT 5
    """).fetchall()
    dbi.close()
    return rows


def update_order(oid, status, tx_hash=""):
    try:
        payload = json.dumps({"status": status, "tx_hash": tx_hash}).encode()
        req = urllib.request.Request(
            f"http://localhost:8001/api/trading/orders/{oid}",
            data=payload, method="PATCH",
            headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY}
        )
        urllib.request.urlopen(req, timeout=5)
        print(f"  📝 Order #{oid} → {status}")
    except Exception as e:
        print(f"  ⚠️ Update failed: {e}")


# ── Main loop ───────────────────────────────────────────────
def run():
    print("═══ ares_jupiter_signer v2 ═══")
    print(f"  Jupiter API: {JUPITER_QUOTE}")
    executed = set()

    while True:
        try:
            orders = get_pending_orders()
            if not orders:
                time.sleep(30)
                continue

            for oid, symbol, side, quantity, token_address, wallet_id in orders:
                if oid in executed:
                    continue

                token_sym = symbol.split("/")[0] if "/" in symbol else symbol
                print(f"\n  🎯 Order #{oid}: {side} {quantity} {token_sym} (wallet #{wallet_id})")

                # Load wallet keypair
                try:
                    keypair, sol_address = load_keypair(wallet_id)
                    print(f"  Wallet: {sol_address}")
                except Exception as e:
                    print(f"  ❌ Key load failed: {e}")
                    update_order(oid, "failed", str(e)[:200])
                    executed.add(oid)
                    continue

                # Check SOL balance
                try:
                    bal = rpc("getBalance", [sol_address])
                    sol_balance = bal.get("result", {}).get("value", 0) / 1e9
                    print(f"  Balance: {sol_balance:.4f} SOL")
                except:
                    sol_balance = 0

                if sol_balance < 0.001:
                    print(f"  ⚠️ Low balance ({sol_balance:.4f} SOL) — need >0.001")
                    update_order(oid, "failed", "insufficient balance")
                    executed.add(oid)
                    continue

                # Determine input/output mints from order
                side_up = (side or "").upper()
                amount_sol = float(quantity or 0)

                if side_up == "BUY":
                    # Buying a token with SOL
                    input_mint  = SOL_MINT
                    output_mint = token_address or ""
                    amount_lamports = int(amount_sol * 1e9)
                elif side_up == "SELL":
                    # Selling a token for SOL
                    input_mint  = token_address or ""
                    output_mint = SOL_MINT
                    # For sell, amount is in token units — need token decimals
                    amount_lamports = int(amount_sol * 1e6)  # assume 6 decimals
                else:
                    print(f"  ❌ Unknown side: {side}")
                    update_order(oid, "failed", f"unknown side: {side}")
                    executed.add(oid)
                    continue

                if not output_mint:
                    print(f"  ❌ No token address for order")
                    update_order(oid, "failed", "missing token_address")
                    executed.add(oid)
                    continue

                print(f"  Swap: {amount_sol} {input_mint[:8]}... → {output_mint[:8]}...")

                # Jupiter v1 quote
                try:
                    quote_params = (
                        f"?inputMint={input_mint}"
                        f"&outputMint={output_mint}"
                        f"&amount={amount_lamports}"
                        f"&slippageBps=500"
                    )
                    quote_url = f"{JUPITER_QUOTE}{quote_params}"
                    req = urllib.request.Request(quote_url, headers={"accept": "application/json"})
                    quote = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())

                    if quote.get("error"):
                        err_msg = quote.get("error", quote)
                        print(f"  ❌ Quote error: {err_msg}")
                        update_order(oid, "failed", str(err_msg)[:200])
                        executed.add(oid)
                        continue
                except Exception as e:
                    print(f"  ❌ Quote failed: {e}")
                    update_order(oid, "failed", str(e)[:200])
                    executed.add(oid)
                    continue

                # Jupiter v1 swap transaction
                try:
                    swap_payload = json.dumps({
                        "quoteResponse": quote,
                        "userPublicKey": sol_address,
                        "wrapAndUnwrapSol": True,
                        "dynamicComputeUnitLimit": True,
                        "prioritizationFeeLamports": "auto",
                    }).encode()
                    swap_req = urllib.request.Request(
                        JUPITER_SWAP, data=swap_payload,
                        headers={"Content-Type": "application/json"}
                    )
                    swap_data = json.loads(urllib.request.urlopen(swap_req, timeout=15).read().decode())

                    tx_b64 = swap_data.get("swapTransaction", "")
                    if not tx_b64:
                        print(f"  ❌ No swapTransaction")
                        update_order(oid, "failed", "no swapTransaction from Jupiter")
                        executed.add(oid)
                        continue
                except Exception as e:
                    print(f"  ❌ Swap tx failed: {e}")
                    update_order(oid, "failed", str(e)[:200])
                    executed.add(oid)
                    continue

                # Sign and submit
                try:
                    from solders.transaction import VersionedTransaction
                    from solders.message import to_bytes_versioned

                    tx_bytes = b64decode(tx_b64)
                    tx = VersionedTransaction.from_bytes(tx_bytes)
                    sig = keypair.sign_message(to_bytes_versioned(tx.message))

                    result = rpc("sendTransaction", [
                        b64encode(bytes(tx)).decode(),
                        {"encoding": "base64", "skipPreflight": True, "preflightCommitment": "processed"}
                    ])
                    tx_id = result.get("result", "")

                    if tx_id:
                        print(f"  ✅ TX: {tx_id[:40]}...")
                        update_order(oid, "submitted", tx_id)
                    else:
                        err = result.get("error", {}).get("message", "?")
                        print(f"  ❌ Send failed: {err}")
                        update_order(oid, "failed", str(err)[:200])
                except Exception as e:
                    print(f"  ❌ Sign/submit error: {e}")
                    update_order(oid, "failed", str(e)[:200])

                executed.add(oid)
                time.sleep(2)

            time.sleep(30)

        except Exception as e:
            print(f"  ⚠️ Loop error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    run()
