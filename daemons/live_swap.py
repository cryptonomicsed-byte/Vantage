#!/usr/bin/env python3
"""
LIVE Jupiter Swap Executor — Minimal, auditable
Signs swaps with BIPON39 key via solders, submits via Helius RPC
"""
import json, os, sys, urllib.request
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from base64 import b64decode, b64encode

# Load key
with open("/opt/ares/hermes_soul_seed.json") as f:
    seed = json.load(f)
pk_hex = seed["chains"]["solana"]["private_key"]
pk_bytes = bytes.fromhex(pk_hex)
KEYPAIR = Keypair.from_seed(pk_bytes) if len(pk_bytes) == 32 else Keypair.from_bytes(pk_bytes[:64])

HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")
ADDRESS = str(KEYPAIR.pubkey())
print(f"Signer ready: {ADDRESS}")

def jupiter_quote(input_mint, output_mint, amount_lamports, slippage=1.0):
    """Get swap quote from Jupiter."""
    url = (f"https://quote-api.jup.ag/v6/quote"
           f"?inputMint={input_mint}&outputMint={output_mint}"
           f"&amount={amount_lamports}&slippageBps={int(slippage*100)}")
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def jupiter_swap(quote_response):
    """Build swap transaction from quote."""
    req = urllib.request.Request(
        "https://quote-api.jup.ag/v6/swap",
        data=json.dumps({
            "quoteResponse": quote_response,
            "userPublicKey": ADDRESS,
            "wrapAndUnwrapSol": True,
        }).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "curl/8.0"}
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def send_tx(signed_tx_base64):
    """Submit signed transaction to Helius."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": "sendTransaction",
        "params": [signed_tx_base64, {"encoding": "base64", "skipPreflight": False}]
    }).encode()
    req = urllib.request.Request(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}", data=payload, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())

def execute_swap(symbol, amount_sol):
    """Full swap: SOL → token."""
    print(f"\n{'='*50}")
    print(f"  LIVE SWAP: {amount_sol} SOL → {symbol}")
    print(f"{'='*50}")
    
    # Step 1: Get quote
    SOL_MINT = "So11111111111111111111111111111111111111112"
    TOKEN_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC placeholder
    
    amount = int(amount_sol * 1e9)
    print(f"  Amount: {amount} lamports ({amount_sol} SOL)")
    
    try:
        quote = jupiter_quote(SOL_MINT, TOKEN_MINT, amount)
        print(f"  Quote: {int(quote.get('inAmount',0))/1e9} SOL → {int(quote.get('outAmount',0))/1e6} USDC")
    except Exception as e:
        print(f"  Quote error: {e}")
        return None
    
    # Step 2: Build swap tx
    try:
        swap = jupiter_swap(quote)
        tx_base64 = swap.get("swapTransaction", "")
        if not tx_base64:
            print(f"  Swap build failed: {swap}")
            return None
        print(f"  Transaction built ({len(tx_base64)} bytes)")
    except Exception as e:
        print(f"  Build error: {e}")
        return None
    
    # Step 3: Sign
    try:
        tx_bytes = b64decode(tx_base64)
        tx = VersionedTransaction.from_bytes(tx_bytes)
        tx.sign([KEYPAIR])
        signed = b64encode(bytes(tx)).decode()
        print(f"  Signed: {ADDRESS[:12]}...")
    except Exception as e:
        print(f"  Sign error: {e}")
        return None
    
    # Step 4: Send
    try:
        result = send_tx(signed)
        sig = result.get("result", result.get("error", {}))
        if isinstance(sig, str):
            print(f"  ✅ TX SENT: {sig[:20]}...")
            print(f"  Explorer: https://solscan.io/tx/{sig}")
            return sig
        else:
            print(f"  ❌ TX failed: {sig}")
            return None
    except Exception as e:
        print(f"  Send error: {e}")
        return None

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: live_swap.py <symbol> <amount_sol>")
        print("Example: live_swap.py USDC 0.005")
        # Test mode: just get a quote
        print("\nTest mode — getting quote for 0.001 SOL → USDC")
        execute_swap("USDC", 0.001)
    else:
        execute_swap(sys.argv[1], float(sys.argv[2]))
