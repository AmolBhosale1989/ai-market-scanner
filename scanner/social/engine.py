from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any

import pandas as pd


SOCIAL_SCHEMA = "1.0.0-approval-first"
DISCLAIMER = "Research only—not financial advice."
FORBIDDEN_CLAIMS = re.compile(
    r"\b(guaranteed?|risk[- ]?free|sure[- ]?shot|can't lose|cannot lose|buy now|sell now|"
    r"will (?:rise|gain|moon)|profit guaranteed)\b",
    re.IGNORECASE,
)
QUEUE_COLUMNS = [
    "content_id", "platform", "content_type", "draft_text", "char_count", "status",
    "approval_required", "publish_authorized", "approved_at_utc", "scheduled_at_utc",
    "suggested_slot_et", "external_post_id", "source_dataset", "source_ticker",
    "source_fingerprint", "generated_at_utc", "research_only",
]
CALENDAR_COLUMNS = [
    "content_id", "platform", "content_type", "suggested_slot_et", "status",
    "approval_required", "publish_authorized", "draft_text",
]


@dataclass(frozen=True)
class SocialEngineSettings:
    platform: str = "X"
    max_chars: int = 270
    max_setup_posts: int = 1
    approval_required: bool = True


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _label(value: Any, fallback: str = "the market") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9&+./$ -]", "", str(value or "")).strip()
    return cleaned[:48] or fallback


def _price(value: Any) -> str:
    number = _number(value, float("nan"))
    return f"{number:.2f}" if math.isfinite(number) and number > 0 else "not available"


def _fit(body: str, max_chars: int) -> str:
    suffix = f" {DISCLAIMER}"
    body = " ".join(body.split()).strip()
    if len(body) + len(suffix) <= max_chars:
        return body + suffix
    room = max(0, max_chars - len(suffix) - 1)
    shortened = body[:room].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return f"{shortened}…{suffix}"


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _draft(
    content_type: str,
    body: str,
    source_dataset: str,
    source: dict[str, Any],
    slot_et: str,
    now: datetime,
    settings: SocialEngineSettings,
) -> dict[str, Any] | None:
    text = _fit(body, settings.max_chars)
    if FORBIDDEN_CLAIMS.search(text):
        return None
    source_fingerprint = _fingerprint(source)
    content_id = "mhx-" + _fingerprint({"type": content_type, "source": source_fingerprint, "text": text})[:16]
    return {
        "content_id": content_id,
        "platform": settings.platform,
        "content_type": content_type,
        "draft_text": text,
        "char_count": len(text),
        "status": "DRAFT_REVIEW_REQUIRED",
        "approval_required": settings.approval_required,
        "publish_authorized": False,
        "approved_at_utc": "",
        "scheduled_at_utc": "",
        "suggested_slot_et": slot_et,
        "external_post_id": "",
        "source_dataset": source_dataset,
        "source_ticker": str(source.get("ticker", "")),
        "source_fingerprint": source_fingerprint,
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "research_only": True,
    }


def build_social_content(
    scan_health: pd.DataFrame,
    candidates: pd.DataFrame,
    recommendations: pd.DataFrame,
    themes: pd.DataFrame,
    now: datetime | None = None,
    settings: SocialEngineSettings | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Create evidence-linked X drafts; never publish or authorize them."""
    settings = settings or SocialEngineSettings()
    now = now or datetime.now(timezone.utc)
    health_row = scan_health.iloc[0].to_dict() if scan_health is not None and not scan_health.empty else {}
    if str(health_row.get("status", "")).upper() != "PASS":
        health = {
            "schema_version": SOCIAL_SCHEMA,
            "status": "BLOCKED_UNHEALTHY_SCAN",
            "reason": "verified scan health dataset is absent or not PASS",
            "drafts_generated": 0,
            "publish_authorized": False,
            "external_actions_taken": 0,
            "settings": asdict(settings),
        }
        return pd.DataFrame(columns=QUEUE_COLUMNS), pd.DataFrame(columns=CALENDAR_COLUMNS), health

    drafts: list[dict[str, Any]] = []
    rejected = 0
    if themes is not None and not themes.empty:
        ranked_themes = themes.copy()
        if "theme_rank" in ranked_themes:
            ranked_themes = ranked_themes.sort_values("theme_rank")
        top = ranked_themes.iloc[0].to_dict()
        theme = _label(top.get("theme"))
        state = _label(top.get("theme_state"), "active").lower()
        score = _number(top.get("theme_score"))
        item = _draft(
            "MARKET_PULSE",
            f"Market Hunt pulse: {theme} is {state} ({score:.0f}/100) in the latest verified scan. Watching relative strength and clean risk/reward—not chasing extension.",
            "trending_themes",
            {key: top.get(key) for key in ("theme", "theme_state", "theme_score", "theme_rank", "etf")},
            "08:45 ET",
            now,
            settings,
        )
        if item:
            drafts.append(item)
        else:
            rejected += 1

    setup_source = recommendations if recommendations is not None and not recommendations.empty else pd.DataFrame()
    if not setup_source.empty:
        if "market_hunt_score" in setup_source:
            setup_source = setup_source.sort_values("market_hunt_score", ascending=False)
        for _, series in setup_source.head(settings.max_setup_posts).iterrows():
            row = series.to_dict()
            ticker = _label(row.get("ticker"), "Ticker")
            stage = _label(row.get("stage"), "research watch")
            trigger = _price(row.get("entry_trigger"))
            stop = _price(row.get("stop"))
            rr = _number(row.get("effective_rr"))
            theme = _label(row.get("theme"))
            item = _draft(
                "SETUP_BREAKDOWN",
                f"${ticker} research watch: {stage}. Trigger {trigger}; invalidation {stop}; R/R {rr:.1f}:1; theme {theme}. No prediction—the levels define the test.",
                "recommended_trades",
                {key: row.get(key) for key in ("ticker", "stage", "entry_trigger", "stop", "effective_rr", "theme", "market_hunt_score")},
                "10:15 ET",
                now,
                settings,
            )
            if item:
                drafts.append(item)
            else:
                rejected += 1

    candidate_count = len(candidates) if candidates is not None else 0
    recommendation_count = len(recommendations) if recommendations is not None else 0
    source = {
        "analyzable_symbols": health_row.get("analyzable_symbols"),
        "analyzable_coverage": health_row.get("analyzable_coverage"),
        "technical_candidate_rows": health_row.get("technical_candidate_rows", candidate_count),
        "recommendation_count": recommendation_count,
    }
    item = _draft(
        "BUILD_IN_PUBLIC",
        f"Market Hunt scan complete: {int(_number(source['analyzable_symbols'])):,} symbols analyzed, {candidate_count:,} evidence-backed candidates, {recommendation_count} live-confirmed setups. The engine can abstain when evidence is weak.",
        "scan_health + all_candidates + recommended_trades",
        source,
        "16:30 ET",
        now,
        settings,
    )
    if item:
        drafts.append(item)
    else:
        rejected += 1

    queue = pd.DataFrame(drafts, columns=QUEUE_COLUMNS)
    calendar = queue[CALENDAR_COLUMNS].copy()
    health = {
        "schema_version": SOCIAL_SCHEMA,
        "status": "DRAFTS_READY" if len(queue) else "NO_SAFE_DRAFTS",
        "reason": "human approval required before scheduling or publishing",
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "drafts_generated": len(queue),
        "drafts_rejected_by_policy": rejected,
        "publish_authorized": False,
        "external_actions_taken": 0,
        "source_scan_status": "PASS",
        "settings": asdict(settings),
    }
    return queue, calendar, health
