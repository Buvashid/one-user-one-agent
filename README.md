# One User, One Agent — implementation

Evidence-first, deterministic OTT lifecycle agent for the supplied synthetic pack. Python 3.9+; Python standard library only. No API key or external model is required: deterministic replay mode and the supplied scripted-fault stream make the project reproducible offline.

## Run the complete replay

Run these commands from the repository root (the folder containing `src/` and `one_user_one_agent_pack/`):

```powershell
python src/agent.py --pack one_user_one_agent_pack --out sample_run
python one_user_one_agent_pack/simulator/simulate_outcomes.py --pack one_user_one_agent_pack --actions sample_run/actions.jsonl --out sample_run/sim
python src/analyze_outcomes.py --pack one_user_one_agent_pack --out sample_run
python -m unittest discover -s tests -v
```

The replay writes decisions, actions, goal commitments, durable viewer state, latency metrics, and a control audit. The simulator produces outcome events. The analyzer attributes goal outcomes and computes the randomized intention-to-treat comparison. Outputs are deterministic apart from measured processing latency.

## Decision and safety rules

- One active goal is retained per viewer while its window remains open.
- `deliver_at` and server `received_at` are used for replay ordering and evidence freshness; device `occurred_at`/`sent_at` values are retained as context but are not trusted as the arrival clock.
- Duplicate event IDs and duplicate trigger IDs are idempotent.
- Current trigger `app_dialect` takes precedence for the current page; conflicts with the profile dialect are recorded explicitly.
- A trustworthy paid purchase received after the profile's update timestamp supersedes a stale subscription snapshot for goal eligibility.
- Holdout users receive zero actions. Provider faults and dependency outages/degradation fail closed to no action.
- Catalogue content must be available at decision time, match the current dialect, and be deduplicated by `content_slug`; rows contain 3–10 titles and at most six rows.
- Push/WhatsApp messaging is disabled in this baseline, avoiding consent, quiet-hour, frequency-cap and template risks.
- The G03 rule correction is applied when its control event arrives and recorded in `sample_run/control_audit.json`.
- G07 stores the target `content_slug`; a play on a different series does not count as success.

## Outputs

- `sample_run/decisions.jsonl` — decision, evidence references, dialect conflicts, confidence rationale, goal deadline, checks and no-action reasons.
- `sample_run/viewer_state/state.json` — latest per-viewer replay state.
- `sample_run/actions.jsonl` — published home-page actions (holdout has none).
- `sample_run/outcomes.jsonl` — goal commitments and attributed outcomes.
- `sample_run/latency_report.json` — mean, p95 and maximum processing time.
- `sample_run/control_audit.json` — corrected goal rule and dependency incident audit.
- `sample_run/sim/` — simulator events and policy validation report.
- `sample_run/impact_report.md` — randomized intention-to-treat comparison and caveats.
- `sample_run/final_report.md` — concise replay summary.

## Measurement caveat

The primary estimate compares every randomized agent-arm user with every randomized holdout user, regardless of whether an action was delivered. The observed difference is descriptive for the supplied synthetic replay and simulator seed; it is not by itself proof of statistical significance or causal uplift. See `KNOWN_LIMITATIONS.md`.

## Tests

The automated tests cover event/trigger deduplication, holdout safety, dialect conflict resolution, fail-closed behavior during a dependency outage, application of a corrected goal rule, target-series outcome attribution, independent pre-publication verification, and catalogue dialect/availability/deduplication constraints.
