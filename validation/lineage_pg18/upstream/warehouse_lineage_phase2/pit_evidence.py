"""Isolated, opt-in PIT-boundary evidence draft. NOT installed in Market Hunt.

The hook records the complete pandas SQL result BEFORE warehouse filtering.
It also records the visible instrument catalogue under the same explicit T0/xid
predicate. It does not return a FRESH verdict, relax quality/coverage, send alerts,
or write to a database. SQL execution is injected (real integration: read-only
pandas.read_sql_query using the caller's existing bounded connection).

SHA-256 seals detect accidental mutation. They are not signatures or proof that
an untrusted caller actually ran a query. Production needs trusted, append-only
persistence and database-backed snapshot-isolation tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import re
from threading import Lock
from typing import Any, Callable, Mapping
from uuid import UUID, uuid4

import pandas as pd
from pandas.testing import assert_frame_equal

from dependency_suppression import Binding, PublicationBlocked
from rotation_lineage import ReadHandle, frame_digest

SCHEMA = 'PIT_READ_MANIFEST_DRAFT_V1'
OHLCV = ('open', 'high', 'low', 'close', 'volume')
CAT_COLUMNS = ('revision_id', 'instrument_id', 'canonical_symbol', 'known_at', 'writer_xid')
OBS_COLUMNS = ('observation_id', 'instrument_id', 'ticker', 'data_type', 'timeframe',
               'event_timestamp', 'ingested_at', 'writer_xid', 'version_rank') + OHLCV


def typed(value: Any) -> Any:
    """Lossless review encoding of supported Python/pandas result values.

    Unlike display JSON, distinguish null, NaN, +/-Infinity, Decimal and float.
    This is NOT the production control-plane serializer and not DB wire bytes.
    """
    if value is None:
        return {'type': 'none'}
    if value is pd.NA:
        return {'type': 'pd.NA'}
    if value is pd.NaT:
        return {'type': 'pd.NaT'}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return {'type': 'datetime-naive', 'value': value.isoformat()}
        return {'type': 'datetime', 'value': pd.Timestamp(value).tz_convert('UTC').isoformat()}
    if isinstance(value, date):
        return {'type': 'date', 'value': value.isoformat()}
    if isinstance(value, Decimal):
        return {'type': 'decimal', 'value': str(value)}
    if isinstance(value, UUID):
        return {'type': 'uuid', 'value': str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {'type': 'bytes', 'value': bytes(value).hex()}
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise PublicationBlocked('EVIDENCE_NONSTRING_KEY')
        return {'type': 'map', 'value': [[k, typed(value[k])] for k in sorted(value)]}
    if isinstance(value, (tuple, list)):
        return {'type': type(value).__name__, 'value': [typed(v) for v in value]}
    if hasattr(value, 'item'):
        return typed(value.item())
    if isinstance(value, bool):
        return {'type': 'bool', 'value': value}
    if isinstance(value, int):
        return {'type': 'int', 'value': str(value)}
    if isinstance(value, float):
        return {'type': 'float', 'value': value.hex() if math.isfinite(value) else repr(value)}
    if isinstance(value, str):
        return {'type': 'str', 'value': value}
    raise PublicationBlocked(f'EVIDENCE_UNSUPPORTED_TYPE:{type(value).__name__}')


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def frame_record(frame: pd.DataFrame) -> dict:
    if not isinstance(frame, pd.DataFrame) or not frame.columns.is_unique:
        raise PublicationBlocked('EVIDENCE_FRAME_INVALID')
    if any(not isinstance(c, str) for c in frame.columns):
        raise PublicationBlocked('EVIDENCE_COLUMN_INVALID')
    return {'columns': list(frame.columns), 'dtypes': [str(d) for d in frame.dtypes],
            'index': typed(list(frame.index)), 'index_names': typed(list(frame.index.names)),
            'rows': [[typed(v) for v in row] for row in frame.itertuples(index=False, name=None)]}


def utc(value: Any) -> pd.Timestamp:
    try:
        out = pd.Timestamp(value)
        if pd.isna(out) or out.tzinfo is None:
            raise ValueError()
        return out.tz_convert('UTC')
    except (TypeError, ValueError) as exc:
        raise PublicationBlocked('EVIDENCE_CLOCK_INVALID') from exc


def integer_id(value: Any) -> str:
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)) or int(value) <= 0:
        raise PublicationBlocked('EVIDENCE_REVISION_ID_INVALID')
    return str(int(value))


@dataclass(frozen=True)
class ReadSeal:
    document_json: str
    content_hash: str

    @classmethod
    def build(cls, document: dict, max_bytes: int) -> 'ReadSeal':
        text = canonical(document)
        if len(text.encode()) > max_bytes:
            raise PublicationBlocked('EVIDENCE_BYTE_BUDGET_EXCEEDED')
        return cls(text, hashlib.sha256(text.encode()).hexdigest())

    def document(self, expected_hash: str) -> dict:
        if (expected_hash != self.content_hash or
                hashlib.sha256(self.document_json.encode()).hexdigest() != expected_hash):
            raise PublicationBlocked('READ_SEAL_HASH_MISMATCH')
        return json.loads(self.document_json)  # detached copy, not mutable seal state


class PITRecorder:
    """Opt-in local recorder. One immutable fragment per read; no shared JSON overwrite.

    read_sql is the real SQL function only when integrated into the repository.
    The included tests use a fake executor and cannot certify DB visibility.
    A failure record deliberately contains NO absence certificates and no error
    message that could expose a connection string. Do not persist manifests
    through an independent production connection until transaction rules exist.
    """
    def __init__(self, binding: Binding, read_sql: Callable, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 max_rows: int = 250_000, max_bytes: int = 64 * 1024 * 1024):
        if max_rows <= 0 or max_bytes <= 0:
            raise PublicationBlocked('EVIDENCE_BUDGET_INVALID')
        self.binding, self.read_sql, self.clock = binding, read_sql, clock
        self.max_rows, self.max_bytes = max_rows, max_bytes
        self._records: list[ReadSeal] = []
        self._lock = Lock()

    @property
    def records(self) -> tuple[ReadSeal, ...]:
        with self._lock:
            return tuple(self._records)

    def _append(self, seal: ReadSeal) -> None:
        with self._lock:
            self._records.append(seal)

    def read(self, sql, conn, params, *, req, as_of, visibility, lower,
             catalogue, catalogue_params, upper):
        """Called at point_in_time(), replacing only its SQL read when opted in.

        Keeps the original OHLCV SELECT and parameters verbatim. The extra
        catalogue SELECT has the exact same CTE, as_of and pg_snapshot parameters.
        Empty successful queries are captured before the original EMPTY error.
        """
        started = utc(self.clock())
        observed = utc(as_of)
        if visibility != self.binding.pg_snapshot or not visibility:
            raise PublicationBlocked('EVIDENCE_SNAPSHOT_MISMATCH')
        if observed > utc(self.binding.t0_utc) or started < utc(self.binding.t0_utc):
            raise PublicationBlocked('EVIDENCE_ANCHOR_MISMATCH')
        if req.timeframe not in {'1d', '5m'} or req.data_type != 'OHLCV':
            raise PublicationBlocked('EVIDENCE_REQUEST_UNSUPPORTED')
        tickers = [str(t).upper() for t in req.tickers if t]
        if not tickers or any(t != t.strip() or not t for t in tickers):
            raise PublicationBlocked('EVIDENCE_SYMBOL_INVALID')
        if tuple(catalogue_params) != (as_of, visibility):
            raise PublicationBlocked('EVIDENCE_CATALOGUE_BOUNDARY_MISMATCH')
        # Match the frozen function's exact positional bindings. This does not
        # parse arbitrary SQL or turn an untrusted SQL template into a proof.
        expected = tuple(catalogue_params) + (tickers, req.data_type, req.timeframe, upper, as_of)
        if lower is not None:
            expected += (lower,)
        expected += (visibility,)
        if typed(params) != typed(expected):
            raise PublicationBlocked('EVIDENCE_QUERY_PARAMETERS_MISMATCH')
        if utc(upper) > observed or (lower is not None and utc(lower) > utc(upper)):
            raise PublicationBlocked('EVIDENCE_QUERY_WINDOW_INVALID')
        if ('pg_visible_in_snapshot(o.writer_xid, %s::pg_snapshot)' not in sql or
                'instrument_catalogue_revision' not in catalogue):
            raise PublicationBlocked('EVIDENCE_PIT_QUERY_REQUIRED')
        cat_sql = (f'WITH {catalogue}\nSELECT revision_id,instrument_id,canonical_symbol,'
                   'known_at,writer_xid FROM visible_instrument '
                   'WHERE canonical_symbol = ANY(%s) ORDER BY canonical_symbol,instrument_id')
        cat_params = tuple(catalogue_params) + (tickers,)
        query = {'consumer': req.consumer, 'requested_tickers': tickers,
                 'data_type': req.data_type, 'timeframe': req.timeframe, 'period': req.period,
                 'as_of_utc': observed.isoformat(), 'event_upper_utc': utc(upper).isoformat(),
                 'event_lower_utc': utc(lower).isoformat() if lower is not None else None,
                 'sql': sql, 'parameters': typed(params), 'catalogue_sql': cat_sql,
                 'catalogue_parameters': typed(cat_params)}
        query_hash = digest(query)
        doc = {'schema': SCHEMA, 'read_id': str(uuid4()), 'binding': self.binding.record(),
               'started_at_utc': started.isoformat(), 'query': query, 'query_hash': query_hash,
               'absence_semantics': 'Only a completed scoped database read; never a no-trade/provider verdict.'}
        phase = 'OBSERVATION_QUERY'
        try:
            raw = self.read_sql(sql, conn, params=params)
            phase = 'CATALOGUE_QUERY'
            cat = self.read_sql(cat_sql, conn, params=cat_params)
            phase = 'CAPTURE_VALIDATION'
            if not isinstance(raw, pd.DataFrame) or not isinstance(cat, pd.DataFrame):
                raise PublicationBlocked('EVIDENCE_COMPLETE_DATAFRAME_REQUIRED')
            if len(raw) + len(cat) > self.max_rows:
                raise PublicationBlocked('EVIDENCE_ROW_BUDGET_EXCEEDED')
            inventory = self._inventory(raw, cat, query)
            raw_payload, cat_payload = frame_record(raw), frame_record(cat)
            finished = utc(self.clock())
            if finished < started:
                raise PublicationBlocked('EVIDENCE_CLOCK_REGRESSION')
            doc.update(outcome='COMPLETE_RESULT', completed_at_utc=finished.isoformat(),
                       raw_result=raw_payload, raw_result_hash=digest(raw_payload),
                       catalogue_result=cat_payload, catalogue_result_hash=digest(cat_payload),
                       symbols=inventory)
            seal = ReadSeal.build(doc, self.max_bytes)
        except BaseException as exc:
            # A timeout/error/partial result NEVER becomes a missing-data verdict.
            failure = {k: v for k, v in doc.items() if k not in {
                'raw_result', 'raw_result_hash', 'catalogue_result', 'catalogue_result_hash', 'symbols'}}
            failure.update(outcome='READ_OR_CAPTURE_FAILED', failure_phase=phase,
                           error_type=type(exc).__name__)
            # Failure summaries are small; no configured payload budget may
            # convert failed capture into a silently successful numerical read.
            self._append(ReadSeal.build(failure, max(self.max_bytes, 256 * 1024)))
            raise
        self._append(seal)
        return raw  # same object; no columns dropped, filled, sorted or relabelled

    def _inventory(self, raw, cat, query):
        wanted = set(query['requested_tickers'])
        if not set(CAT_COLUMNS) <= set(cat.columns):
            raise PublicationBlocked('EVIDENCE_CATALOGUE_SCHEMA_MISSING')
        if not set(OBS_COLUMNS) <= set(raw.columns):
            raise PublicationBlocked('EVIDENCE_OBSERVATION_SCHEMA_MISSING')
        upper, as_of = utc(query['event_upper_utc']), utc(query['as_of_utc'])
        lower = utc(query['event_lower_utc']) if query['event_lower_utc'] else None
        catalogue = {}
        cat_seen = set()
        for row in cat.to_dict('records'):
            symbol = str(row['canonical_symbol'])
            iid = integer_id(row['instrument_id'])
            if symbol not in wanted or iid in cat_seen or utc(row['known_at']) > as_of:
                raise PublicationBlocked('EVIDENCE_CATALOGUE_MISMATCH')
            cat_seen.add(iid)
            catalogue.setdefault(symbol, []).append({
                'instrument_id': iid, 'catalogue_revision_id': integer_id(row['revision_id']),
                'writer_xid': integer_id(row['writer_xid']), 'known_at_utc': utc(row['known_at']).isoformat()})
        by_symbol = {s: [] for s in wanted}
        observation_ids, logical_keys = set(), set()
        for ordinal, row in enumerate(raw.to_dict('records')):
            symbol = str(row['ticker'])
            oid, iid = integer_id(row['observation_id']), integer_id(row['instrument_id'])
            ts, ingested = utc(row['event_timestamp']), utc(row['ingested_at'])
            if symbol not in wanted or iid not in {r['instrument_id'] for r in catalogue.get(symbol, [])}:
                raise PublicationBlocked('EVIDENCE_OBSERVATION_CATALOGUE_MISMATCH')
            if row['data_type'] != query['data_type'] or row['timeframe'] != query['timeframe']:
                raise PublicationBlocked('EVIDENCE_OBSERVATION_KIND_MISMATCH')
            if (ts > upper or ingested > as_of or (lower is not None and ts < lower) or
                    (query['timeframe'] == '5m' and ts + pd.Timedelta(minutes=5) > as_of)):
                raise PublicationBlocked('EVIDENCE_OBSERVATION_OUTSIDE_PIT_WINDOW')
            key = (iid, row['data_type'], row['timeframe'], ts.isoformat())
            if oid in observation_ids or key in logical_keys or row['version_rank'] != 1:
                raise PublicationBlocked('EVIDENCE_DUPLICATE_OR_UNSELECTED_REVISION')
            observation_ids.add(oid); logical_keys.add(key)
            by_symbol[symbol].append({'ordinal': ordinal, 'observation_id': oid,
                'instrument_id': iid, 'writer_xid': integer_id(row['writer_xid']),
                'event_timestamp_utc': ts.isoformat(), 'ingested_at_utc': ingested.isoformat(),
                'selected_result_row_hash': digest(typed(row))})
        output = []
        for symbol in sorted(wanted):
            revisions = by_symbol[symbol]
            state = ('OBSERVATIONS_RETURNED' if revisions else
                     'NO_OBSERVATIONS_IN_SCOPED_RESULT' if catalogue.get(symbol) else
                     'NO_VISIBLE_CATALOGUE_MATCH')
            # Historical observations remain inputs; NO per-old-bar TTL applied.
            output.append({'symbol': symbol, 'timeframe': query['timeframe'], 'state': state,
                'catalogue': catalogue.get(symbol, []), 'revisions': revisions,
                'newest_event_timestamp_utc': max((r['event_timestamp_utc'] for r in revisions), default=None),
                'absence_certificate': None if revisions else {
                    'scope_query_hash': digest(query), 'state': state,
                    'interpretation': 'No matching rows under this query and pinned visibility; no trading/provider inference.'}})
        return output


def _validate_original(seal, expected_hash, binding, raw):
    doc = seal.document(expected_hash)
    if doc.get('outcome') != 'COMPLETE_RESULT' or doc.get('binding') != binding.record():
        raise PublicationBlocked('HISTORY_READ_BINDING_INVALID')
    if digest(frame_record(raw)) != doc['raw_result_hash']:
        raise PublicationBlocked('RAW_EVIDENCE_CHANGED_OR_PRUNED')
    return doc


def _bind_view(doc, expected_hash, binding, raw, symbol, consumed, output_timezone):
    source = next((s for s in doc['symbols'] if s['symbol'] == symbol), None)
    if source is None or source['state'] != 'OBSERVATIONS_RETURNED':
        raise PublicationBlocked('HISTORY_ABSENCE_IS_NOT_FRESHNESS')
    group = raw.loc[raw.ticker.eq(symbol)].copy()
    if group.instrument_id.nunique() != 1 or group.event_timestamp.duplicated().any():
        raise PublicationBlocked('HISTORY_INSTRUMENT_AMBIGUOUS')
    projected = group.loc[:, list(OHLCV)].rename(columns={c: c.title() for c in OHLCV})
    projected.index = pd.DatetimeIndex(pd.to_datetime(group.event_timestamp, utc=True)).tz_convert(output_timezone)
    projected.index.name = 'bar_timestamp'
    projected = projected.sort_index()
    try:
        assert_frame_equal(projected, consumed, check_exact=True, check_dtype=True)
    except AssertionError as exc:
        raise PublicationBlocked('HISTORY_PROJECTION_MISMATCH') from exc
    view_document = {'schema': 'OHLCV_VIEW_MANIFEST_DRAFT_V1', 'binding': binding.record(),
        'parent_read_hash': expected_hash, 'symbol': symbol, 'timeframe': source['timeframe'],
        'source_observation_ids': [r['observation_id'] for r in source['revisions']],
        'transformation': 'whole_symbol_ohlcv_sorted_v1', 'output_timezone': output_timezone,
        'consumed_frame_hash': frame_digest(consumed),
        'freshness_authorization': 'NONE: existing validation remains mandatory'}
    view = ReadSeal.build(view_document, 64 * 1024 * 1024)
    handle = ReadHandle(binding, symbol, source['timeframe'], view.content_hash, frame_digest(consumed))
    return handle, view


def bind_ohlcv_history(seal: ReadSeal, *, expected_hash: str, binding: Binding,
                       raw: pd.DataFrame, symbol: str, consumed: pd.DataFrame,
                       output_timezone: str = 'UTC') -> tuple[ReadHandle, ReadSeal]:
    """Bind a warehouse.frames-style OHLCV view to its raw manifest.

    Called AFTER existing quality/coverage/freshness checks, not instead of them.
    Current slice supports all returned rows for one unambiguous instrument.
    Arbitrary resampling, dropping bad bars and multi-instrument ticker merging
    are rejected until their own transformations are declared.
    """
    frozen = raw.copy(deep=True)
    doc = _validate_original(seal, expected_hash, binding, frozen)
    return _bind_view(doc, expected_hash, binding, frozen, symbol, consumed, output_timezone)


def bind_ohlcv_histories(seal: ReadSeal, *, expected_hash: str, binding: Binding,
                        raw: pd.DataFrame, consumed: Mapping[str, pd.DataFrame],
                        output_timezone: str = 'UTC') -> tuple[dict[str, ReadHandle], tuple[ReadSeal, ...]]:
    """Batch adapter: verify the full read once, not once per consumer symbol.

    Unconsumed/missing symbols still remain in the parent read manifest. This
    adapter does not decide whether they are legally optional or quarantine them.
    """
    frozen = raw.copy(deep=True)
    doc = _validate_original(seal, expected_hash, binding, frozen)
    handles, views = {}, []
    for symbol, frame in consumed.items():
        handle, view = _bind_view(doc, expected_hash, binding, frozen, symbol, frame, output_timezone)
        handles[symbol] = handle
        views.append(view)
    return handles, tuple(views)
