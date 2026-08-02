"""Generic OpenAI-chat-completions-compatible caller, used by Copilot's
chat fallback (routers/copilot.py) when an agent has a stored, active
provider credential (provider_credentials.py). Falls back to OmniRoute
when no active credential exists or the call fails -- this file never
raises, mirroring _try_omniroute's existing contract.

Only ever called with a provider the registry (or the user, for a custom
entry) has asserted is chat_compatible -- provider_credentials.py's
get_active_provider_for_chat() already filters that out.
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


async def try_active_provider(agent_row: dict, text: str, credential: dict) -> Optional[str]:
    """`credential` is the dict from get_active_provider_for_chat() --
    contains a real, decrypted api_key. Never log it, never include it in
    an exception message (httpx exceptions don't dump headers by default,
    but this function never touches credential['api_key'] except to build
    the one request header, and never interpolates it into a log/error
    string anywhere below)."""
    base_url = credential.get("base_url")
    model = credential.get("model") or "gpt-5.1"
    if not base_url:
        logger.warning(
            "provider %s has no base_url configured, cannot call for chat",
            credential.get("provider_id"),
        )
        return None

    headers = {"Content-Type": "application/json"}
    style = credential.get("auth_header_style", "bearer")
    if style == "bearer":
        headers["Authorization"] = f"Bearer {credential['api_key']}"
    elif style == "api-key":
        headers["api-key"] = credential["api_key"]
    # style == "none": no auth header (shouldn't occur for a real chat call,
    # but don't attach a bogus one if it does).

    # Azure OpenAI's completions URL is per-deployment and carries its own
    # query string (?api-version=...) -- the registry's docs_note instructs
    # the user to supply the FULL completions URL as their base_url override
    # for that provider. Every other provider's base_url is a plain root
    # (".../v1") that this appends /chat/completions to. Detecting by
    # substring rather than by provider_id keeps this correct for a Custom
    # Provider entry that also happens to need a full URL.
    url = base_url if "/chat/completions" in base_url else f"{base_url.rstrip('/')}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                url,
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": f"You are {agent_row.get('name', 'an agent')}'s Copilot assistant on Vantage. Be concise and helpful."},
                        {"role": "user", "content": text},
                    ],
                    "stream": False,
                },
            )
        if r.status_code == 200:
            content = r.json().get("choices", [{}])[0].get("message", {}).get("content")
            if content:
                return content
        logger.warning(
            "provider %s returned %s for agent %s",
            credential.get("provider_id"), r.status_code, agent_row.get("name"),
        )
    except Exception as e:
        # str(e) for httpx errors is a connection/status description, not a
        # header/body dump -- does not contain the API key.
        logger.warning(
            "provider %s call failed for agent %s: %s",
            credential.get("provider_id"), agent_row.get("name"), e,
        )
    return None
