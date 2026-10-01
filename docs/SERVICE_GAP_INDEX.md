# Service Gap Index (SGI)

A single 0-100 score per ward that says **how far the ward's service is from where it should be**. 0 = best ward in the city, 100 = worst on every component. Implemented in `backend/app/services/service_gaps.py` (`gap_index`), exposed at `GET /service-gaps/index`, used by the choropleth (`metric=gap_index`), the ward risk score and the briefs.

## Formula

```
SGI = 100 x ( 0.35 x breach_rate
            + 0.20 x delay_ratio
            + 0.20 x backlog_share
            + 0.15 x repeat_rate
            + 0.10 x dissatisfaction )
```

Each component is **min-max scaled across wards to 0..1** before weighting, so SGI ranks wards *relative to each other* over the chosen window (default: last 12 months; optional category filter).

| Component | Definition | Why it matters | Weight |
|---|---|---|---:|
| `breach_rate` | complaints that breached their SLA / complaints received. A complaint breaches if it was resolved later than its SLA hours, or is still open and older than its SLA at the "as of" date | The headline service-level measure | 0.35 |
| `delay_ratio` | mean resolution hours / mean SLA hours of resolved complaints | Captures *how late*, not just whether late | 0.20 |
| `backlog_share` | open complaints / complaints received | Unresolved work citizens are still living with | 0.20 |
| `repeat_rate` | complaints flagged as near-duplicates / complaints received | Proxy for "the fix did not hold" or "nobody responded, so people re-reported" | 0.15 |
| `dissatisfaction` | (5 - mean citizen rating) / 4 | Citizens' own view; missing feedback counts as a neutral rating of 3 | 0.10 |

Weights live in `WEIGHTS` in the module (they sum to 1) and are returned by the API so the UI can show them. They are an expert judgement, not a fitted result: SLA performance is weighted most because it is what the grievance policy promises; satisfaction least because it is sparse and noisy.

## Reading it

* Bands used in the UI: < 33 good, 33-66 watch, >= 66 poor.
* Because of min-max scaling, a uniformly excellent city still has a "worst" ward with a high score; compare against the absolute components (breach %, backlog %) before concluding a ward is failing.
* The newest months look worse on `backlog_share` and `delay_ratio` because recent complaints are still open; use a 12-month window (the default) for rankings.
* Changing the window or category changes the scaling, so scores are not comparable across different filters.

## Validation

On the synthetic data the generator makes wards W05, W12, W15 and W17 slower (factors 1.4-1.9) and W03, W14, W20 faster. The index's four worst wards over the last 12 months are exactly W17, W05, W12, W15; the three well-run wards rank among the best (see [EVALUATION.md](EVALUATION.md#6-emerging-issue-detection-and-service-gap-index)).

## Extending

Add a component by computing it per ward in `gap_index`, adding it to `WEIGHTS` (re-normalise to 1) and to the table above. Keep every component "higher = worse" and scaled across wards.
