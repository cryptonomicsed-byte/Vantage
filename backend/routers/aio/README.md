![Version](https://img.shields.io/badge/version-v0.1.0-blue)
![License](https://img.shields.io/badge/license-BSL_1.1-orange)
![Layer](https://img.shields.io/badge/layer-Agentic-orange)

AIO is the token economy engine of the Technosis ecosystem — governing value flows, agent compensation, and sovereign economic protocols.

# AIO Phase‑1 (Secure Minimal)

**What’s here**
- Elder‑gated Mode flips (Normal/Degraded/Halted)
- Elder‑gated ActiveRoot sealing
- Bond‑gated Oracle registration (SUI bond locked in Operator object)
- Safe Escrow for arbitrary coins with correct work receipt events
- Minimal scripts + local indexer and oracle stubs

## Build

sui move build

## Deploy
1. Set elders:

export ELDER1=0x...
export ELDER2=0x...

2. Publish & init:

./scripts/deploy.sh

3. Copy shared object IDs for Config & Mode from `sui client objects` output.

## Use
- Set mode:

PKG=0x... CFG_ID=0x... MODE_ID=0x... TARGET=degraded ./scripts/set_mode.sh

- Submit root:

PKG=0x... CFG_ID=0x... ROOT_HEX=<32Bhex> COUNT=123 EPOCH=1 ./scripts/submit_root.sh

- Register oracle:

PKG=0x... MIN_BOND=1000000000 PUBKEY_HEX=<hex> ./scripts/register_oracle.sh

> **Note** Phase‑1 uses off‑chain quorum discipline. Phase‑2 should add on‑chain multisig & slashing.

## The Aṣẹ Token Economy

AIO (Aṣẹ Input/Output) defines the core tokenomics, smart contracts, and economic principles for the Technosis ecosystem. It includes the Move contracts for minting, burning, and distributing Àṣẹ and other soul-bound tokens.


---

## Part of the Technosis Sovereign Ecosystem

This component is a core piece of a larger architecture for creating and coordinating sovereign AI. For more information, see the [organism-core repository](https://github.com/Bino-Elgua/organism-core).

Àṣẹ.
