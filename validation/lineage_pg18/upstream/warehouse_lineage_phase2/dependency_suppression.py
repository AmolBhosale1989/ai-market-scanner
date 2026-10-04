"""Isolated review draft: propagate certified source expiry through a DAG.

NOT WIRED INTO MARKET HUNT. This module performs no database, provider, GitHub,
Render, or filesystem I/O. It neither changes coverage nor calculates source
freshness. Existing calendar/PIT/quality gates must supply same-check verdicts.

A source identity MUST distinguish timeframe and evidence revision, e.g.
'ohlcv:SKYY:5m:revision-A' versus 'ohlcv:SKYY:1d:revision-B'. The adapter must
record all dependencies (including universe-wide ranks, not only theme_etf).
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from graphlib import CycleError, TopologicalSorter
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence
from uuid import UUID


class PublicationBlocked(ValueError):
    """Reject the candidate projection; do not advance its publication pointer."""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PublicationBlocked('CLOCK_INVALID: timezone-aware datetime required')
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Binding:
    run_id: str
    t0_utc: datetime
    pg_snapshot: str
    source_commit: str
    policy_version: str

    def __post_init__(self) -> None:
        try:
            UUID(self.run_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise PublicationBlocked('RUN_ID_INVALID') from exc
        object.__setattr__(self, 't0_utc', _utc(self.t0_utc))
        if not isinstance(self.pg_snapshot, str) or not self.pg_snapshot.strip():
            raise PublicationBlocked('SNAPSHOT_MISSING')
        if not isinstance(self.source_commit, str) or not re.fullmatch(r'[0-9a-f]{40}', self.source_commit):
            raise PublicationBlocked('COMMIT_INVALID')
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise PublicationBlocked('POLICY_VERSION_MISSING')

    def record(self) -> dict[str, str]:
        return dict(run_id=self.run_id, t0_utc=self.t0_utc.isoformat(),
                    pg_snapshot=self.pg_snapshot, source_commit=self.source_commit,
                    policy_version=self.policy_version)


@dataclass(frozen=True)
class Node:
    node_id: str
    binding: Binding
    kind: Literal['source', 'derived']
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceVerdict:
    binding: Binding
    checked_at_utc: datetime
    state: Literal['FRESH', 'EXPIRED', 'INVALID', 'MISSING']


@dataclass(frozen=True)
class Policy:
    version: str
    optional_sources: frozenset[str]


@dataclass(frozen=True)
class Decision:
    binding: Binding
    checked_at_utc: datetime
    graph_hash: str
    # Each value is the sorted set of expired optional roots affecting the node.
    blocked_by: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class FeedSection:
    source_dataset: str
    limit: int


@dataclass(frozen=True)
class Projection:
    datasets: dict[str, list[dict[str, Any]]]
    audit: dict[str, Any]


def graph_digest(nodes: Sequence[Node], policy: Policy) -> str:
    """Producer-side seal; publisher must use a previously verified saved hash.

    A hash of a graph supplied by the same untrusted caller is not proof of
    provenance. Persistence, row hashes and PostgreSQL snapshot visibility
    remain the integration layer's responsibility.
    """
    manifest = {
        'policy': {'version': policy.version,
                   'optional_sources': sorted(policy.optional_sources)},
        'nodes': [dict(id=n.node_id, kind=n.kind, binding=n.binding.record(),
                       depends_on=sorted(n.depends_on))
                  for n in sorted(nodes, key=lambda n: n.node_id)],
    }
    raw = json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def evaluate_dependencies(
    nodes: Sequence[Node], verdicts: Mapping[str, SourceVerdict], *,
    binding: Binding, policy: Policy, checked_at_utc: datetime,
    expected_graph_hash: str,
) -> Decision:
    """Propagate expired optional roots; reject all incomplete evidence.

    Only explicit EXPIRED verdicts are excludable under this initial draft.
    Invalid, missing and unknown source states block even for optional roots.
    Roots outside the fixed optional set remain required. Their expiry blocks.
    """
    checked = _utc(checked_at_utc)
    if checked < binding.t0_utc:
        raise PublicationBlocked('CLOCK_BEFORE_T0')
    if policy.version != binding.policy_version:
        raise PublicationBlocked('POLICY_VERSION_MISMATCH')
    by_id: dict[str, Node] = {}
    for node in nodes:
        if not isinstance(node.node_id, str) or not node.node_id.strip():
            raise PublicationBlocked('NODE_ID_MISSING')
        if node.node_id in by_id:
            raise PublicationBlocked(f'NODE_DUPLICATE: {node.node_id}')
        if node.binding != binding:
            raise PublicationBlocked(f'LINEAGE_BINDING_MISMATCH: {node.node_id}')
        if not isinstance(node.depends_on, tuple) or any(
            not isinstance(x, str) or not x.strip() for x in node.depends_on
        ):
            raise PublicationBlocked(f'DEPENDENCIES_INVALID: {node.node_id}')
        if len(set(node.depends_on)) != len(node.depends_on):
            raise PublicationBlocked(f'DEPENDENCIES_DUPLICATE: {node.node_id}')
        if node.kind not in {'source', 'derived'}:
            raise PublicationBlocked(f'NODE_KIND_INVALID: {node.node_id}')
        if node.kind == 'source' and node.depends_on:
            raise PublicationBlocked(f'SOURCE_HAS_DEPENDENCIES: {node.node_id}')
        if node.kind == 'derived' and not node.depends_on:
            raise PublicationBlocked(f'DERIVED_LINEAGE_MISSING: {node.node_id}')
        by_id[node.node_id] = node
    if not by_id:
        raise PublicationBlocked('GRAPH_EMPTY')
    sources = {k for k, n in by_id.items() if n.kind == 'source'}
    if set(verdicts) != sources:
        raise PublicationBlocked('SOURCE_VERDICT_INVENTORY_MISMATCH')
    if not policy.optional_sources <= sources:
        raise PublicationBlocked('OPTIONAL_SOURCE_UNKNOWN')
    all_ids = set(by_id)
    graph: dict[str, tuple[str, ...]] = {}
    for key in sorted(by_id):
        deps = by_id[key].depends_on
        if not set(deps) <= all_ids:
            raise PublicationBlocked(f'LINEAGE_NODE_MISSING: {key}')
        graph[key] = tuple(sorted(deps))
    try:
        order = tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise PublicationBlocked('LINEAGE_CYCLE') from exc
    digest = graph_digest(nodes, policy)
    if digest != expected_graph_hash:
        raise PublicationBlocked('GRAPH_HASH_MISMATCH')
    blocked: dict[str, tuple[str, ...]] = {}
    for key in order:
        node = by_id[key]
        if node.kind == 'source':
            verdict = verdicts[key]
            if verdict.binding != binding:
                raise PublicationBlocked(f'SOURCE_BINDING_MISMATCH: {key}')
            if _utc(verdict.checked_at_utc) != checked:
                raise PublicationBlocked(f'SOURCE_CHECK_TIME_MISMATCH: {key}')
            if verdict.state == 'FRESH':
                blocked[key] = ()
            elif verdict.state == 'EXPIRED' and key in policy.optional_sources:
                blocked[key] = (key,)
            elif verdict.state == 'EXPIRED':
                raise PublicationBlocked(f'REQUIRED_SOURCE_EXPIRED: {key}')
            else:
                raise PublicationBlocked(f'SOURCE_EVIDENCE_INVALID: {key}/{verdict.state}')
        else:
            blocked[key] = tuple(sorted({root for dep in node.depends_on for root in blocked[dep]}))
    return Decision(binding, checked, digest, MappingProxyType(blocked))


def project_current_outputs(
    datasets: Mapping[str, list[dict[str, Any]]], decision: Decision, *,
    dataset_roles: Mapping[str, Literal['current', 'historical', 'diagnostic', 'feed']],
    feed_sections: Mapping[str, FeedSection],
    feed_diagnostic_keys: frozenset[str] = frozenset(),
    lineage_field: str = 'lineage_node_id',
) -> Projection:
    """Copy and suppress current rows; rebuild feed arrays from their sources.

    All current rows must carry explicit producer-generated lineage, even when
    all sources are fresh. Every supplied dataset must have an explicit role.
    Historical/diagnostic rows are retained, not turned into current signals.
    Known noncurrent feed fields must be explicitly inventoried by the adapter.
    No original datasets, scores, prices or observation times are rewritten.
    """
    if set(dataset_roles) != set(datasets):
        raise PublicationBlocked('DATASET_ROLE_INVENTORY_MISMATCH')
    if any(role not in {'current', 'historical', 'diagnostic', 'feed'}
           for role in dataset_roles.values()):
        raise PublicationBlocked('DATASET_ROLE_INVALID')
    if set(feed_sections) & feed_diagnostic_keys:
        raise PublicationBlocked('FEED_ROLE_CONFLICT')
    for key, section in feed_sections.items():
        if (not isinstance(key, str) or not key or
            type(section.limit) is not int or section.limit < 1 or
            dataset_roles.get(section.source_dataset) != 'current'):
            raise PublicationBlocked(f'FEED_SOURCE_INVALID: {key}')
    out: dict[str, list[dict[str, Any]]] = {}
    excluded: list[dict[str, Any]] = []
    for name in sorted(datasets):
        rows = datasets[name]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise PublicationBlocked(f'DATASET_SHAPE_INVALID: {name}')
        if dataset_roles[name] != 'current':
            out[name] = deepcopy(rows)
            continue
        retained: list[dict[str, Any]] = []
        for ordinal, row in enumerate(rows):
            node_id = row.get(lineage_field)
            if not isinstance(node_id, str) or node_id not in decision.blocked_by:
                raise PublicationBlocked(f'ROW_LINEAGE_MISSING: {name}/{ordinal}')
            roots = decision.blocked_by[node_id]
            if roots:
                excluded.append(dict(dataset=name, row_ordinal=ordinal, node_id=node_id,
                                     roots=list(roots), reason='EXPIRED_OPTIONAL_DEPENDENCY'))
            else:
                retained.append(deepcopy(row))
        out[name] = retained
    for name in sorted(datasets):
        if dataset_roles[name] != 'feed':
            continue
        if not feed_sections:
            raise PublicationBlocked('FEED_SECTION_INVENTORY_MISSING')
        for original, projected in zip(datasets[name], out[name]):
            market = original.get('market_hunt')
            if not isinstance(market, dict):
                raise PublicationBlocked('FEED_MARKET_HUNT_INVALID')
            if not set(feed_sections) <= set(market):
                raise PublicationBlocked('FEED_SECTION_MISSING')
            if set(market) - set(feed_sections) - feed_diagnostic_keys:
                raise PublicationBlocked('FEED_SECTION_UNREGISTERED')
            rebuilt = deepcopy(market)
            for key, section in feed_sections.items():
                # Never silently accept a stale/orphan embedded copy.
                expected = datasets[section.source_dataset][:section.limit]
                if market[key] != expected:
                    raise PublicationBlocked(f'FEED_SOURCE_MISMATCH: {key}')
                rebuilt[key] = deepcopy(out[section.source_dataset][:section.limit])
            projected['market_hunt'] = rebuilt
    expired_roots = sorted({root for roots in decision.blocked_by.values() for root in roots})
    report = dict(binding=decision.binding.record(),
                  checked_at_utc=decision.checked_at_utc.isoformat(),
                  graph_hash=decision.graph_hash,
                  dependency_status='DEGRADED_DEPENDENCIES' if expired_roots else 'DEPENDENCIES_OK',
                  expired_optional_roots=expired_roots, exclusions=excluded,
                  remaining_rows={k: len(v) for k, v in sorted(out.items())
                                  if dataset_roles[k] == 'current'})
    return Projection(out, report)
