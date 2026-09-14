from datetime import datetime, timezone

import pandas as pd

from scanner.social.engine import DISCLAIMER, SocialEngineSettings, build_social_content


NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def healthy():
    return pd.DataFrame([{
        "status": "PASS",
        "analyzable_symbols": 1234,
        "analyzable_coverage": 0.91,
        "technical_candidate_rows": 42,
    }])


def inputs():
    candidates = pd.DataFrame([{"ticker": "AAA"}, {"ticker": "BBB"}])
    recommendations = pd.DataFrame([{
        "ticker": "AAA", "stage": "CONFIRMED", "entry_trigger": 10.25,
        "stop": 9.75, "effective_rr": 3.2, "theme": "Semiconductors",
        "market_hunt_score": 88,
    }])
    themes = pd.DataFrame([{
        "theme": "Semiconductors", "theme_state": "LEADING", "theme_score": 81,
        "theme_rank": 1, "etf": "SMH",
    }])
    return candidates, recommendations, themes


def test_unhealthy_scan_blocks_every_draft_and_external_action():
    candidates, recommendations, themes = inputs()
    queue, calendar, health = build_social_content(
        pd.DataFrame([{"status": "FAIL"}]), candidates, recommendations, themes, now=NOW
    )
    assert queue.empty
    assert calendar.empty
    assert health["status"] == "BLOCKED_UNHEALTHY_SCAN"
    assert health["publish_authorized"] is False
    assert health["external_actions_taken"] == 0


def test_healthy_scan_creates_traceable_approval_only_drafts():
    candidates, recommendations, themes = inputs()
    queue, calendar, health = build_social_content(healthy(), candidates, recommendations, themes, now=NOW)
    assert set(queue["content_type"]) == {"MARKET_PULSE", "SETUP_BREAKDOWN", "BUILD_IN_PUBLIC"}
    assert queue["status"].eq("DRAFT_REVIEW_REQUIRED").all()
    assert queue["approval_required"].all()
    assert ~queue["publish_authorized"].any()
    assert queue["external_post_id"].eq("").all()
    assert queue["source_fingerprint"].str.len().eq(64).all()
    assert queue["draft_text"].str.endswith(DISCLAIMER).all()
    assert queue["char_count"].le(270).all()
    assert health["external_actions_taken"] == 0
    assert len(calendar) == len(queue)


def test_setup_draft_uses_exact_verified_levels_and_stable_identity():
    candidates, recommendations, themes = inputs()
    first, _, _ = build_social_content(healthy(), candidates, recommendations, themes, now=NOW)
    second, _, _ = build_social_content(healthy(), candidates, recommendations, themes, now=NOW)
    setup = first[first["content_type"].eq("SETUP_BREAKDOWN")].iloc[0]
    assert "$AAA" in setup["draft_text"]
    assert "10.25" in setup["draft_text"]
    assert "9.75" in setup["draft_text"]
    assert "3.2:1" in setup["draft_text"]
    assert list(first["content_id"]) == list(second["content_id"])


def test_character_limit_preserves_disclaimer():
    candidates, recommendations, themes = inputs()
    themes.loc[0, "theme"] = "Very Long Theme Name " * 20
    queue, _, _ = build_social_content(
        healthy(), candidates, recommendations, themes, now=NOW,
        settings=SocialEngineSettings(max_chars=180),
    )
    assert queue["char_count"].le(180).all()
    assert queue["draft_text"].str.endswith(DISCLAIMER).all()


def test_forbidden_claim_in_source_is_not_emitted():
    candidates, recommendations, themes = inputs()
    themes.loc[0, "theme"] = "Guaranteed Profit"
    queue, _, health = build_social_content(healthy(), candidates, recommendations, themes, now=NOW)
    assert not queue["draft_text"].str.contains("guaranteed", case=False).any()
    assert health["drafts_rejected_by_policy"] == 1
