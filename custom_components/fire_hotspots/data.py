"""Runtime data types for Fire Hotspots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import FirmsCoordinator

type FirmsConfigEntry = ConfigEntry[FirmsRuntimeData]


@dataclass(slots=True)
class FirmsRuntimeData:
    """Objects owned by a loaded config entry."""

    coordinator: FirmsCoordinator
