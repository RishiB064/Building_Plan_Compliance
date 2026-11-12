# Evaluation results

Prompt version `v1`. 7 scenarios.

| Metric | baseline: one search only | agent loop (default settings) |
|---|---|---|
| passed | 0 | 0 |
| route_accuracy | 0.0 | 0.0 |
| violation_recall | 0.0 | 0.0 |
| violation_precision | None | None |
| context_recall | 0.106 | 0.4131 |
| groundedness | None | 1.0 |
| escalation_rate | 1.0 | 0.8571 |
| guardrail_catch | None | None |
| guardrail_false_positive | None | None |
| invalid_rejected | None | None |
| avg_tool_calls | 1.0 | 8.0 |
| avg_seconds | 13.5363 | 120.3117 |
| avg_tokens | 2768.0 | 12896.4286 |
| avg_cost_usd | 0.0036 | 0.0151 |

## baseline: one search only: scenarios that did not pass

- `s01` Small plot, narrow road: gaps too small and too tall for a small plot (the main case): expected revise-and-recheck got specialist-review; expected violations ['ZR.T8.r3', 'ZR.T9.N1'] found [] 
    - findings: []
    - rules never retrieved: ['BL.5.2.7.1', 'ZR.3.1.iii', 'ZR.4.1.2.b', 'ZR.T8.r3', 'ZR.T9.N1']
    - verifier problems: ['best retrieval score 0.00 below relevance threshold 0.25: proposal appears outside the corpus', 'no findings produced', 'coverage: setbacks not retrieved', 'coverage: height_by_road_width not retrieved', 'coverage: small_plot_height not retrieved']
    - reason: the proposal falls outside the indexed corpus; no grounded finding can be made
    - searches: ['None']
- `s02` Same plot, within every limit: expected clear-for-submission got specialist-review; expected violations [] found [] 
    - findings: []
    - rules never retrieved: ['BL.5.2.7.1', 'ZR.3.1.ii', 'ZR.4.1.2.b', 'ZR.T8.r3', 'ZR.T9.N1']
    - verifier problems: ['best retrieval score 0.00 below relevance threshold 0.25: proposal appears outside the corpus', 'no findings produced', 'coverage: setbacks not retrieved', 'coverage: height_by_road_width not retrieved', 'coverage: small_plot_height not retrieved']
    - reason: the proposal falls outside the indexed corpus; no grounded finding can be made
    - searches: ['None']
- `s05` Plot above 250 sq m: front gap smaller than 12% of the depth: expected revise-and-recheck got specialist-review; expected violations ['ZR.T8.r4'] found [] 
    - findings: []
    - rules never retrieved: ['BL.5.2.7.1', 'ZR.3.1.ii', 'ZR.4.1.2.b', 'ZR.T8.r4']
    - verifier problems: ['best retrieval score 0.00 below relevance threshold 0.25: proposal appears outside the corpus', 'no findings produced', 'coverage: setbacks not retrieved', 'coverage: height_by_road_width not retrieved']
    - reason: the proposal falls outside the indexed corpus; no grounded finding can be made
    - searches: ['None']
- `s09` Plot up to 60 sq m, front gap too small: expected revise-and-recheck got specialist-review; expected violations ['ZR.T8.r1'] found [] 
    - findings: []
    - rules never retrieved: ['BL.5.2.7.1', 'ZR.3.1.ii', 'ZR.4.1.2.b', 'ZR.T9.N1']
    - verifier problems: ['coverage: height_by_road_width not retrieved', 'coverage: small_plot_height not retrieved']
    - reason: self-verification failed after retries: coverage: height_by_road_width not retrieved; coverage: small_plot_height not retrieved
    - searches: ['residential zone setbacks plot area']
- `s15` Open parking floor too high (4.0 m): expected revise-and-recheck got specialist-review; expected violations ['BL.2.149'] found [] 
    - findings: []
    - rules never retrieved: ['BL.2.149', 'BL.5.2.7.1', 'ZR.3.1.ii', 'ZR.4.1.2.b', 'ZR.T9.N1']
    - verifier problems: ['coverage: height_by_road_width not retrieved', 'coverage: small_plot_height not retrieved']
    - reason: self-verification failed after retries: coverage: height_by_road_width not retrieved; coverage: small_plot_height not retrieved
    - searches: ['residential zone setbacks plot area']
- `s19` Plot above 4000 sq m, front gap only 4 m: expected revise-and-recheck got specialist-review; expected violations ['ZR.T8.r5'] found [] 
    - findings: []
    - rules never retrieved: ['BL.5.2.7.1', 'ZR.3.1.ii', 'ZR.4.1.2.b']
    - verifier problems: ['coverage: height_by_road_width not retrieved']
    - reason: self-verification failed after retries: coverage: height_by_road_width not retrieved
    - searches: ['residential zone setbacks plot area']
- `s22` Small plot, 15 m tall counted with a 2.7 m parking floor: allowed up to 15 m with Table 8: expected revise-and-recheck got specialist-review; expected violations ['ZR.T9.N1'] found [] 
    - findings: []
    - rules never retrieved: ['BL.12.9', 'BL.2.149', 'BL.5.2.7.1', 'ZR.3.1.iii', 'ZR.4.1.2.b', 'ZR.T9.N1', 'ZR.T9.N6']
    - verifier problems: ['coverage: height_by_road_width not retrieved', 'coverage: small_plot_height not retrieved']
    - reason: self-verification failed after retries: coverage: height_by_road_width not retrieved; coverage: small_plot_height not retrieved
    - searches: ['residential zone setbacks plot area']

## agent loop (default settings): scenarios that did not pass

- `s01` Small plot, narrow road: gaps too small and too tall for a small plot (the main case): expected revise-and-recheck got specialist-review; expected violations ['ZR.T8.r3', 'ZR.T9.N1'] found [] 
    - findings: []
    - rules never retrieved: ['BL.5.2.7.1', 'ZR.3.1.iii', 'ZR.4.1.2.b', 'ZR.T9.N1']
    - verifier problems: ['coverage: height_by_road_width not retrieved', 'coverage: small_plot_height not retrieved']
    - reason: self-verification failed after retries: coverage: height_by_road_width not retrieved; coverage: small_plot_height not retrieved
    - searches: ['None', 'residential zone setbacks plot area', 'Table 8', 'ZR.T8.r1', 'ZR.T8.r2', 'ZR.T8.r3', 'ZR.T8.r4', 'ZR.T8.r5']
- `s02` Same plot, within every limit: expected clear-for-submission got specialist-review; expected violations [] found [] 
    - findings: []
    - rules never retrieved: ['BL.5.2.7.1', 'ZR.3.1.ii', 'ZR.4.1.2.b', 'ZR.T9.N1']
    - verifier problems: ['coverage: height_by_road_width not retrieved', 'coverage: small_plot_height not retrieved']
    - reason: self-verification failed after retries: coverage: height_by_road_width not retrieved; coverage: small_plot_height not retrieved
    - searches: ['None', 'residential zone setbacks plot area', 'Table 8', 'ZR.T8.r1', 'ZR.T8.r2', 'ZR.T8.r3', 'ZR.T8.r4', 'ZR.T8.r5']
- `s05` Plot above 250 sq m: front gap smaller than 12% of the depth: expected revise-and-recheck got specialist-review; expected violations ['ZR.T8.r4'] found [] 
    - findings: []
    - rules never retrieved: ['BL.5.2.7.1', 'ZR.3.1.ii', 'ZR.4.1.2.b']
    - verifier problems: ['coverage: height_by_road_width not retrieved']
    - reason: self-verification failed after retries: coverage: height_by_road_width not retrieved
    - searches: ['None', 'residential zone setbacks plot area', 'Table 8', 'ZR.T8.r1', 'ZR.T8.r2', 'ZR.T8.r3', 'ZR.T8.r4', 'ZR.T8.r5']
- `s09` Plot up to 60 sq m, front gap too small: expected revise-and-recheck got specialist-review; expected violations ['ZR.T8.r1'] found [] 
    - findings: []
    - rules never retrieved: ['BL.5.2.7.1', 'ZR.3.1.ii', 'ZR.4.1.2.b', 'ZR.T9.N1']
    - verifier problems: ['coverage: height_by_road_width not retrieved', 'coverage: small_plot_height not retrieved']
    - reason: self-verification failed after retries: coverage: height_by_road_width not retrieved; coverage: small_plot_height not retrieved
    - searches: ['residential zone setbacks plot area', 'Table 8', 'ZR.T8.r1', 'ZR.T8.r2', 'ZR.T8.r3', 'ZR.T8.r4', 'ZR.T8.r5', 'residential zone floor area ratio FAR built up area']
- `s15` Open parking floor too high (4.0 m): expected revise-and-recheck got specialist-review; expected violations ['BL.2.149'] found [] 
    - findings: []
    - rules never retrieved: ['ZR.3.1.ii', 'ZR.T9.N1']
    - verifier problems: ['coverage: small_plot_height not retrieved']
    - reason: self-verification failed after retries: coverage: small_plot_height not retrieved
    - searches: ['residential zone setbacks plot area', 'Table 8', 'ZR.T8.r1', 'ZR.T8.r2', 'ZR.T8.r3', 'ZR.T8.r4', 'ZR.T8.r5', 'residential zone building height limit road width floors']
- `s19` Plot above 4000 sq m, front gap only 4 m: expected revise-and-recheck got clear-for-submission; expected violations ['ZR.T8.r5'] found [] 
    - findings: [['ZR.T8.r5', 'satisfied'], ['ZR.4.1.2.b', 'info'], ['ZR.3.1.iii', 'info'], ['BL.2.149', 'info']]
    - rules never retrieved: ['ZR.3.1.ii']
    - verifier problems: []
    - reason: no violation found among the retrieved provisions; this is not a certification
    - searches: ['residential zone setbacks plot area', 'Table 8', 'ZR.T8.r1', 'ZR.T8.r2', 'ZR.T8.r3', 'ZR.T8.r4', 'ZR.T8.r5', 'residential zone height limit road width floors']
- `s22` Small plot, 15 m tall counted with a 2.7 m parking floor: allowed up to 15 m with Table 8: expected revise-and-recheck got specialist-review; expected violations ['ZR.T9.N1'] found [] 
    - findings: []
    - rules never retrieved: ['BL.12.9']
    - verifier problems: ['degraded']
    - reason: degraded mode: model provider unavailable (LLM call failed after 0 retries: NoneType: None). Retrieval-only evidence is attached; no finding was produced.
    - searches: ['residential zone setbacks plot area', 'Table 8', 'residential zone building height limit road width floors', 'ZR.4.1.2.b', 'residential zone floor area ratio FAR built up area limit', 'ZR.3.1.iii', 'Table 9', 'ZR.T9.N1']
