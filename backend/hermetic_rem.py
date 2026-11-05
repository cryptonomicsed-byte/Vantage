"""Hermetic REM valve — routes memory/signal outcomes through 7 Hermetic
principles to determine memory fate.

Principles mirror omokoda-hermetic/src/lib.rs (archived Rust crate).
Pure-function: no I/O, no DB. Callers emit mycelium traces.

Routing thresholds:
  >= 0.70  → PROMOTE     (anchor to main vault / surface as finding)
  >= 0.50  → FOLD_TO_HIVE (collective learning value, share with swarm)
  >= 0.35  → FOLD_TO_PRIVATE (keep but do not broadcast)
  <  0.35  → ARCHIVE     (dormant)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MemoryFate(str, Enum):
    PROMOTE = "promote"
    FOLD_TO_HIVE = "fold-to-hive"
    FOLD_TO_PRIVATE = "fold-to-private"
    ARCHIVE = "archive"


@dataclass
class PrincipleScore:
    name: str
    score: float  # 0.0–1.0

    def __post_init__(self):
        self.score = max(0.0, min(1.0, self.score))


@dataclass
class RemResult:
    fate: MemoryFate
    overall_score: float
    principles: list[PrincipleScore] = field(default_factory=list)
    reasoning: str = ""

    def to_dict(self) -> dict:
        return {
            "fate": self.fate.value,
            "overall_score": round(self.overall_score, 4),
            "principles": {p.name: round(p.score, 4) for p in self.principles},
            "reasoning": self.reasoning,
        }


def score_memory(candidate: dict) -> RemResult:
    """Score a signal/memory candidate against the 7 Hermetic principles.

    candidate keys (all optional, fail-open to 0.5):
      confidence       float  0–1   model confidence in verdict
      correct          bool|None    outcome if resolved (None = unresolved)
      novelty          float  0–1   uniqueness vs existing patterns
      collective_value float  0–1   usefulness beyond this agent
      recency_hours    float        age of signal (0 = just happened)
      domain           str          topic label (unused in scoring, kept for context)
    """
    conf = float(candidate.get("confidence", 0.5))
    correct: Optional[bool] = candidate.get("correct")
    novelty = float(candidate.get("novelty", 0.5))
    coll_val = float(candidate.get("collective_value", 0.5))
    recency = float(candidate.get("recency_hours", 24.0))

    # 1. Mentalism — how idea-rich / abstract is this memory?
    mentalism = novelty * 0.6 + conf * 0.4

    # 2. Correspondence — mirrors known patterns; inverse of novelty
    # High correspondence = confirmed pattern → fold-to-hive candidate
    correspondence = max(0.0, 1.0 - novelty * 0.8)

    # 3. Vibration — energy / currency of signal; decays over 1 week
    decay = max(0.0, 1.0 - recency / 168.0)
    vibration = conf * 0.5 + decay * 0.5

    # 4. Polarity — strength of directional signal
    polarity = conf
    if correct is False:
        polarity *= 0.4  # wrong signals have weaker polarity
    elif correct is None:
        polarity *= 0.7

    # 5. Rhythm — temporal fit; has a floor so old signals aren't killed
    rhythm = max(0.0, 1.0 - recency / 72.0) * 0.6 + 0.4
    rhythm = min(1.0, rhythm)

    # 6. Cause-Effect — clarity of causal chain
    if correct is True:
        cause_effect = conf * 0.75 + 0.25
    elif correct is False:
        # Wrong but high-confidence is instructive (causal chain was present, model misfired)
        cause_effect = (1.0 - conf) * 0.5 + conf * 0.3
    else:
        cause_effect = conf * 0.5

    # 7. Gender — generative potential (spawns useful derivatives)
    gender = coll_val * 0.7 + novelty * 0.3

    principles = [
        PrincipleScore("mentalism", mentalism),
        PrincipleScore("correspondence", correspondence),
        PrincipleScore("vibration", vibration),
        PrincipleScore("polarity", polarity),
        PrincipleScore("rhythm", rhythm),
        PrincipleScore("cause_effect", cause_effect),
        PrincipleScore("gender", gender),
    ]

    overall = sum(p.score for p in principles) / len(principles)

    # Routing
    if overall >= 0.70:
        fate = MemoryFate.PROMOTE
        reason = f"score={overall:.3f} all 7 principles strong — anchor"
    elif overall >= 0.50 and correspondence >= 0.55:
        fate = MemoryFate.FOLD_TO_HIVE
        reason = (
            f"score={overall:.3f} correspondence={correspondence:.3f} — collective pattern"
        )
    elif overall >= 0.35:
        fate = MemoryFate.FOLD_TO_PRIVATE
        reason = f"score={overall:.3f} moderate — private vault"
    else:
        fate = MemoryFate.ARCHIVE
        reason = f"score={overall:.3f} below threshold — archive"

    return RemResult(fate=fate, overall_score=overall, principles=principles, reasoning=reason)
