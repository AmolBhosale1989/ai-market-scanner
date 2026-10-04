"""Review-only producer instrumentation. NOT installed in Market Hunt.

This adapter observes the actual sector_rotation.run intermediate cache and
final tables; it never reconstructs scores or subtracts bonuses. Read handles
must ultimately be minted at the raw PIT boundary before any warehouse pruning.
The fixture mints synthetic handles; it does NOT verify PostgreSQL visibility.

Scope: two rotation datasets. Missing source histories are deliberately an
unsupported/fail-closed case until absence/quarantine certificates are wired.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
import re
from typing import Any, Mapping

import pandas as pd

from dependency_suppression import (Binding, Node, Policy, PublicationBlocked,
    SourceVerdict, Decision, evaluate_dependencies, graph_digest)

BASELINE_SHA = '25c2c8bfca2cb648bf265e0baed5ffe25a6fec69'
EXPECTED_BLOBS = {'sector_rotation.py': '1b05671f685aac6306e988ed59ef22592a2a8a47',
                  'intraday_metrics.py': 'a4dde4b82cfa0b29cf8ba60595c562961494ef36'}


def jsonable(value: Any) -> Any:
    """Canonical review representation; not a replacement for CP serialization."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise PublicationBlocked('REVIEW_JSON_KEY_INVALID')
        return {key: jsonable(v) for key, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if hasattr(value, 'item'):
        return jsonable(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)):
        return value
    raise PublicationBlocked(f'REVIEW_JSON_TYPE_UNSUPPORTED: {type(value).__name__}')


def digest(value: Any) -> str:
    raw = json.dumps(jsonable(value), sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def frame_digest(frame: pd.DataFrame) -> str:
    return digest({'frame': frame.to_dict(orient='split'),
                   'dtypes': [str(dtype) for dtype in frame.dtypes]})


def records(frame: pd.DataFrame) -> list[dict]:
    return jsonable(frame.to_dict(orient='records'))


@dataclass(frozen=True)
class ReadHandle:
    binding: Binding
    symbol: str
    timeframe: str
    evidence_manifest_hash: str
    frame_hash: str

    def __post_init__(self):
        if not self.symbol or self.symbol != self.symbol.strip().upper():
            raise PublicationBlocked('READ_SYMBOL_INVALID')
        if self.timeframe not in {'1d', '5m'}:
            raise PublicationBlocked('READ_TIMEFRAME_UNSUPPORTED')
        for value in (self.evidence_manifest_hash, self.frame_hash):
            if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
                raise PublicationBlocked('READ_HASH_INVALID')

    @property
    def node_id(self) -> str:
        return f'ohlcv:{self.symbol}:{self.timeframe}:{self.evidence_manifest_hash}'


@dataclass(frozen=True)
class RowBinding:
    dataset: str
    ordinal: int
    row_hash: str
    node_id: str


@dataclass(frozen=True)
class RotationSeal:
    binding: Binding
    nodes: tuple[Node, ...]
    policy: Policy
    row_bindings: tuple[RowBinding, ...]
    dataset_hashes: tuple[tuple[str, str], ...]
    input_inventory_json: str
    configuration_hash: str
    graph_hash: str

    def record(self) -> dict:
        return {'binding': self.binding.record(), 'graph_hash': self.graph_hash,
            'configuration_hash': self.configuration_hash,
            'input_inventory': json.loads(self.input_inventory_json),
            'dataset_hashes': dict(self.dataset_hashes),
            'rows': [vars(row) for row in self.row_bindings]}

    @property
    def sidecar_hash(self) -> str:
        return digest(self.record())


def evaluate_seal(seal: RotationSeal, verdicts: Mapping[str, SourceVerdict], *,
                  checked_at_utc: datetime, expected_sidecar_hash: str) -> Decision:
    if seal.sidecar_hash != expected_sidecar_hash:
        raise PublicationBlocked('SIDECAR_HASH_MISMATCH')
    return evaluate_dependencies(seal.nodes, verdicts, binding=seal.binding,
        policy=seal.policy, checked_at_utc=checked_at_utc, expected_graph_hash=seal.graph_hash)


def project_rotation(seal: RotationSeal, original_datasets: Mapping[str, list[dict]],
                     decision: Decision) -> dict[str, list[dict]]:
    """Use the immutable original tables, not previously pruned tables.

    Does not inject lineage columns or renumber/recompute any output value.
    This two-dataset adapter does not rebuild product_feed or dispatch alerts.
    """
    if decision.binding != seal.binding or decision.graph_hash != seal.graph_hash:
        raise PublicationBlocked('PROJECTION_BINDING_MISMATCH')
    if {key: digest(rows) for key, rows in original_datasets.items()} != dict(seal.dataset_hashes):
        raise PublicationBlocked('ORIGINAL_DATASET_HASH_MISMATCH')
    out = {key: [] for key in original_datasets}
    seen = set()
    for ref in seal.row_bindings:
        row = original_datasets[ref.dataset][ref.ordinal]
        key = (ref.dataset, ref.ordinal)
        if key in seen or digest(row) != ref.row_hash or ref.node_id not in decision.blocked_by:
            raise PublicationBlocked('ROW_BINDING_INVALID')
        seen.add(key)
        if not decision.blocked_by[ref.node_id]:
            out[ref.dataset].append(deepcopy(row))
    if len(seen) != sum(map(len, original_datasets.values())):
        raise PublicationBlocked('ROW_BINDING_INCOMPLETE')
    return out


class RotationRecorder:
    """Optional SHADOW hook; production must later require complete sidecars.

    Requested-but-absent histories are unsupported in this first adapter.
    Complete returned histories with _stats=None are explicitly represented:
    exclusions/readiness decisions also influence the cohort denominator/rank.
    """
    def __init__(self, binding: Binding, handles: Mapping[str, ReadHandle], *,
                 optional_5m_symbols: frozenset[str] = frozenset({'FINX', 'SKYY'})):
        self.binding = binding
        self.handles = dict(handles)
        self.optional_5m_symbols = optional_5m_symbols
        if not optional_5m_symbols <= {'FINX', 'SKYY'}:
            raise PublicationBlocked('UNAPPROVED_OPTIONAL_INPUT')
        self._captured = False
        self.seal: RotationSeal | None = None
        self._nodes: dict[str, Node] = {}
        self.labels: dict[str, str] = {}

    def _derive(self, label: str, deps, content) -> str:
        parents = tuple(sorted(set(deps)))
        if not parents or label in self.labels or any(p not in self._nodes for p in parents):
            raise PublicationBlocked(f'DERIVATION_INVALID: {label}')
        node_id = 'derived:' + digest({'label': label, 'parents': parents,
            'content': content, 'binding': self.binding.record()})
        self._nodes[node_id] = Node(node_id, self.binding, 'derived', parents)
        self.labels[label] = node_id
        return node_id

    def capture(self, tickers, raw, cache, theme_etfs, constituents):
        if self._captured:
            raise PublicationBlocked('RECORDER_ALREADY_CAPTURED')
        self._captured = True
        wanted = set(tickers)
        if set(raw) != wanted or set(cache) != wanted or set(self.handles) != wanted:
            raise PublicationBlocked('LINEAGE_UNSUPPORTED_ABSENT_OR_EXTRA_INPUT')
        self._theme_etfs = dict(theme_etfs)
        self._constituents = {k: tuple(v) for k, v in constituents.items()}
        self._cache = deepcopy(cache)
        self._configuration_hash = digest({'theme_etfs': theme_etfs, 'constituents': constituents,
            'baseline_blob': EXPECTED_BLOBS['sector_rotation.py']})
        inventory = []
        stats = {}
        for ticker in sorted(wanted):
            handle = self.handles[ticker]
            if handle.binding != self.binding or handle.symbol != ticker or handle.timeframe != '5m':
                raise PublicationBlocked('READ_BINDING_OR_TIMEFRAME_MISMATCH')
            if frame_digest(raw[ticker]) != handle.frame_hash:
                raise PublicationBlocked('CONSUMED_FRAME_HASH_MISMATCH')
            self._nodes[handle.node_id] = Node(handle.node_id, self.binding, 'source')
            inventory.append({'symbol': ticker, 'timeframe': handle.timeframe,
                'evidence_manifest_hash': handle.evidence_manifest_hash,
                'frame_hash': handle.frame_hash,
                'stats_outcome': 'AVAILABLE' if cache[ticker] else 'NONE'})
            stats[ticker] = self._derive(f'stats:{ticker}', (handle.node_id,), cache[ticker])
        self._inventory_json = json.dumps(inventory, sort_keys=True, separators=(',', ':'))
        if not cache.get('SPY'):
            raise PublicationBlocked('SPY_STATS_REQUIRED')
        self._stats = stats

    def emit(self, themes: pd.DataFrame, leaders: pd.DataFrame):
        if not self._captured or self.seal is not None:
            raise PublicationBlocked('RECORDER_NOT_READY_OR_ALREADY_SEALED')
        theme_rows, leader_rows = records(themes), records(leaders)
        theme_by_key = {row['theme']: row for row in theme_rows}
        leader_by_key = {(row['theme'], row['ticker']): row for row in leader_rows}
        expected_themes = {t for t, etf in self._theme_etfs.items() if self._cache[etf]}
        expected_leaders = {(t, s) for t in expected_themes for s in self._constituents[t]
                            if self._cache.get(s)}
        if (set(theme_by_key) != expected_themes or len(theme_by_key) != len(theme_rows)
                or set(leader_by_key) != expected_leaders or len(leader_by_key) != len(leader_rows)):
            raise PublicationBlocked('PRODUCER_OUTPUT_INVENTORY_MISMATCH')
        theme_nodes, stock_nodes = {}, {}
        for theme, etf in self._theme_etfs.items():
            cohort = self._constituents[theme]
            if not set(cohort) <= set(self._stats):
                raise PublicationBlocked('COHORT_EVIDENCE_MISSING')
            if theme not in theme_by_key:
                theme_nodes[theme] = self._derive('theme_skipped:' + theme,
                    (self._stats[etf],), {'skip': 'ETF_STATS_NONE'})
                continue
            payload = {k: v for k, v in theme_by_key[theme].items() if k != 'rotation_rank'}
            # All cohort members: positives, negatives, and stats=None exclusions.
            parents = [self._stats['SPY'], self._stats[etf]] + [self._stats[s] for s in cohort]
            theme_nodes[theme] = self._derive('theme_metric:' + theme, parents, payload)
            for symbol in cohort:
                key = (theme, symbol)
                if key not in leader_by_key:
                    continue
                payload = {k: v for k, v in leader_by_key[key].items() if k != 'rotation_rank'}
                stock_nodes[key] = self._derive(f'leader_metric:{theme}:{symbol}',
                    (theme_nodes[theme], self._stats[symbol], self._stats['SPY']), payload)
        # Each output row includes a global rank. Treat it as a live claim here;
        # even otherwise independent metric nodes inherit the shared rank input.
        theme_rank = self._derive('theme_rank', theme_nodes.values(),
            {'order': [r['theme'] for r in theme_rows], 'configuration': self._configuration_hash})
        leader_rank = self._derive('leader_rank', (*theme_nodes.values(), *stock_nodes.values()),
            {'order': [[r['theme'], r['ticker']] for r in leader_rows],
             'configuration': self._configuration_hash})
        row_bindings = []
        for dataset, rows in (('sector_rotation', theme_rows), ('rotation_leaders', leader_rows)):
            for ordinal, row in enumerate(rows):
                metric = theme_nodes[row['theme']] if dataset == 'sector_rotation' else stock_nodes[(row['theme'], row['ticker'])]
                ranking = theme_rank if dataset == 'sector_rotation' else leader_rank
                node = self._derive(f'output:{dataset}:{ordinal}', (metric, ranking), row)
                row_bindings.append(RowBinding(dataset, ordinal, digest(row), node))
        optional = frozenset(h.node_id for h in self.handles.values()
            if h.timeframe == '5m' and h.symbol in self.optional_5m_symbols)
        nodes = tuple(self._nodes[k] for k in sorted(self._nodes))
        policy = Policy(self.binding.policy_version, optional)
        self.seal = RotationSeal(self.binding, nodes, policy, tuple(row_bindings),
            (('sector_rotation', digest(theme_rows)), ('rotation_leaders', digest(leader_rows))),
            self._inventory_json, self._configuration_hash, graph_digest(nodes, policy))
