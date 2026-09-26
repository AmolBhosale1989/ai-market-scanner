from __future__ import annotations

from abc import ABC,abstractmethod
from dataclasses import dataclass,field
from datetime import datetime
from typing import Any,Mapping,Sequence


@dataclass(frozen=True)
class NormalizedCatalystEvent:
    provider_event_id: str
    catalyst_type: str
    event_timestamp: datetime
    payload: Mapping[str,Any]=field(default_factory=dict)

    def __post_init__(self):
        if not str(self.provider_event_id).strip():
            raise ValueError("provider_event_id is required")
        if not str(self.catalyst_type).strip():
            raise ValueError("catalyst_type is required")
        if self.event_timestamp.tzinfo is None:
            raise ValueError("event_timestamp must be timezone-aware")


@dataclass(frozen=True)
class CatalystFetchResult:
    events: tuple[NormalizedCatalystEvent,...]
    rejected_count: int=0

    def __post_init__(self):
        if self.rejected_count < 0:
            raise ValueError("rejected_count must be nonnegative")


class CatalystProviderAdapter(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    def fetch_catalysts(self,ticker: str) -> CatalystFetchResult:
        """Raise on transport/provider failure; return an explicit successful result otherwise."""
        ...
