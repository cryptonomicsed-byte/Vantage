"""Ifá odu cast — pure-Python port of BIPỌ̀N39's Ifáscript odu lookup.

Why a port instead of a call to the Rust crate: Bipon39-Rust is a library, not a
daemon, and the trading/eco decision path cannot take a build dependency on
cargo at request time. The functions ported here are small, pure, and fully
specified by the crate's own tests, so the port is verifiable without running
Rust (see tests/test_odu_cast.py, which replays the crate's golden vectors).

Ported 1:1 from Bipon39-Rust:
  bipon39::mnemonic::entropy_to_mnemonic   (byte k -> flat index k+1 -> token)
  bipon39::dualmode::mode_index_of_token   (256-mode + 2048-mode "base~subtone")
  bipon39::ifascript::odu_primary_index    (XOR-reduce token indices, mod 256)

Consequence worth knowing: one byte of entropy maps to one word maps to one
token index, so for a 32-byte seed the odu index is exactly the XOR-fold of the
seed bytes. The mnemonic is the human/audit-facing rendering, not extra mixing.

Divination layer (NOT in the Rust crate — defined here):
  cast() derives one 32-byte seed per leg from (seed_material, nonce), renders
  each as a 32-word mnemonic, and resolves both signs against the 256-Odu
  table. Two legs is the Ifá idiom; a single leg is not a casting.
  gate() turns the pair into allow / escalate / veto for a notional at risk.

Everything is deterministic: same (seed_material, nonce) -> same cast_id, so a
receipt written at decision time can be re-cast and checked later.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

def _resolve_data_dir() -> Path:
    """Find the data dir whether we run from a source checkout or an install.

    The JSON is not shipped as package-data (it is read from the repo-root
    backend/data at runtime), so a non-editable install must still resolve
    against the working tree.
    """
    override = os.environ.get("ODU_DATA_DIR")
    if override:
        return Path(override)
    here = Path(__file__).resolve().parent / "data"
    if (here / "odu_256.json").exists():
        return here
    for base in (Path.cwd(), Path.cwd().parent):
        for candidate in (base / "backend" / "data", base / "data"):
            if (candidate / "odu_256.json").exists():
                return candidate
    return here  # let the open() raise with a path that names the problem


DATA_DIR = _resolve_data_dir()
CANONICAL_FILE = DATA_DIR / "bipon39_canonical.json"
ODU_FILE = DATA_DIR / "odu_256.json"

SUBTONES = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta")

# Stake bands (USD notional). Below soft cap, ambiguity is tolerated; above it,
# ambiguity is routed to a human; above hard cap, only a clean principal pair
# proceeds without review.
STAKE_SOFT_CAP = float(os.environ.get("ODU_STAKE_SOFT_CAP_USD", "250"))
STAKE_HARD_CAP = float(os.environ.get("ODU_STAKE_HARD_CAP_USD", "2000"))

# hermetic_gate at or above this reads as a demanding sign: force review.
GATE_STRICT_THRESHOLD = float(os.environ.get("ODU_GATE_STRICT", "12.0"))

# Hamming distance bands over the 8-bit odu patterns.
AGREE_MAX = 2      # <=: signs corroborate
CONFLICT_MIN = 5   # >=: signs contradict

_wordlist: list[str] | None = None
_encoding_to_index: dict[str, int] | None = None
_odu_table: list[dict] | None = None


def _load_wordlist() -> tuple[list[str], dict[str, int]]:
    global _wordlist, _encoding_to_index
    if _wordlist is None or _encoding_to_index is None:
        with open(CANONICAL_FILE, encoding="utf-8") as fh:
            doc = json.load(fh)
        entries = sorted(doc["flat_index"], key=lambda e: e["index"])
        if len(entries) != 256:
            raise ValueError(f"expected 256 canonical tokens, got {len(entries)}")
        _wordlist = [e["encoding"] for e in entries]
        _encoding_to_index = {
            e["encoding"]: int(e.get("token_meta", {}).get("id", e["index"] - 1))
            for e in entries
        }
        # token_meta.id is the 0-based array index; assert the two views agree.
        for pos, e in enumerate(entries):
            if _encoding_to_index[e["encoding"]] != pos:
                raise ValueError(f"canonical index drift at {e['encoding']}")
    return _wordlist, _encoding_to_index


def _load_odu_table() -> list[dict]:
    global _odu_table
    if _odu_table is None:
        with open(ODU_FILE, encoding="utf-8") as fh:
            table = json.load(fh)
        if len(table) != 256:
            raise ValueError(f"expected 256 odu, got {len(table)}")
        _odu_table = sorted(table, key=lambda o: o["index"])
    return _odu_table


def token_index(token: str) -> int:
    """Mode index for a 256-mode token or a 2048-mode 'base~subtone' token."""
    _, index = _load_wordlist()
    base, sep, subtone = token.partition("~")
    if base not in index:
        raise KeyError(f"unknown BIPỌ̀N39 token: {token!r}")
    base_index = index[base]
    if not sep:
        return base_index
    if subtone not in SUBTONES:
        raise KeyError(f"unknown subtone in token: {token!r}")
    return (base_index << 3) | SUBTONES.index(subtone)


def entropy_to_mnemonic(entropy: bytes) -> list[str]:
    """Bytes -> words. One byte per word, flat index = byte + 1."""
    words, _ = _load_wordlist()
    if not entropy:
        raise ValueError("entropy must be non-empty")
    return [words[b] for b in entropy]


def odu_primary_index(words: list[str]) -> int:
    """XOR-reduce token indices mod 256 (u8 truncation, as in the crate)."""
    value = 0
    for word in words:
        value ^= token_index(word) & 0xFF
    return value


def xor_fold(data: bytes) -> int:
    """XOR-fold raw bytes — equal to odu_primary_index(entropy_to_mnemonic(data))."""
    value = 0
    for b in data:
        value ^= b
    return value


@dataclass
class CastLeg:
    """One Ifá leg: seed -> mnemonic -> odu sign."""

    odu: dict
    mnemonic: list[str]
    entropy_hex: str

    @property
    def index(self) -> int:
        return int(self.odu["index"])

    @property
    def binary(self) -> str:
        return str(self.odu["binary_string"])

    @property
    def tier(self) -> int:
        return int(self.odu.get("tier", 2))

    @property
    def hermetic_gate(self) -> float:
        try:
            return float(self.odu.get("hermetic_gate", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def to_dict(self) -> dict:
        return {
            "odu_index": self.index,
            "sacred_name": self.odu.get("sacred_name"),
            "translation": self.odu.get("translation"),
            "binary": self.binary,
            "mother": self.odu.get("mother"),
            "child": self.odu.get("child"),
            "tier": self.tier,
            "hermetic_gate": self.hermetic_gate,
            "orisha_vector": self.odu.get("orisha_vector"),
            "prescription": self.odu.get("prescription"),
            "mnemonic": " ".join(self.mnemonic),
            "entropy_hex": self.entropy_hex,
        }


@dataclass
class OduCast:
    """A two-leg casting."""

    primary: CastLeg
    corroborating: CastLeg
    cast_id: str
    seed_material: str
    nonce: str

    @property
    def hamming(self) -> int:
        a, b = int(self.primary.binary, 2), int(self.corroborating.binary, 2)
        return bin(a ^ b).count("1")

    @property
    def principal(self) -> bool:
        """Both legs are among the 16 principal odus (tier 1)."""
        return self.primary.tier == 1 and self.corroborating.tier == 1

    @property
    def mean_gate(self) -> float:
        return (self.primary.hermetic_gate + self.corroborating.hermetic_gate) / 2.0

    def to_dict(self) -> dict:
        return {
            "cast_id": self.cast_id,
            "seed_material": self.seed_material,
            "nonce": self.nonce,
            "hamming": self.hamming,
            "principal": self.principal,
            "mean_hermetic_gate": round(self.mean_gate, 3),
            "primary": self.primary.to_dict(),
            "corroborating": self.corroborating.to_dict(),
        }


@dataclass
class GateVerdict:
    """Policy outcome for a casting in the presence of a stake."""

    decision: str  # allow | escalate | veto
    reason: str
    stake_usd: float
    hamming: int
    principal: bool
    cast_id: str
    primary: dict = field(default_factory=dict)
    corroborating: dict = field(default_factory=dict)
    prescription: str = ""

    @property
    def blocked(self) -> bool:
        return self.decision == "veto"

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "blocked": self.blocked,
            "reason": self.reason,
            "stake_usd": self.stake_usd,
            "hamming": self.hamming,
            "principal": self.principal,
            "cast_id": self.cast_id,
            "primary": self.primary,
            "corroborating": self.corroborating,
            "prescription": self.prescription,
        }


def _leg(seed_material: str, nonce: str, leg: int) -> CastLeg:
    material = f"{seed_material}|{nonce}|leg{leg}".encode("utf-8")
    entropy = hashlib.sha256(material).digest()
    words = entropy_to_mnemonic(entropy)
    index = odu_primary_index(words)
    table = _load_odu_table()
    # odu_primary_index is 0-based; the table is 1-based over the same 256 signs.
    return CastLeg(odu=table[index], mnemonic=words, entropy_hex=entropy.hex())


def cast(seed_material: str, nonce: str = "") -> OduCast:
    """Cast both legs. Deterministic in (seed_material, nonce)."""
    if not seed_material:
        raise ValueError("seed_material is required")
    primary = _leg(seed_material, nonce, 1)
    corroborating = _leg(seed_material, nonce, 2)
    cast_id = hashlib.sha256(
        f"{seed_material}|{nonce}|{primary.index}|{corroborating.index}".encode("utf-8")
    ).hexdigest()[:16]
    return OduCast(
        primary=primary,
        corroborating=corroborating,
        cast_id=cast_id,
        seed_material=seed_material,
        nonce=nonce,
    )


def gate(casting: OduCast, stake_usd: float = 0.0) -> GateVerdict:
    """Route a casting to allow / escalate / veto for the notional at risk.

    Rules, in order:
      both principal + agreeing (hamming <= 2)          -> allow (any stake)
      agreeing, stake <= soft cap                       -> allow
      agreeing, stake  > soft cap                       -> escalate
      contradicting (hamming >= 5) and stake >= soft    -> veto
      contradicting and stake < soft cap                -> escalate
      ambiguous (3-4) and stake >= soft cap             -> escalate
      ambiguous and stake < soft cap                    -> allow
      mean hermetic_gate >= GATE_STRICT_THRESHOLD       -> never allow
      stake > hard cap                                  -> never allow
    """
    stake = max(0.0, float(stake_usd or 0.0))
    hamming = casting.hamming
    principal = casting.principal
    strict = casting.mean_gate >= GATE_STRICT_THRESHOLD
    above_soft = stake >= STAKE_SOFT_CAP
    above_hard = stake >= STAKE_HARD_CAP

    if hamming <= AGREE_MAX:
        if principal and not above_hard:
            decision, reason = "allow", "principal pair, signs corroborate"
        elif not above_soft and not strict and not above_hard:
            decision, reason = "allow", "signs corroborate under soft cap"
        else:
            decision, reason = "escalate", (
                f"signs corroborate but stake ${stake:,.0f} or gate "
                f"{casting.mean_gate:.1f} requires review"
            )
    elif hamming >= CONFLICT_MIN:
        if above_soft:
            decision, reason = "veto", f"signs contradict (hamming={hamming}) with ${stake:,.0f} at risk"
        else:
            decision, reason = "escalate", f"signs contradict (hamming={hamming}) at low stake"
    else:
        if above_soft:
            decision, reason = "escalate", f"ambiguous signs (hamming={hamming}) with ${stake:,.0f} at risk"
        else:
            decision, reason = "allow", f"ambiguous signs (hamming={hamming}) under soft cap"

    if decision != "veto" and strict:
        decision = "escalate"
        reason = f"{reason}; hermetic_gate {casting.mean_gate:.1f} >= {GATE_STRICT_THRESHOLD}"

    return GateVerdict(
        decision=decision,
        reason=reason,
        stake_usd=stake,
        hamming=hamming,
        principal=principal,
        cast_id=casting.cast_id,
        primary=casting.primary.to_dict(),
        corroborating=casting.corroborating.to_dict(),
        prescription=casting.primary.odu.get("prescription", ""),
    )
