# Architecture

1. **Input loader** reads the supplied event history, latest profile snapshot per user, catalogue, episode map, randomized arms, goal rules, intervention rules, replay triggers, controls and scripted model faults.
2. **Event-sourced evidence** stores per-user events and deduplicates by `event_id`. `received_at`/`deliver_at` are the trusted arrival clocks; phone `occurred_at` and `sent_at` are not used to order or establish freshness.
3. **Fact precedence** uses the current trigger's `app_dialect` for the page and records disagreement with the profile. A paid purchase received after a profile update supersedes that stale subscription status for goal selection. Other unresolved conflicts should lead to abstention rather than invented facts.
4. **Goal selector** deterministically checks a supported subset of measurable goals and retains one active goal per viewer while its window is open. Goals with missing or ambiguous success signals (G10) and unsupported signals (G11) are not selected.
5. **Intervention builder** creates a dialect-specific home page only from currently available catalogue titles, deduplicated by `content_slug`. Rows with fewer than three titles are omitted; at most six rows are emitted.
6. **Safety gate** blocks all actions for randomized holdout users. Messaging is disabled in this baseline, so no push/WhatsApp can violate consent, quiet hours, frequency caps or approved-template rules.
7. **Failure recovery** uses an offline deterministic/mock replay path without an API key. Scripted malformed/unsupported/timeout faults, model-provider outages and degraded data-store controls fail closed to no action. Control changes are recorded in `control_audit.json`.
8. **Memory and audit** write per-viewer state, decision outputs, evidence event IDs with timestamps, dialect conflicts, action records, goal commitments, outcome judgments and latency metrics.
9. **Outcome attribution** matches G07 play events to the committed target `content_slug`. The primary impact measure is intention-to-treat across all randomized agent and holdout users, including users who received no action; served-vs-unserved pilot comparisons are treated as biased.

All runtime dependencies are from the Python standard library. This implementation uses a deterministic rules/mock provider rather than a live LLM so evaluators can reproduce the replay without credentials. Model-like outputs are never accepted as evidence without pack-backed facts.
