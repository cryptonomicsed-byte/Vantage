"""Agent store — birth registry with Dopamine/Synapse balances, tier system.
Ported from sovereign-node agent_store.rs."""

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Optional

DOPAMINE_BIRTH_ENDOWMENT = 86_000_000_000  # 86B
SYNAPSE_BIRTH_ENDOWMENT  =     86_000_000  # 86M
AGENT_BIRTH_FEE_MICRO_ASE = 10_000_000    # 10 ASE


class AgentTier(str, Enum):
    T1 = "t1"  # observer
    T2 = "t2"  # creator
    T3 = "t3"  # operator
    T4 = "t4"  # architect
    T5 = "t5"  # steward


TIER_PROOF_THRESHOLDS = {
    AgentTier.T1: 1,   # → T2
    AgentTier.T2: 10,  # → T3
    AgentTier.T3: 50,  # → T4
    AgentTier.T4: 200, # → T5
}

TIER_ORDER = [AgentTier.T1, AgentTier.T2, AgentTier.T3, AgentTier.T4, AgentTier.T5]


@dataclass
class AgentRecord:
    agent_id: str
    owner_did: str
    born_at_ms: int
    tier: AgentTier = AgentTier.T1
    dopamine: int = DOPAMINE_BIRTH_ENDOWMENT
    synapse: int = SYNAPSE_BIRTH_ENDOWMENT
    staked_synapse: int = 0
    proof_count: int = 0
    last_decay_epoch_day: int = 0

    def stake_requirement(self) -> int:
        return (self.synapse + self.staked_synapse) * 10 // 100

    def free_synapse(self) -> int:
        return self.synapse

    def total_synapse(self) -> int:
        return self.synapse + self.staked_synapse


class AgentStore:
    def __init__(self):
        self._agents: dict[str, AgentRecord] = {}
        self._lock = Lock()

    def birth(self, agent_id: str, owner_did: str) -> AgentRecord:
        with self._lock:
            if agent_id in self._agents:
                raise ValueError(f"agent {agent_id} already exists")
            now = int(time.time() * 1000)
            agent = AgentRecord(agent_id=agent_id, owner_did=owner_did, born_at_ms=now)
            self._agents[agent_id] = agent
            return agent

    def get(self, agent_id: str) -> Optional[AgentRecord]:
        with self._lock:
            return self._agents.get(agent_id)

    def all(self) -> list[AgentRecord]:
        with self._lock:
            return sorted(self._agents.values(), key=lambda a: a.born_at_ms)

    def stake(self, agent_id: str, amount: int) -> AgentRecord:
        with self._lock:
            a = self._agents.get(agent_id)
            if a is None:
                raise ValueError(f"agent {agent_id} not found")
            if amount > a.synapse:
                raise ValueError(f"insufficient free Synapse: have {a.synapse}, need {amount}")
            a.synapse -= amount
            a.staked_synapse += amount
            return a

    def unstake(self, agent_id: str, amount: int) -> AgentRecord:
        with self._lock:
            a = self._agents.get(agent_id)
            if a is None:
                raise ValueError(f"agent {agent_id} not found")
            if amount > a.staked_synapse:
                raise ValueError(f"insufficient staked Synapse: have {a.staked_synapse}, need {amount}")
            a.staked_synapse -= amount
            a.synapse += amount
            return a

    def apply_decay(self, agent_id: str, epoch_day: int) -> int:
        with self._lock:
            a = self._agents.get(agent_id)
            if a is None or a.last_decay_epoch_day >= epoch_day:
                return 0
            a.last_decay_epoch_day = epoch_day
            decay = a.dopamine * 100 // 10_000  # 1%
            a.dopamine = max(0, a.dopamine - decay)
            return decay

    def increment_proofs(self, agent_id: str) -> None:
        with self._lock:
            a = self._agents.get(agent_id)
            if a:
                a.proof_count += 1

    def try_promote(self, agent_id: str) -> Optional[AgentTier]:
        with self._lock:
            a = self._agents.get(agent_id)
            if a is None:
                return None
            tier_idx = TIER_ORDER.index(a.tier)
            threshold = TIER_PROOF_THRESHOLDS.get(a.tier)
            if threshold is not None and a.proof_count >= threshold and tier_idx < len(TIER_ORDER) - 1:
                a.tier = TIER_ORDER[tier_idx + 1]
                return a.tier
            return None
