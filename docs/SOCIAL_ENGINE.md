# Market Hunt social engine

The social engine turns a completed, healthy Market Hunt scan into reviewable X
drafts. It is deliberately separate from scanner decisions and cannot influence
rankings, signals, alerts, portfolio allocation or broker execution.

## Safety and approval contract

- `scan_health` must report `PASS`; missing or failed health produces no drafts.
- Every draft contains a stable content ID and SHA-256 source fingerprint.
- Every draft is `DRAFT_REVIEW_REQUIRED`, `publish_authorized: false` and has no
  external post ID or scheduled timestamp.
- Drafts end with “Research only—not financial advice.”
- Guaranteed-return, risk-free, urgency and direct buy/sell claims are rejected.
- The engine never calls X, Metricool or any other external publishing API.

The CLI writes the `social_content_queue`, `social_content_calendar`, and
`social_engine_health.json`. The daily broad-scan workflow publishes these files
to the dashboard only after the source scan succeeds.

## Daily content system

| Suggested slot (ET) | Pillar | Purpose |
|---|---|---|
| 08:45 | Market pulse | Explain the strongest verified theme without predicting a return. |
| 10:15 | Setup breakdown | Show verified trigger, invalidation and R/R after the market opens. |
| 16:30 | Build in public | Share scanner coverage, abstentions and engineering progress. |

These are editorial suggestions, not automatic schedules. One to three drafts may
be produced depending on available evidence. A human must approve the final text
and timing before an external scheduling tool is used.

## Monetization path

1. Establish trust with consistent, evidence-linked educational posts.
2. Measure impressions, engagement, profile visits and qualified follower growth.
3. Use one transparent call to action: the free Market Hunt dashboard or waitlist.
4. Introduce a paid research tier only after sufficient forward-validation evidence.
5. Sell research access and workflow convenience—not promised returns or signals
   presented as certainty.

The first operating phase should remain approval-first. Metricool analytics can
later determine which pillars and times work best; scheduling remains a separate,
explicitly authorized action.
