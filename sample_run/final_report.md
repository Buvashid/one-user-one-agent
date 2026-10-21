# One User, One Agent - replay report

## Implementation summary

The deterministic offline agent processes app-open triggers in delivery order, keeps per-viewer goal/evidence state, records source conflicts, and publishes a dialect-specific home page only when safety checks pass. Holdout users receive no actions. Messaging is disabled in this baseline.

## Validation

- Decisions: 2558
- Home-page actions accepted by simulator: 747
- Simulator policy problems: 0
- Automated tests: run `python -m unittest discover -s tests -v` (17 tests passed)
- Processing latency: mean 1.315 ms, p95 4.15 ms, max 59.689 ms; target under 60,000 ms.

## Impact

- Agent-arm play rate: 66.9% (577/863)
- Holdout play rate: 64.9% (573/883)
- Absolute difference: +1.97 percentage points (approximate 95% CI: [-4.32, +8.23] pp).

This is descriptive for the supplied synthetic replay and simulator seed. It does not establish statistical significance or prove causal uplift. The randomized intention-to-treat comparison is preferred to the biased served-versus-rest pilot headline.

## Limitations

See `../KNOWN_LIMITATIONS.md`. The deterministic mock path avoids API credentials; no push/WhatsApp messages are sent; goal eligibility covers a supported subset; and outcomes without attributable evidence remain failed or inconclusive according to observed-window coverage.


