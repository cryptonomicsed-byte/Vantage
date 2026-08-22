"""BIP-39 / BIP-32 multi-chain HD wallet derivation.

Ports the same scheme ~/vanity-cloakseed/src/utils/hdWallet.ts +
chains.ts uses: one BIP-39 mnemonic, one seed, deterministic per-chain
keys via each chain's BIP-44 path (Ethereum, Solana, Bitcoin, Sui,
Cosmos, Aptos -- Sui's coin type here is the same one Omo-Koda2 already
uses). One deliberate change from the reference: that tool derives every
chain (including the ed25519 ones) through a single secp256k1 BIP-32
tree, which is fine for a display-only reference tool but not for a
path that actually custodies funds. Here, ed25519 chains are derived
with real SLIP-0010 ed25519 derivation -- the same method real Solana
wallets (Phantom, Solflare) use -- via solders' own
Keypair.from_seed_and_derivation_path, not a hand-rolled implementation.

Only Solana gets a locally-held, signable keypair (that's the only chain
Vantage has signing infrastructure for today). The other chains are
derived to real, correct public addresses only -- never a private key --
purely for multi-chain identity; their private keys can be re-derived
later from the same sealed mnemonic if Vantage ever adds signing support
for them.
"""
from typing import NamedTuple

from bip_utils import (
    Bip39MnemonicGenerator,
    Bip39MnemonicValidator,
    Bip39SeedGenerator,
    Bip39WordsNum,
    Bip44,
    Bip44Coins,
    Bip84,
    Bip84Coins,
)
from solders.keypair import Keypair as SolanaKeypair

# Same path vanity-cloakseed/src/utils/chains.ts uses for Solana.
SOLANA_DERIVATION_PATH = "m/44'/501'/0'/0'"

# Other chains: address-only, derived via bip_utils' standard (complete)
# BIP-44 default path per coin -- this is one level deeper than the
# reference tool's truncated path (which stops at the change level), so
# addresses here match what a real wallet (MetaMask, Electrum, Keplr)
# would show for the same mnemonic, which is more useful than bit-for-bit
# parity with a simplified debug tool.
_OTHER_CHAIN_COINS = {
    "ethereum": Bip44Coins.ETHEREUM,
    "cosmos": Bip44Coins.COSMOS,
    "sui": Bip44Coins.SUI,
    "aptos": Bip44Coins.APTOS,
}


class MultiChainWallet(NamedTuple):
    mnemonic: str
    solana_keypair: SolanaKeypair
    chain_addresses: dict[str, str]  # chain -> public address only


def generate_mnemonic() -> str:
    """24-word BIP-39 mnemonic (256-bit entropy) -- same strength as
    vanity-cloakseed's `crypto.getRandomValues(new Uint8Array(32))`."""
    return str(Bip39MnemonicGenerator().FromWordsNumber(Bip39WordsNum.WORDS_NUM_24))


def validate_mnemonic(mnemonic: str) -> bool:
    return Bip39MnemonicValidator().IsValid(mnemonic)


def derive_solana_keypair(seed: bytes) -> SolanaKeypair:
    """Real SLIP-0010 ed25519 derivation at m/44'/501'/0'/0' via solders'
    own trusted implementation."""
    return SolanaKeypair.from_seed_and_derivation_path(seed, SOLANA_DERIVATION_PATH)


def derive_other_chain_addresses(seed: bytes) -> dict[str, str]:
    addresses: dict[str, str] = {}
    for chain, coin in _OTHER_CHAIN_COINS.items():
        try:
            ctx = Bip44.FromSeed(seed, coin).DeriveDefaultPath()
            addresses[chain] = ctx.PublicKey().ToAddress()
        except Exception:
            continue
    try:
        ctx = Bip84.FromSeed(seed, Bip84Coins.BITCOIN).DeriveDefaultPath()
        addresses["bitcoin"] = ctx.PublicKey().ToAddress()
    except Exception:
        pass
    return addresses


def derive_multichain_wallet(mnemonic: str) -> MultiChainWallet:
    """Generates the Solana signing keypair plus every other chain's
    public address from a single mnemonic. Deterministic: the same
    mnemonic always yields the same wallet."""
    seed_bytes = Bip39SeedGenerator(mnemonic).Generate()
    solana_keypair = derive_solana_keypair(seed_bytes)
    chain_addresses = derive_other_chain_addresses(seed_bytes)
    return MultiChainWallet(mnemonic=mnemonic, solana_keypair=solana_keypair, chain_addresses=chain_addresses)
