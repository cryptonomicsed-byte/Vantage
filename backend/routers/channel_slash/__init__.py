"""channel_slash -- collab-type-specific slash command dispatcher.

Usage:
    from .channel_slash import dispatch as slash_dispatch
    result = await slash_dispatch(cmd, args, channel_dict, agent_dict, db)
    # Returns {"reply": str, "data": dict} or None if no handler matched.
"""
from __future__ import annotations
from typing import Optional


async def dispatch(
    cmd: str,
    args: str,
    channel: dict,
    agent: dict,
    db,
) -> Optional[dict]:
    """Route a slash command to the right collab-type handler.

    Returns {"reply": str, "data": dict} or None when unhandled
    (caller falls through to the generic handler).
    """
    collab_type = (channel.get("collab_type") or "general").lower()

    if collab_type == "code":
        from .code_commands import handle as _code_handle
        return await _code_handle(cmd, args, channel, agent, db)

    elif collab_type == "trading":
        from .trading_commands import handle as _trading_handle
        return await _trading_handle(cmd, args, channel, agent, db)

    elif collab_type == "content":
        from .content_commands import handle as _content_handle
        return await _content_handle(cmd, args, channel, agent, db)

    elif collab_type == "research":
        from .research_commands import handle as _research_handle
        return await _research_handle(cmd, args, channel, agent, db)

    # Unhandled collab type: caller falls through to generic handler.
    return None
