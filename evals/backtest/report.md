# Back-test: base-rate earnings calls (no look-ahead)

232 prints across 29 large caps, 2024-11-12 to 2026-11-03. Each brief was built as of the close before the information, through the production gatherer (FactStore cutoff enforced; the event's actuals hidden).

## Direction: P(stock up on day 1)

| Model | Brier (lower is better) | Log loss | Hit rate |
|---|---|---|---|
| This system (stock's own history, shrunk toward 50%) | 0.2604 | 0.7146 | 45% |
| Pooled cross-sectional base rate | 0.2478 | 0.6887 | 54% |
| Coin flip (0.5) | 0.25 | 0.6931 | — |

Observed up-rate: 42%.

## Size: expected |day-1 move|

MAE this system: 3.76 pp · MAE pooled median: 3.933 pp.

## Calibration

| Predicted bin | n | Mean predicted | Observed up-rate |
|---|---|---|---|
| 20-30% | 3 | 30% | 67% |
| 30-40% | 10 | 34% | 60% |
| 40-50% | 77 | 44% | 44% |
| 50-60% | 108 | 52% | 40% |
| 60-70% | 37 | 61% | 38% |

## Verdict

- Direction call: **NOT validated** (must beat both the pooled base rate and a coin flip on Brier).
- Move-size call: **validated** (must beat the pooled median).

The UI labels a claim 'unvalidated' unless the verdict above is 'validated'.

## Where it fails (largest errors)

| Ticker | Event | P(up) | Day 1 |
|---|---|---|---|
| XOM | 2026-10-30 | 30% | +2.2% |
| NKE | 2025-06-26 | 30% | +15.2% |
| WMT | 2025-02-20 | 65% | -6.5% |
| XOM | 2026-01-30 | 35% | +0.6% |
| JNJ | 2025-07-16 | 35% | +6.2% |
| PG | 2025-04-24 | 65% | -3.7% |
| PG | 2026-07-29 | 65% | -1.9% |
| AVGO | 2025-06-05 | 65% | -5.0% |