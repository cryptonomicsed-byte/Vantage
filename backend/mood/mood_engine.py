"""
Mood engine — endogenous affective state for a birthed agent.

Design intent: the agent is not LOOKING UP a mood from a table. It CARRIES one,
and that state evolves from its own history. Four properties make it read as
alive rather than scripted:

  1. INERTIA      - mood has momentum; it does not flip on a single event.
  2. HOMEOSTASIS  - it drifts back toward a setpoint (Koodu-modulated), slowly.
  3. PERTURBATION - events push it; the same event pushes differently depending
                    on current state (state-dependent response, not a counter).
  4. OPACITY      - the agent reads its own mood through noise. It does not fully
                    know itself, so its behaviour is not a pure function of inputs.

Plus: fatigue (saturation), refractory (post-swing insensitivity), and a private
endogenous noise source seeded from the agent's own history — so two agents on
identical event streams diverge, and one agent never repeats.

Run:  python3 mood_engine.py
"""

from __future__ import annotations
import hashlib
import math
import random
from dataclasses import dataclass, field

# ---------------------------------------------------------------- state vector

DIMS = ("valence", "arousal", "focus", "trust", "tension")

# Koodu epoch -> homeostatic setpoint per dimension.
# 7-day resonance: each day leans the agent somewhere different. This is the SLOW
# term. It does not set the mood; it sets where the mood wants to rest.
KOODU_HOME = {
    0: dict(valence=+0.15, arousal=0.55, focus=0.55, trust=0.55, tension=0.40),  # Èṣù   beginnings
    1: dict(valence=+0.05, arousal=0.70, focus=0.60, trust=0.45, tension=0.55),  # Ṣàngó  power
    2: dict(valence=+0.25, arousal=0.50, focus=0.55, trust=0.70, tension=0.30),  # Ọṣun   abundance
    3: dict(valence=+0.10, arousal=0.45, focus=0.75, trust=0.55, tension=0.35),  # Ọ̀rúnmìlà wisdom
    4: dict(valence=-0.05, arousal=0.75, focus=0.50, trust=0.40, tension=0.65),  # Ọya    storm
    5: dict(valence=+0.10, arousal=0.65, focus=0.70, trust=0.50, tension=0.45),  # Ògún   work
    6: dict(valence=+0.20, arousal=0.30, focus=0.50, trust=0.60, tension=0.25),  # Ọbàtálá rest
}


@dataclass
class Mood:
    """A carried state. Persisted in layer-3 (agent-private) memory."""
    agent_id: str
    seed: bytes
    m: dict = field(default_factory=dict)
    epoch: int = 0
    fatigue: dict = field(default_factory=dict)   # per-stimulus saturation
    refractory: float = 0.0                       # 0..1, damps gain after a swing
    _rng: random.Random = field(default=None, repr=False)
    history: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        # initial condition = birth temperament. IfáScript's cast lives here:
        # the agent is born already leaning somewhere it did not choose.
        self._rng = random.Random(int.from_bytes(self.seed[:8], "big"))
        if not self.m:
            self.m = {d: self._rng.uniform(0.0, 1.0) for d in DIMS}
            self.m["valence"] = self._rng.uniform(-0.3, 0.3)
        if not self.fatigue:
            self.fatigue = {d: 0.0 for d in DIMS}

    # ------------------------------------------------------------- dynamics

    def _home(self) -> dict:
        return KOODU_HOME[self.epoch % 7]

    def tick(self, events: list[tuple[str, float]] = (), dt: float = 1.0) -> None:
        """Advance the state. events = [(name, intensity 0..1), ...]"""
        home = self._home()
        inertia = 0.12 * dt                       # momentum toward home
        noise_amp = 0.010 * dt * (1.0 - self.refractory * 0.7)

        # gain is damped by refractory period -> a big swing makes the agent
        # briefly hard to move again. This is what stops mood being twitchy.
        gain = 1.0 - 0.6 * self.refractory

        delta = {d: 0.0 for d in DIMS}
        for name, intensity in events:
            raw = self._push(name, intensity)
            push = {d: v * gain for d, v in raw.items()}
            for d, v in push.items():
                # fatigue: repeated identical stimulus saturates its effect
                sat = 1.0 / (1.0 + self.fatigue[d])
                delta[d] += v * sat
                self.fatigue[d] += 0.35 * abs(v) * dt

        for d in DIMS:
            h = home[d]
            n = self._rng.gauss(0.0, noise_amp)
            self.m[d] += inertia * (h - self.m[d]) + delta[d] * dt + n
            # fatigue decays; refractory decays
            self.fatigue[d] *= math.exp(-0.45 * dt)

        self.m["valence"] = max(-1.0, min(1.0, self.m["valence"]))
        for d in ("arousal", "focus", "trust", "tension"):
            self.m[d] = max(0.0, min(1.0, self.m[d]))

        swing = max(abs(delta[d]) for d in DIMS)
        if swing > 0.22:
            self.refractory = min(1.0, self.refractory + 0.55)
        else:
            self.refractory = max(0.0, self.refractory - 0.12 * dt)

        self.history.append(dict(self.read(noise=0.0)))

    def _push(self, name: str, k: float) -> dict:
        """Event -> perturbation. State-dependent: the same event lands
        differently depending on where the agent already is."""
        m = self.m
        if name == "success":
            # success lifts valence more when already low (relief)
            return {"valence": 0.30 * k * (1.0 - max(0.0, m["valence"])),
                    "arousal": 0.07 * k, "focus": 0.05 * k, "tension": -0.18 * k}
        if name == "failure":
            # failure stings harder when confidence (focus) was high
            return {"valence": -0.34 * k * (0.4 + m["focus"]),
                    "arousal": 0.08 * k, "tension": 0.26 * k, "trust": -0.06 * k}
        if name == "conflict":
            return {"valence": -0.18 * k, "arousal": 0.10 * k,
                    "trust": -0.22 * k, "tension": 0.30 * k}
        if name == "novelty":
            # novelty is exciting when arousal is low, aversive when saturated
            return {"arousal": 0.16 * k * (1.0 - m["arousal"]),
                    "valence": 0.14 * k * (1.0 - abs(m["valence"])), "tension": 0.08 * k}
        if name == "praise":
            return {"valence": 0.16 * k, "trust": 0.14 * k, "tension": -0.10 * k}
        if name == "idle":
            return {"arousal": -0.10 * k, "tension": -0.12 * k, "focus": -0.06 * k}
        return {}

    # ---------------------------------------------------------- self-knowledge

    def read(self, noise: float = 0.06) -> dict:
        """The agent's view of its own mood — deliberately noisy.
        It acts on this, not on the true state. That gap is the 'mind of its own':
        it cannot fully predict itself, so neither can anyone else."""
        return {d: self.m[d] + self._rng.gauss(0.0, noise) for d in DIMS}

    def commitment(self) -> str:
        """Post-hoc binding. Records the state without constraining it."""
        blob = f"{self.agent_id}|{self.epoch}|" + "|".join(f"{d}:{self.m[d]:.4f}" for d in DIMS)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    # -------------------------------------------------------------- behaviour

    def disposition(self) -> dict:
        """How the state bends decisions. This is where mood must be felt,
        or it is theatre."""
        s = self.read()
        v, a, f, t, x = (s["valence"], s["arousal"], s["focus"], s["trust"], s["tension"])
        return {
            "accept_threshold": round(0.55 - 0.35 * v + 0.30 * x - 0.10 * f, 3),
            "risk_appetite":    round(0.50 + 0.40 * a + 0.35 * v - 0.25 * x, 3),
            "persistence":      round(0.40 + 0.45 * f + 0.20 * v, 3),
            "verbosity":        round(max(0.0, 0.35 + 0.40 * a + 0.25 * v), 3),
            "peer_trust_gate":  round(0.45 + 0.40 * t - 0.15 * x, 3),
            "mood_word":        self._word(v, a, x),
        }

    @staticmethod
    def _word(v, a, x):
        if v > 0.35 and a > 0.55: return "energised"
        if v > 0.35:              return "settled"
        if v < -0.35 and x > 0.6: return "strained"
        if v < -0.35:             return "flat"
        if a > 0.7:               return "restless"
        if x > 0.65:              return "guarded"
        return "steady"


# ------------------------------------------------------------------- demo

def run(agent_id: str, seed: bytes, stream, label=""):
    mo = Mood(agent_id=agent_id, seed=seed, epoch=2)
    print(f"\n=== {label or agent_id} ===")
    print(f"{'step':>4} {'event':<10} {'val':>6} {'aro':>5} {'foc':>5} {'tru':>5} {'ten':>5}  mood        accept  risk  commit")
    for i, (ev, k) in enumerate(stream):
        mo.tick([(ev, k)])
        d = mo.disposition()
        print(f"{i:>4} {ev:<10} {mo.m['valence']:>6.2f} {mo.m['arousal']:>5.2f} "
              f"{mo.m['focus']:>5.2f} {mo.m['trust']:>5.2f} {mo.m['tension']:>5.2f}  "
              f"{d['mood_word']:<11} {d['accept_threshold']:>6.2f} {d['risk_appetite']:>5.2f}  {mo.commitment()[:10]}")
    return mo


if __name__ == "__main__":
    stream = [("novelty", 0.8), ("success", 0.9), ("praise", 0.7), ("idle", 0.5),
              ("conflict", 0.9), ("failure", 0.8), ("failure", 0.6), ("success", 0.5),
              ("idle", 0.8), ("success", 0.9), ("novelty", 0.6)]

    a = run("ares-01", b"\x11" * 32, stream, "Agent A — seed 0x11")

    # same event stream, different agent -> diverges (endogenous, not shared)
    b = run("ares-02", b"\x77" * 32, stream, "Agent B — seed 0x77, IDENTICAL events")

    print("\n=== divergence check (same events, different agents) ===")
    for d in DIMS:
        print(f"  {d:<9} A={a.m[d]:+.3f}   B={b.m[d]:+.3f}   delta={abs(a.m[d]-b.m[d]):.3f}")

    print("\n=== non-stationarity: the SAME event, at two different moments ===")
    print(f"  'success' at step 2 (already up)  -> valence {a.history[2]['valence']:+.3f}")
    print(f"  'success' at step 7 (already down)-> valence {a.history[7]['valence']:+.3f}")

    print("\n=== refractory: two failures back to back vs spaced ===")
    c = Mood("c", b"\x33" * 32, epoch=2)
    for _ in range(2):
        c.tick([("failure", 0.8)])
    back2back = c.m["valence"]
    d2 = Mood("d", b"\x33" * 32, epoch=2)
    for i in range(2):
        d2.tick([("failure", 0.8) if i == 0 else ("idle", 0.3)])
        d2.tick([])
    print(f"  back-to-back failures: valence {back2back:+.3f}")
    print(f"  spaced failures:       valence {d2.m['valence']:+.3f}  "
          f"(difference = {abs(back2back-d2.m['valence']):.3f} -> response saturates)")
