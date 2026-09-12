# Personal Validation and Limited-Live Runbook

## Current operating phase

The scanner remains in PAPER_VALIDATION until the automated readiness gate passes.
Broker order execution stays disabled.

## Automated evidence collection

- Full universe scan: weekdays at 12:30 UTC.
- Intraday monitor: every 15 minutes, 12:00–22:59 UTC, weekdays.
- Forward validation report: weekdays at 22:30 UTC.
- Dashboard reads the published scan-data branch and refreshes every 60 seconds.

## Promotion gate

Every condition must pass:

| Check | Minimum |
|---|---:|
| Closed paper signals | 30 |
| Win rate | 45% |
| Average R multiple | +0.25 R |
| Profit factor | 1.20 |
| Usable probability buckets | 1 |
| Monitor health | PASS / OK / HEALTHY |

The gate output is published as dashboard-data/validation_gate.csv on the scan-data branch.
A failed gate is normal while samples accumulate and is not a software failure.

## Paper-validation protocol

1. Do not manually remove losing signals.
2. Keep entries, stops and targets fixed at the first actionable state.
3. Record gaps and slippage separately.
4. Review performance by setup, market regime, theme and catalyst status.
5. Do not promote probabilities from score buckets marked provisional.
6. Require at least 2–4 weeks of market sessions even if 30 samples arrive sooner.

## Limited-live test — only after the gate passes

- Starting account reference: USD 5,000.
- Maximum planned account risk per trade: 0.50% (USD 25).
- Maximum simultaneous positions: 2.
- No averaging down.
- No trade when the live state is failed, invalidated or stale.
- Recheck spreads, earnings/news and actual order price before entry.
- Stop live testing after three consecutive losses or a 3% account drawdown and return to paper validation.
- Review after 20 limited-live trades before increasing risk.
- Broker automation remains disabled; the user makes every final order decision.

## Commercial gate

Do not start multi-user SaaS, billing or marketing work until paper validation and the first
limited-live review both pass.