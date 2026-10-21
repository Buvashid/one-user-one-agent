# Known limitations

- Goal eligibility intentionally implements a supported subset of the 12 goals. G10 has no measurable success event and G11's TV event is not supported by the supplied event stream, so neither is selected.
- Subscription reconciliation explicitly handles a paid purchase newer than the profile snapshot; more complex billing reversals and all possible profile/event conflicts would need additional rules.
- The home-page ranking is deterministic and intentionally simple, not a learned recommender. No push or WhatsApp messages are sent; the baseline prioritizes safe, inspectable behavior over channel breadth.
- G07 success is tied to the target `content_slug`, but the supplied synthetic simulator may not produce a target-series play for every commitment. Lack of an attributable event is reported as not observed within the window, not inferred success.
- The primary randomized replay result is descriptive for the supplied seed. Statistical uncertainty intervals and repeated-seed sensitivity analysis are not included, so no production uplift claim should be made.
- The deterministic mock provider is used instead of a live model. This removes credential and provider variability from evaluation, but does not measure live LLM quality or real provider latency.
- The automated test suite covers the explicit required categories and core catalogue/holdout constraints. The latest verified run passed 17 tests, but the suite cannot exhaust every unseen replay seed or every possible event-ordering edge case.