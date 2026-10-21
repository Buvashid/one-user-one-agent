# Impact report

## Pilot interpretation

The supplied headline compares users served with everyone else, which is not a randomized intention-to-treat comparison and can be biased by eligibility and engagement. Do not use the 2.3x headline as causal proof.

The replay is assigned by `replay/arms.csv`. The correct primary estimate is intention-to-treat: compare outcomes for every `agent`-arm user against every `holdout` user, regardless of whether the agent acted. Holdout must receive zero actions.

## Current replay outputs

- Decisions: 2558
- Published home-page actions: 747
- No-action decisions: 1811

## Measurement caveat

Run the supplied simulator against `actions.jsonl`, then compute goal outcomes from the resulting `outcome_events.jsonl` for the goal and its committed window. Compare agent vs holdout by randomized assignment (intention-to-treat), report sample sizes and absolute percentage-point difference, and avoid conditioning on being served. This initial report does not claim causal uplift before simulation.
