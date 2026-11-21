# Eval report (live)

Generated 2026-11-21T05:18:20.995933+00:00

- Cases: 4 · pass rate **50%** (threshold 90%)
- Hard failures (grounding / safety / look-ahead): none
- Judge means: not run
- Total cost: $0.443
- Gates: {'pass_rate>=threshold': False, 'no_grounding_safety_lookahead_failures': True, 'judge_bars': True}

| Case | Category | Result | Time | Cost | Failed checks |
|---|---|---|---|---|---|
| pre_nvda_next | pre_only | FAIL | 80.0s | $0.117 | fact:pr.guide_revenue (None) |
| missing_transcript | data_gap | FAIL | 59.7s | $0.086 | unknowns (Consensus EPS median: needs FINNHUB_API_) |
| transcript_tone_live | transcript_tone | PASS | 65.2s | $0.126 |  |
| planted_claim_live | planted_claim | PASS | 56.3s | $0.113 |  |

## Most frequent failed checks
- fact:pr.guide_revenue: 1
- unknowns: 1