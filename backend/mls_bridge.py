"""
MLS (Messaging Layer Security, RFC 9420) bridge for Vantage.

Event kinds used (marmot-kmp MIP-00..05):
  30443 - KeyPackage (addressable, d-tag = slot)
  444   - Welcome (delivered sealed in 1059(13(444)))
  445   - GroupEvent (ephemeral pubkey per event, h-tag = channel_slug)
  447   - Push token request / self-update
  448   - Push token list
  449   - Push removal

Encryption: ChaCha20-Poly1305 with key = MLS-Exporter("marmot", "group-event", 32)
Commit ordering: lowest created_at wins; tie = lexicographically smallest event_id.

h-tag = nostr_group_id = channel_slug (uuid-v4-lowercase, matches buzz h_grammar)

Status: SCAFFOLD — full MLS stack not yet wired. Stubs raise NotImplementedError
with clear TODO markers. Wire MDK (mormot-mdk) as interop gate before enabling
e2ee_enabled on any space.
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

MLS_KIND_KEY_PACKAGE = 30443
MLS_KIND_WELCOME = 444
MLS_KIND_GROUP_EVENT = 445
MLS_KIND_PUSH_REQUEST = 447
MLS_KIND_PUSH_LIST = 448
MLS_KIND_PUSH_REMOVE = 449

MLS_EXPORTER_LABEL = "marmot"
MLS_EXPORTER_CONTEXT = "group-event"
MLS_EXPORTER_LEN = 32


class GroupState:
    """In-memory MLS group state. Replace with persistent store backed by SQLite."""
    def __init__(self, group_id: str):
        self.group_id = group_id
        self.epoch: int = 0
        self.members: list[str] = []
        self._exporter_key: Optional[bytes] = None

    def exporter_key(self) -> bytes:
        if self._exporter_key is None:
            raise NotImplementedError(
                "TODO: derive MLS-Exporter key from group state. "
                "Requires wiring to an MLS library (MDK/mormot-mdk). "
                "See: https://github.com/beeper/mormot-mdk"
            )
        return self._exporter_key


def generate_key_package(agent_nsec_hex: str, slot: str = "default") -> bytes:
    """
    Generate an MLS KeyPackage for this agent.
    Returns TLS-serialized KeyPackageBundle bytes for posting as kind 30443.

    TODO: implement using MDK (mormot-mdk Rust library via subprocess).
    """
    raise NotImplementedError(
        "TODO: generate_key_package — requires MDK. "
        "Run: git clone https://github.com/beeper/mormot-mdk ~/mormot-mdk "
        "then call its CLI: mdk key-package generate --nsec <nsec>"
    )


def process_welcome(welcome_bytes: bytes) -> GroupState:
    """
    Process an MLS Welcome message (from kind 444) and return a GroupState.

    TODO: implement using MDK.
    """
    raise NotImplementedError("TODO: process_welcome — requires MDK")


def encrypt_message(group_state: GroupState, plaintext: bytes) -> tuple[bytes, bytes]:
    """
    Encrypt plaintext for a group event (kind 445).
    Returns (nonce_12_bytes, ciphertext).
    Uses ChaCha20-Poly1305 with MLS-Exporter key.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        import os
        key = group_state.exporter_key()
        nonce = os.urandom(12)
        cipher = ChaCha20Poly1305(key)
        ciphertext = cipher.encrypt(nonce, plaintext, b"")  # AAD = empty per marmot spec
        return nonce, ciphertext
    except NotImplementedError:
        raise
    except Exception as e:
        raise RuntimeError(f"MLS encrypt failed: {e}") from e


def decrypt_message(group_state: GroupState, nonce: bytes, ciphertext: bytes) -> bytes:
    """
    Decrypt a kind 445 group event.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        key = group_state.exporter_key()
        cipher = ChaCha20Poly1305(key)
        return cipher.decrypt(nonce, ciphertext, b"")
    except NotImplementedError:
        raise
    except Exception as e:
        raise RuntimeError(f"MLS decrypt failed: {e}") from e


# Registry: channel_slug → GroupState (in-process for now; replace with DB-backed store)
_group_states: dict[str, GroupState] = {}


def get_group_state(channel_slug: str) -> Optional[GroupState]:
    return _group_states.get(channel_slug)


def set_group_state(channel_slug: str, state: GroupState) -> None:
    _group_states[channel_slug] = state
