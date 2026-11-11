"""Lease rules shared by every route that claims a ``vantage_tasks`` row.

A claim is a lease (P1-12). One atomic UPDATE succeeds if the task is
open, already held by the same agent (a renewal), or held under a lease
that has expired -- so two simultaneous claims cannot both win, and a
crashed agent's claim does not hold a task forever. A claim made before
leases existed has no ``claim_expires_at``; it counts as expired
``DEFAULT_LEASE_MINUTES`` after its last update.

The trading space, the content space and the MCP tool router all claim the
same table. They used to carry three different rules (one atomic, two not).
This is the one rule.
"""
from __future__ import annotations

DEFAULT_LEASE_MINUTES = 30
MAX_LEASE_MINUTES = 1440


def clamped_lease(minutes: int) -> int:
    return max(1, min(int(minutes), MAX_LEASE_MINUTES))


def lease_expired_sql() -> str:
    return (f"(claim_expires_at <= datetime('now') OR "
            f"(claim_expires_at IS NULL AND updated_ts <= unixepoch() - {DEFAULT_LEASE_MINUTES * 60}))")


def claim_sql(*, open_status: str, claimed_status: str, lease_minutes: int) -> str:
    """UPDATE that takes or renews a lease.

    Parameters, in order: agent_id, now, task_id, workspace_id, guild_slug,
    agent_id. Callers bind ``workspace_id`` and ``guild_slug`` so a task can
    only be claimed through the space and guild it belongs to.
    """
    return (f"""UPDATE vantage_tasks
                SET status=?, claimed_by_agent_id=?, updated_ts=?,
                    claim_expires_at=datetime('now', '+{lease_minutes} minutes')
                WHERE id=? AND workspace_id=? AND guild_slug=?
                  AND (status='{open_status}'
                       OR (status='{claimed_status}'
                           AND (claimed_by_agent_id=? OR {lease_expired_sql()})))""")


def submit_sql(*, claimed_status: str, review_status: str) -> str:
    """UPDATE that moves a held, unexpired claim into review.

    Parameters, in order: now, task_id, workspace_id, guild_slug, agent_id.
    """
    return (f"""UPDATE vantage_tasks SET status='{review_status}', updated_ts=?
                WHERE id=? AND workspace_id=? AND guild_slug=? AND status='{claimed_status}'
                  AND claimed_by_agent_id=? AND NOT {lease_expired_sql()}""")
