"""Twin license grant store. Ported from sovereign-node license_store.rs."""

from dataclasses import dataclass, field
from threading import Lock
from typing import Optional


@dataclass
class TwinLicenseConstraints:
    max_uses: Optional[int] = None
    geographic_restriction: Optional[str] = None
    sublicense_allowed: bool = False


@dataclass
class TwinLicenseGrant:
    grant_id: str
    twin_id: str
    grantor_did: str
    grantee_did: str
    rights: list[str]
    constraints: TwinLicenseConstraints
    issued_at: int
    grantor_sig: str
    merkle_root: str
    expires_at: Optional[int] = None
    fee_mist: Optional[int] = None
    sui_tx: Optional[str] = None
    grantee_sig: Optional[str] = None


class LicenseStore:
    def __init__(self):
        self._grants: dict[str, TwinLicenseGrant] = {}
        self._lock = Lock()

    def insert(self, grant: TwinLicenseGrant) -> None:
        with self._lock:
            self._grants[grant.grant_id] = grant

    def get(self, grant_id: str) -> Optional[TwinLicenseGrant]:
        with self._lock:
            return self._grants.get(grant_id)

    def for_twin(self, twin_id: str) -> list[TwinLicenseGrant]:
        with self._lock:
            return [g for g in self._grants.values() if g.twin_id == twin_id]

    def for_grantee(self, grantee_did: str) -> list[TwinLicenseGrant]:
        with self._lock:
            return [g for g in self._grants.values() if g.grantee_did == grantee_did]

    def all(self) -> list[TwinLicenseGrant]:
        with self._lock:
            return list(self._grants.values())

    def accept(self, grant_id: str, grantee_sig: str) -> Optional[TwinLicenseGrant]:
        with self._lock:
            g = self._grants.get(grant_id)
            if g:
                g.grantee_sig = grantee_sig
                return g
            return None
