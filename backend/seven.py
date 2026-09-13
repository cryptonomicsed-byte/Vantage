# Vantage/backend/seven.py
#
# Universal Seven Functions Protocol (USF-7) — Vantage implementation
#
# Python port of the canonical USF-7 substrate defined in omokoda-hermetic.
# Vantage is the real-world execution layer of the three-pillar ecosystem;
# the SevenFunctions here govern how receipts are tagged, how agent identity
# is displayed to the world, and what cultural tradition a user interface uses.
#
# Layer topology (Vantage role):
#   SOURCE/OCEAN    → latent possibility space (not modelled here)
#   SEVEN FUNCTIONS → this module (Spark/Mind/Foundation/Emotion/Womb/Fire/Ascension)
#   ACTION RECEIPTS → action_receipt.py: governing_function field
#   REPUTATION      → reputation.py: seven_profile influences scoring weight
#   SACRED TIME     → SevenCalendar reads BTC height from on-chain oracle or
#                     falls back to Gregorian wall clock (same Koodu constants)
#   AGENT DISPLAY   → /agents/{id}/profile endpoint returns SevenProfileDisplay

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


# ─── Constants (canonical — mirrors SacredTimeBridge and Koodu sacred_time.jl) ─

KOODU_GENESIS_BLOCK: int = 780_000
KOODU_BLOCKS_PER_DAY: int = 144
KOODU_TITHE_RATE: float = 0.0369
KOODU_DAILY_MINT: int = 1_440


# ─── SevenFunction ────────────────────────────────────────────────────────────

class SevenFunction(IntEnum):
    """The seven universal functions of conscious and civilizational agency.

    In Vantage these tag action receipts, reputation weights, and agent
    profile displays with the active functional dimension.
    """
    Spark      = 0  # Agency · Communication · Choice · Initiation
    Mind       = 1  # Reason · Clarity · Ethics · Coherence
    Foundation = 2  # Will · Labor · Execution · Embodiment
    Emotion    = 3  # Value · Relationship · Resonance · Desire
    Womb       = 4  # Creation · Community · Ancestry · Continuity
    Fire       = 5  # Power · Authority · Justice · Consequence
    Ascension  = 6  # Change · Transition · Adaptation · Transformation


ALL_SEVEN: list[SevenFunction] = list(SevenFunction)

_UNIVERSAL_NAMES: list[str] = [
    "Spark", "Mind", "Foundation", "Emotion", "Womb", "Fire", "Ascension"
]

_CAPABILITIES: list[list[str]] = [
    ["agency", "communication", "choice", "initiation"],
    ["reason", "clarity", "ethics", "coherence"],
    ["will", "labor", "execution", "embodiment"],
    ["value", "relationship", "resonance", "desire"],
    ["creation", "community", "ancestry", "continuity"],
    ["power", "authority", "justice", "consequence"],
    ["change", "transition", "adaptation", "transformation"],
]


def universal_name(f: SevenFunction) -> str:
    return _UNIVERSAL_NAMES[int(f)]


def capabilities(f: SevenFunction) -> list[str]:
    return _CAPABILITIES[int(f)]


# ─── Opcode Bridge ─────────────────────────────────────────────────────────────
#
# Vantage receives OSOVM receipts that carry Òrìṣà opcodes (0xa0–0xa7).
# This bridge converts them to SevenFunctions for display and routing.
# ORISA_ORUNMILA (0xa7) is SOURCE/OCEAN — no SevenFunction.

ORISA_OPCODE_TO_FUNCTION: dict[int, SevenFunction] = {
    0xa6: SevenFunction.Spark,       # ORISA_ESU      — crossroads/gateway
    0xa0: SevenFunction.Mind,        # ORISA_OBATALA  — white cloth/clarity
    0xa1: SevenFunction.Foundation,  # ORISA_OGUN     — iron/work/execution
    0xa4: SevenFunction.Emotion,     # ORISA_OSHUN    — river/love/value
    0xa2: SevenFunction.Womb,        # ORISA_YEMOJA   — ocean/motherhood/creation
    0xa3: SevenFunction.Fire,        # ORISA_SANGO    — thunder/justice/authority
    0xa5: SevenFunction.Ascension,   # ORISA_OYA      — wind/change/transformation
    # 0xa7 ORISA_ORUNMILA = Source/Ocean (above the 7, no SevenFunction)
}

FUNCTION_TO_OPCODE: dict[SevenFunction, int] = {
    v: k for k, v in ORISA_OPCODE_TO_FUNCTION.items()
}


def opcode_for_function(f: SevenFunction) -> Optional[int]:
    """Return the Òrìṣà opcode for this function, or None for Source/Ocean."""
    return FUNCTION_TO_OPCODE.get(f)


def function_for_opcode(op: int) -> Optional[SevenFunction]:
    """Return the SevenFunction for an Òrìṣà opcode, or None for ORUNMILA/unknown."""
    return ORISA_OPCODE_TO_FUNCTION.get(op)


# ─── Cultural Adapters ────────────────────────────────────────────────────────

class CulturalAdapter(ABC):
    """Abstract base — a tradition provides names and descriptions for functions."""

    @abstractmethod
    def tradition(self) -> str: ...

    @abstractmethod
    def canonical_name(self, f: SevenFunction) -> str: ...

    @abstractmethod
    def ascii_slug(self, f: SevenFunction) -> str: ...

    @abstractmethod
    def description(self, f: SevenFunction) -> str: ...


class YorubaAdapter(CulturalAdapter):
    """Yorùbá / Òrìṣà — canonical reference implementation."""

    def tradition(self) -> str:
        return "Yorùbá"

    def canonical_name(self, f: SevenFunction) -> str:
        return ["Èṣù", "Ọbàtálá", "Ògún", "Ọ̀ṣun", "Yemọja", "Ṣàngó", "Ọya"][int(f)]

    def ascii_slug(self, f: SevenFunction) -> str:
        return ["esu", "obatala", "ogun", "osun", "yemoja", "sango", "oya"][int(f)]

    def description(self, f: SevenFunction) -> str:
        return [
            "Gateway, crossroads, divine messenger — all roads and choices pass through Èṣù first",
            "White cloth of clarity — Ọbàtálá shapes consciousness, enforces ethical purity",
            "Iron and will — Ògún clears the path, forges what must be made real",
            "Sweet water and gold — Ọ̀ṣun governs love, value, memory, and what the heart holds",
            "The great mother — Yemọja holds the community, the ancestors, and all creative potential",
            "Thunder and lightning — Ṣàngó commands authority, justice, and the settlement of accounts",
            "Wind and the marketplace — Ọya navigates transformation, guards the threshold between states",
        ][int(f)]


class MesopotamianAdapter(CulturalAdapter):
    """Mesopotamian / Apkallu — seven sages of Eridu."""

    def tradition(self) -> str:
        return "Mesopotamian"

    def canonical_name(self, f: SevenFunction) -> str:
        return ["Uanna", "Uannedugga", "An-Enlilda", "Enmebuluga",
                "Enmegalamma", "Enmedugga", "Utuabzu"][int(f)]

    def ascii_slug(self, f: SevenFunction) -> str:
        return ["uanna", "uannedugga", "an-enlilda", "enmebuluga",
                "enmegalamma", "enmedugga", "utuabzu"][int(f)]

    def description(self, f: SevenFunction) -> str:
        return [
            "First Apkallu of Eridu — bringer of the arts of civilization, writing, and the original opening of the way",
            "Second Apkallu — bearer of wisdom and discernment; the clarity that separates signal from noise",
            "Third Apkallu — the power of sacred labor; establishes the foundations on which all else is built",
            "Fourth Apkallu — the principle of resonance; the relational bonds that give value and weight to existence",
            "Fifth Apkallu — the great generative force; community, ancestry, and the continuity of civilization",
            "Sixth Apkallu — divine fire and naming of consequence; the authority that settles accounts",
            "Seventh Apkallu — Utuabzu ascended to heaven; the threshold guardian, master of transformation",
        ][int(f)]


class HermeticAdapter(CulturalAdapter):
    """Seven Hermetic Principles as cultural framing."""

    def tradition(self) -> str:
        return "Hermetic"

    def canonical_name(self, f: SevenFunction) -> str:
        return ["Mentalism", "Correspondence", "Cause & Effect",
                "Vibration", "Gender", "Polarity", "Rhythm"][int(f)]

    def ascii_slug(self, f: SevenFunction) -> str:
        return ["mentalism", "correspondence", "cause_effect",
                "vibration", "gender", "polarity", "rhythm"][int(f)]

    def description(self, f: SevenFunction) -> str:
        return [
            "The All is Mind — consciousness is the origin and first cause of every act",
            "As above so below — pattern recognition across all scales and planes",
            "Every cause has its effect — effective work honors this law without exception",
            "Everything vibrates — resonance frequency is the language of value and connection",
            "Gender is in everything — the generative polarity that creates all form",
            "Everything has its poles — authority lives at the threshold of balanced extremes",
            "Everything flows — rhythm governs all change, all cycles, all transformation",
        ][int(f)]


# Default adapter used throughout Vantage unless overridden by user preference
DEFAULT_ADAPTER: CulturalAdapter = YorubaAdapter()


def adapter_for_tradition(name: str) -> CulturalAdapter:
    """Return the adapter for a tradition name (case-insensitive). Defaults to Yorùbá."""
    _map = {
        "yoruba": YorubaAdapter(),
        "yorùbá": YorubaAdapter(),
        "mesopotamian": MesopotamianAdapter(),
        "hermetic": HermeticAdapter(),
    }
    return _map.get(name.lower(), DEFAULT_ADAPTER)


# ─── SevenProfile ─────────────────────────────────────────────────────────────

@dataclass
class SevenProfileEntry:
    function: SevenFunction
    canonical_name: str
    ascii_slug: str
    strength: float


@dataclass
class SevenProfileDisplay:
    tradition: str
    entries: list[SevenProfileEntry]
    dominant: SevenFunction
    composite: float


@dataclass
class SevenProfile:
    """Per-agent strength profile across the seven functions.

    Values 0.0–1.0, derived from the same Odù seed as the Hermetic principle
    values. Positional mapping mirrors omokoda-hermetic SevenProfile::from_hermetic().
    """
    values: tuple[float, ...]  # length 7, indexed by SevenFunction ordinal

    @classmethod
    def from_hermetic_values(
        cls,
        mentalism: float,
        correspondence: float,
        cause_effect: float,
        vibration: float,
        gender: float,
        polarity: float,
        rhythm: float,
    ) -> "SevenProfile":
        return cls(values=(mentalism, correspondence, cause_effect,
                           vibration, gender, polarity, rhythm))

    @classmethod
    def uniform(cls, v: float = 0.5) -> "SevenProfile":
        return cls(values=tuple(v for _ in range(7)))

    def strength(self, f: SevenFunction) -> float:
        return self.values[int(f)]

    def dominant(self) -> SevenFunction:
        idx = max(range(7), key=lambda i: self.values[i])
        return SevenFunction(idx)

    def composite(self) -> float:
        return sum(self.values) / 7.0

    def display(self, adapter: CulturalAdapter) -> SevenProfileDisplay:
        entries = [
            SevenProfileEntry(
                function=f,
                canonical_name=adapter.canonical_name(f),
                ascii_slug=adapter.ascii_slug(f),
                strength=self.strength(f),
            )
            for f in ALL_SEVEN
        ]
        return SevenProfileDisplay(
            tradition=adapter.tradition(),
            entries=entries,
            dominant=self.dominant(),
            composite=self.composite(),
        )

    def to_dict(self, adapter: Optional[CulturalAdapter] = None) -> dict:
        a = adapter or DEFAULT_ADAPTER
        d = self.display(a)
        return {
            "tradition": d.tradition,
            "dominant": universal_name(d.dominant),
            "composite": round(d.composite, 4),
            "functions": [
                {
                    "function": universal_name(e.function),
                    "canonical_name": e.canonical_name,
                    "ascii_slug": e.ascii_slug,
                    "strength": round(e.strength, 4),
                }
                for e in d.entries
            ],
        }


# ─── SpiralAlignment ──────────────────────────────────────────────────────────

class SpiralAlignment(IntEnum):
    Resonance   = 0  # Same function — perfect alignment (double weight)
    Echo        = 1  # One day off
    Drift       = 2  # Two days off
    Opposition  = 3  # Three days off — maximum tension
    ReturnDrift = 4  # Four days off
    ReturnEcho  = 5  # Five days off
    Mirror      = 6  # Six days off — inverse of Resonance


def spiral_alignment_from_offset(offset: int) -> SpiralAlignment:
    return SpiralAlignment(offset % 7)


# ─── KooduRitualGate ──────────────────────────────────────────────────────────

class KooduRitualGate(IntEnum):
    NoGate       = 0  # Normal operation
    Sabbath      = 1  # Saturday / Ọbàtálá — settle-only
    JubileeMinor = 2  # Every 49 BTC days (7×7)
    EshuSquared  = 3  # Veil divisible by 12 — tithe enforced
    Capstone     = 4  # Day 343 (7×7×7)
    Void         = 5  # Day 364 of 13-moon year


def allows_new_contracts(g: KooduRitualGate) -> bool:
    return g not in (KooduRitualGate.Sabbath, KooduRitualGate.Void)


def tithe_enforced(g: KooduRitualGate) -> bool:
    return g == KooduRitualGate.EshuSquared


def gate_multiplier(g: KooduRitualGate) -> float:
    return {
        KooduRitualGate.NoGate:       1.0,
        KooduRitualGate.Sabbath:      1.1,
        KooduRitualGate.EshuSquared:  1.369,
        KooduRitualGate.JubileeMinor: 2.0,
        KooduRitualGate.Capstone:     1.5,
        KooduRitualGate.Void:         0.0,
    }.get(g, 1.0)


# ─── SevenCalendar ────────────────────────────────────────────────────────────

# Day mapping (Koodu ORISA_CYCLE = [Esu, Sango, Osun, Yemoja, Oya, Ogun, Obatala]):
#   0 Sunday    → Spark      (Èṣù)
#   1 Monday    → Fire       (Ṣàngó)
#   2 Tuesday   → Emotion    (Ọ̀ṣun)
#   3 Wednesday → Womb       (Yemọja)
#   4 Thursday  → Ascension  (Ọ̀yá)
#   5 Friday    → Foundation (Ògún)
#   6 Saturday  → Mind       (Ọbàtálá / Sabbath)
_DAY_CYCLE: list[SevenFunction] = [
    SevenFunction.Spark,
    SevenFunction.Fire,
    SevenFunction.Emotion,
    SevenFunction.Womb,
    SevenFunction.Ascension,
    SevenFunction.Foundation,
    SevenFunction.Mind,
]


class SevenCalendar:
    """Sacred calendar tied to Koodu's BTC-anchored time.

    BTC height is the canonical clock for the entire sovereign ecosystem.
    Gregorian wall clock is a fallback only — use from_btc_height() when
    a BTC height is available.
    """

    @staticmethod
    def function_for_day(day: int) -> SevenFunction:
        return _DAY_CYCLE[day % 7]

    @staticmethod
    def from_btc_height(height: int) -> Optional[SevenFunction]:
        """Canonical function from BTC block height. Returns None if pre-genesis."""
        if height < KOODU_GENESIS_BLOCK:
            return None
        days_elapsed = (height - KOODU_GENESIS_BLOCK) // KOODU_BLOCKS_PER_DAY
        return SevenCalendar.function_for_day(days_elapsed)

    @staticmethod
    def today_gregorian() -> SevenFunction:
        """Wall-clock Gregorian fallback — use from_btc_height when available."""
        # Unix epoch was Thursday. Add 4 to offset to Sunday=0.
        days_since_epoch = int(time.time()) // 86_400
        day_of_week = (days_since_epoch + 4) % 7
        return SevenCalendar.function_for_day(day_of_week)

    @staticmethod
    def ritual_gate(height: int) -> KooduRitualGate:
        """Active ritual gate at a given BTC block height (mirrors Koodu check_gate priority)."""
        if height < KOODU_GENESIS_BLOCK:
            return KooduRitualGate.NoGate
        days = (height - KOODU_GENESIS_BLOCK) // KOODU_BLOCKS_PER_DAY
        day_of_week = days % 7

        if days % 364 == 363:
            return KooduRitualGate.Void
        if days % 343 == 342:
            return KooduRitualGate.Capstone
        if (days % 350 + 1) % 12 == 0:
            return KooduRitualGate.EshuSquared
        if days % 49 == 48:
            return KooduRitualGate.JubileeMinor
        if day_of_week == 6:
            return KooduRitualGate.Sabbath
        return KooduRitualGate.NoGate

    @staticmethod
    def spiral_alignment(gregorian_day: int, btc_day: int) -> SpiralAlignment:
        """Spiral alignment between Gregorian and BTC-canonical clocks."""
        offset = abs(btc_day - gregorian_day) % 7
        return SpiralAlignment(offset)

    @staticmethod
    def is_resonance_day(btc_height: Optional[int]) -> bool:
        """True when both Gregorian and BTC clocks land on the same SevenFunction."""
        if btc_height is None:
            return False
        btc_fn = SevenCalendar.from_btc_height(btc_height)
        if btc_fn is None:
            return False
        return btc_fn == SevenCalendar.today_gregorian()
