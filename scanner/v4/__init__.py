"""Market Hunt V4 event-driven foundation.

V4 runs beside the V3 batch scanner until its shadow-validation gates pass.
"""

from .contracts import CandidateState, EventType, MarketEvent, SCHEMA_VERSION
from .engine import MomentumEngine

__all__ = [
    "CandidateState",
    "EventType",
    "MarketEvent",
    "MomentumEngine",
    "SCHEMA_VERSION",
]
