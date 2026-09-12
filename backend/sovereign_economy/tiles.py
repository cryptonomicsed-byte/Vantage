"""Tile economy store — per-tile Àṣẹ economy state.
Ported from sovereign-node tile_economy_store.rs."""

from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

DEFAULT_USAGE_FEE_PCT = 5.0  # 5%


@dataclass
class TileEconomy:
    tile_id: str
    usage_fee_pct: float = DEFAULT_USAGE_FEE_PCT
    owner_did: Optional[str] = None
    staked_tokens: int = 0
    total_ase_minted: int = 0
    capture_count: int = 0
    eshu_tithe_total: int = 0
    owner_fee_total: int = 0


class TileEconomyStore:
    def __init__(self):
        self._tiles: dict[str, TileEconomy] = {}
        self._lock = Lock()

    def _get_or_create(self, tile_id: str) -> TileEconomy:
        if tile_id not in self._tiles:
            self._tiles[tile_id] = TileEconomy(tile_id=tile_id)
        return self._tiles[tile_id]

    def apply_mint(self, tile_id: str, net_minted: int, eshu_tithe: int, owner_fee: int) -> None:
        with self._lock:
            economy = self._get_or_create(tile_id)
            economy.total_ase_minted += net_minted
            economy.eshu_tithe_total += eshu_tithe
            economy.owner_fee_total += owner_fee
            economy.capture_count += 1

    def stake(self, tile_id: str, amount: int) -> None:
        with self._lock:
            self._get_or_create(tile_id).staked_tokens += amount

    def claim(self, tile_id: str, owner_did: str) -> None:
        with self._lock:
            self._get_or_create(tile_id).owner_did = owner_did

    def get(self, tile_id: str) -> Optional[TileEconomy]:
        with self._lock:
            return self._tiles.get(tile_id)

    def upsert(self, economy: TileEconomy) -> None:
        with self._lock:
            self._tiles[economy.tile_id] = economy

    def list(self) -> list[TileEconomy]:
        with self._lock:
            return list(self._tiles.values())
