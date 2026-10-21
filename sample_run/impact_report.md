# Replay impact report

## Primary intention-to-treat outcome: any play within 7 days of first replay trigger

| Randomized arm | Users | Users with play | Rate |
|---|---:|---:|---:|
| agent | 863 | 578 | 67.0% |
| holdout | 883 | 573 | 64.9% |

Absolute difference (agent minus holdout): **+2.08 percentage points**. Approximate 95% CI for the difference (Wilson/Newcombe): **[-4.20, +8.35] percentage points**. Agent rate 95% Wilson CI: [63.77%, 70.03%]; holdout rate 95% Wilson CI: [61.69%, 67.97%]. These intervals quantify sampling uncertainty under the replay's randomized assignment; the result is still specific to this synthetic replay and simulator seed and is not proof of production causal uplift.

## Corrected goal rule audit

- G03 commitments re-judged because they were open at the correction time: **0**
- G03 commitments evaluated under the corrected rule (including commitments created after correction): **0**
- Only goals whose commitment window spans the correction instant are marked as re-judged; closed goals are not retroactively re-judged.

## Goal-level success (conditional on a measurable goal being selected)

| Arm | Goal commitments | Successes | Rate |
|---|---:|---:|---:|
| agent | 736 | 64 | 8.7% |
| holdout | 751 | 60 | 8.0% |

### Goal outcome status counts

- `failed_no_attributable_signal`: 1363
- `succeeded`: 124

G07 success requires a play on the committed target `content_slug`; an unrelated play is not credited. Outcomes are marked inconclusive when the goal window is not fully observed or target attribution is missing. Goal-level rates are conditional and are not the primary causal estimate.

## Pilot caution

The supplied pilot headline compares served users with everyone else, which is selection-biased. It is not causal proof. This report compares all randomized agent and holdout users, including users who received no action.
