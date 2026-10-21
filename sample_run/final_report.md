# One User, One Agent — replay report

## Implementation summary

The deterministic offline agent processes every trigger in delivery order, correlates login_success and app_open by session, keeps per-viewer goal/evidence state, records source conflicts, and publishes a dialect-specific home page only when safety checks pass. Holdout users receive no actions. Messaging is disabled in this baseline.

## Validation

- Decisions: 2769
- Home-page actions accepted by simulator: 703
- Simulator policy problems: 0
- Automated tests: run `python -m unittest discover -s tests -v` (24 test cases)
- Processing latency: mean 1.079 ms, p95 3.733 ms, max 10.491 ms; target under 60,000 ms.

## Impact

- Agent-arm play rate: 67.0% (578/863)
- Holdout play rate: 64.9% (573/883)
- Absolute difference: +2.09 percentage points (approximate 95% CI: [-4.20, +8.35] pp).

This is descriptive for the supplied synthetic replay and simulator seed. It does not establish statistical significance or prove causal uplift. The randomized intention-to-treat comparison is preferred to the biased served-versus-rest pilot headline.

## Limitations

See `../KNOWN_LIMITATIONS.md`. The deterministic mock path avoids API credentials; no push/WhatsApp messages are sent; goal eligibility covers a supported subset; and outcomes without attributable evidence remain failed or inconclusive according to observed-window coverage.
