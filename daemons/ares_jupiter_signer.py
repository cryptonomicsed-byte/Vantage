#!/opt/ares/venv/bin/python3
"""ares_jupiter_signer — Signs and executes Jupiter swaps using BIPON39 key.
Uses solders for Ed25519 signing + Helius RPC for submission.
"""
import time, json, sqlite3, os, sys, signal, urllib.request
from base64 import b64decode, b64encode

DB = "/opt/ares/Vantage/data/vantage.db"
HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")
JUPITER_QUOTE = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP = "https://quote-api.jup.ag/v6/swap"
JUPITER_PRICE = "https://price.jup.ag/v6/price"

VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()

# Load key from seed
from solders.keypair import Keypair
seed_data = json.load(open("/opt/ares/hermes_soul_seed.json"))
PK_HEX = seed_data["chains"]["solana"]["private_key"]
PK_BYTES = bytes.fromhex(PK_HEX)
# Handle 32-byte seed vs 64-byte keypair
if len(PK_BYTES) == 32:
    KEYPAIR = Keypair.from_seed(PK_BYTES)
elif len(PK_BYTES) >= 64:
    KEYPAIR = Keypair.from_bytes(PK_BYTES[:64])
else:
    raise ValueError(f"Unexpected key length: {len(PK_BYTES)}")
SOL_ADDRESS = str(KEYPAIR.pubkey())
print(f"═══ ares_jupiter_signer ═══")
print(f"  Wallet: {SOL_ADDRESS}")

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

def rpc(method, params):
    payload = json.dumps(dict(jsonrpc="2.0", id=1, method=method, params=params)).encode()
    req = urllib.request.Request(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}", data=payload, headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())

def get_pending_orders():
    db = sqlite3.connect(DB)
    rows = db.execute("""
        SELECT id, symbol, side FROM trading_orders 
        WHERE status='pending' AND chain='solana' 
        AND trigger_reason LIKE '%moonshot%'
        ORDER BY id DESC LIMIT 3
    """).fetchall()
    db.close()
    return rows

def update_order(oid, status, tx_hash=""):
    try:
        payload = json.dumps(dict(status=status, tx_hash=tx_hash)).encode()
        req = urllib.request.Request(
            f"http://localhost:8001/api/trading/orders/{oid}",
            data=payload, method="PATCH",
            headers={"Content-Type":"application/json","X-Agent-Key":VANTAGE_KEY}
        )
        urllib.request.urlopen(req, timeout=5)
        print(f"  📝 Order #{oid} → {status}")
    except Exception as e:
        print(f"  ⚠️ Update failed: {e}")

def get_token_mint(symbol):
    """Look up token mint from pumpfun watchlist."""
    try:
        url = f"http://localhost:8001/api/intel/pumpfun/watchlist"
        req = urllib.request.Request(url, headers={"X-Agent-Key": VANTAGE_KEY})
        tokens = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        for t in tokens.get("tokens", []):
            if symbol.upper() in (t.get("symbol","")+t.get("mint","")).upper():
                return t.get("mint", "")
    except:
        pass
    return ""

def run():
    print("  Scanning for pending moonshot orders...")
    executed = set()

    while True:
        try:
            orders = get_pending_orders()
            if not orders:
                time.sleep(30)
                continue

            for oid, symbol, side in orders:
                if oid in executed:
                    continue

                token_sym = symbol.split("/")[0] if "/" in symbol else symbol
                print(f"\n  🎯 Order #{oid}: {token_sym}")

                # Get SOL balance
                bal = rpc("getBalance", [SOL_ADDRESS])
                sol_balance = bal.get("result", {}).get("value", 0) / 1e9
                print(f"  Balance: {sol_balance:.4f} SOL")

                if sol_balance < 0.001:
                    print(f"  ⚠️ Low balance — need >0.001 SOL to cover fees")
                    executed.add(oid)
                    continue

                # Try Jupiter quote via price API (different endpoint, may work)
                try:
                    req = urllib.request.Request(
                        f"{JUPITER_PRICE}?ids={SOL_MINT}&ids={USDC_MINT}",
                        headers={"accept":"application/json"}
                    )
                    prices = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
                    sol_price = prices.get("data",{}).get(SOL_MINT,{}).get("price", 0)
                    print(f"  SOL price: ${sol_price}")
                except:
                    print(f"  ⚠️ Jupiter price API blocked, using Birdeye fallback")

                # Try swap execution
                try:
                    amount = int(0.001 * 1e9)  # 0.001 SOL
                    quote_url = f"{JUPITER_QUOTE}?inputMint={SOL_MINT}&outputMint={USDC_MINT}&amount={amount}&slippageBps=300"
                    req = urllib.request.Request(quote_url, headers={"accept":"application/json"})
                    quote = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

                    if quote.get("error"):
                        print(f"  ❌ Quote error: {quote['error']}")
                        executed.add(oid)
                        continue

                    # Get swap transaction
                    swap_payload = json.dumps(dict(
                        quoteResponse=quote,
                        userPublicKey=SOL_ADDRESS,
                        wrapAndUnwrapSol=True,
                        dynamicComputeUnitLimit=True,
                        prioritizationFeeLamports="auto",
                    )).encode()
                    swap_req = urllib.request.Request(JUPITER_SWAP, data=swap_payload, 
                                                       headers={"Content-Type":"application/json"})
                    swap_data = json.loads(urllib.request.urlopen(swap_req, timeout=10).read().decode())

                    tx_b64 = swap_data.get("swapTransaction", "")
                    if not tx_b64:
                        print(f"  ❌ No swapTransaction in response")
                        update_order(oid, "failed", "")
                        executed.add(oid)
                        continue

                    # Sign with solders
                    from solders.transaction import VersionedTransaction
                    from solders.message import to_bytes_versioned
                    tx_bytes = b64decode(tx_b64)
                    tx = VersionedTransaction.from_bytes(tx_bytes)
                    sig = KEYPAIR.sign_message(to_bytes_versioned(tx.message))
                    
                    # Submit via Helius
                    result = rpc("sendTransaction", [b64encode(bytes(tx)).decode(), {"encoding":"base64", "skipPreflight":True, "preflightCommitment":"processed"}])
                    tx_id = result.get("result", "")
                    
                    if tx_id:
                        print(f"  ✅ TX: {tx_id[:40]}...")
                        update_order(oid, "submitted", tx_id)
                    else:
                        err = result.get("error",{}).get("message","?")
                        print(f"  ❌ Send failed: {err}")
                        update_order(oid, "failed", err)

                    executed.add(oid)
                    time.sleep(2)

                except Exception as e:
                    print(f"  ❌ Swap error: {str(e)[:100]}")
                    executed.add(oid)

            time.sleep(30)

        except Exception as e:
            print(f"  ⚠️ Loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    run()
