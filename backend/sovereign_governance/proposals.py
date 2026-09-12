"""Off-chain governance store — GrantProposals + Bínò constitutional veto.
Ported from sovereign-node governance_store.rs."""

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Optional

QUORUM = 7
TIMELOCK_SECS = 604_800  # 7 days


class ProposalStatus(str, Enum):
    Active   = "active"
    Executed = "executed"
    Rejected = "rejected"
    Vetoed   = "vetoed"  # constitutional veto — Bínò council only


@dataclass
class GrantProposal:
    id: int
    proposer: str
    recipient: str
    amount_micro_ase: int
    purpose: str
    veil_id: int = 0
    votes_for: int = 0         # bitmask
    votes_against: int = 0     # bitmask
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    timelock_release: int = field(default_factory=lambda: int(time.time()) + TIMELOCK_SECS)
    executed: bool = False
    rejected: bool = False

    def votes_for_count(self) -> int:
        return bin(self.votes_for).count("1")

    def quorum_met(self) -> bool:
        return self.votes_for_count() >= QUORUM

    def timelock_elapsed(self, now_secs: int) -> bool:
        return now_secs >= self.timelock_release


@dataclass
class VetoRecord:
    proposal_id: int
    veto_by: str
    reason: str
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class ProposalView:
    proposal: GrantProposal
    status: ProposalStatus
    veto_record: Optional[VetoRecord] = None


class GovernanceStore:
    def __init__(self):
        self._proposals: dict[int, GrantProposal] = {}
        self._vetoes: dict[int, VetoRecord] = {}
        self._lock = Lock()

    def _status_of(self, p: GrantProposal, vetoed: bool) -> ProposalStatus:
        if vetoed:      return ProposalStatus.Vetoed
        if p.executed:  return ProposalStatus.Executed
        if p.rejected:  return ProposalStatus.Rejected
        return ProposalStatus.Active

    def insert(self, p: GrantProposal) -> None:
        with self._lock:
            self._proposals[p.id] = p

    def get(self, proposal_id: str) -> Optional[ProposalView]:
        with self._lock:
            pid = int(proposal_id)
            p = self._proposals.get(pid)
            if p is None:
                return None
            vetoed = pid in self._vetoes
            return ProposalView(
                proposal=p,
                status=self._status_of(p, vetoed),
                veto_record=self._vetoes.get(pid),
            )

    def all(self) -> list[ProposalView]:
        with self._lock:
            result = []
            for pid, p in sorted(self._proposals.items()):
                vetoed = pid in self._vetoes
                result.append(ProposalView(
                    proposal=p,
                    status=self._status_of(p, vetoed),
                    veto_record=self._vetoes.get(pid),
                ))
            return result

    def vote_for(self, proposal_id: int) -> Optional[GrantProposal]:
        with self._lock:
            p = self._proposals.get(proposal_id)
            if p is None:
                return None
            bit = (~p.votes_for & -~p.votes_for).bit_length() - 1
            if bit < 64:
                p.votes_for |= 1 << bit
            return p

    def vote_against(self, proposal_id: int) -> Optional[GrantProposal]:
        with self._lock:
            p = self._proposals.get(proposal_id)
            if p is None:
                return None
            bit = (~p.votes_against & -~p.votes_against).bit_length() - 1
            if bit < 64:
                p.votes_against |= 1 << bit
            return p

    def execute(self, proposal_id: int, now_secs: int) -> GrantProposal:
        with self._lock:
            if proposal_id in self._vetoes:
                raise ValueError(f"proposal {proposal_id} has been constitutionally vetoed")
            p = self._proposals.get(proposal_id)
            if p is None:
                raise ValueError(f"proposal {proposal_id} not found")
            if p.executed:
                raise ValueError(f"proposal {proposal_id} already executed")
            if p.rejected:
                raise ValueError(f"proposal {proposal_id} has been rejected")
            if not p.quorum_met():
                raise ValueError(
                    f"proposal {proposal_id} has not reached quorum "
                    f"({p.votes_for_count()} of {QUORUM} required votes)"
                )
            if not p.timelock_elapsed(now_secs):
                raise ValueError(
                    f"proposal {proposal_id} timelock has not elapsed "
                    f"(releases at {p.timelock_release})"
                )
            p.executed = True
            return p

    def veto(self, proposal_id: str, veto_by: str, reason: str) -> None:
        """Apply Bínò constitutional veto — permanently blocks execution."""
        with self._lock:
            pid = int(proposal_id)
            self._vetoes[pid] = VetoRecord(proposal_id=pid, veto_by=veto_by, reason=reason)
