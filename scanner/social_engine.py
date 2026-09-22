from __future__ import annotations

import argparse
from .control_plane import read_dataset, write_dataset, write_record
from .social.engine import build_social_content


def run():
    queue, calendar, health = build_social_content(
        read_dataset("scan_health", required=False),
        read_dataset("all_candidates", required=False),
        read_dataset("recommended_trades", required=False),
        read_dataset("trending_themes", required=False),
    )
    write_dataset("social_content_queue", queue, entity_key=None)
    write_dataset("social_content_calendar", calendar, entity_key=None)
    write_record("social_engine_health", health)
    print(
        f"Social engine: status={health['status']} drafts={health['drafts_generated']} "
        "external_actions=0"
    )
    return queue, calendar, health


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate approval-first Market Hunt social drafts")
    parser.parse_args()
    run()
