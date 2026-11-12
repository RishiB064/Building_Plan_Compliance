"""Prometheus metrics shared by api and worker (A6, D5, D2, L5)."""
from prometheus_client import Counter, Gauge, Histogram

RUNS = Counter("precheck_runs_total", "Completed runs", ["outcome", "route"])
RUN_LATENCY = Histogram("precheck_run_seconds", "Worker execution time per run",
                        buckets=(1, 2, 5, 10, 20, 30, 45, 60, 90, 120))
E2E_LATENCY = Histogram("precheck_e2e_seconds", "Submission to final result",
                        buckets=(1, 2, 5, 10, 20, 30, 45, 60, 90, 120, 180))
STEP_LATENCY = Histogram("precheck_step_seconds", "Latency per agent step", ["agent", "step"],
                         buckets=(0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30))
STEPS_PER_RUN = Histogram("precheck_steps_per_run", "Agent loop steps per run", ["agent"],
                          buckets=(1, 2, 3, 4, 6, 8, 12))
TOOL_CALLS_PER_RUN = Histogram("precheck_tool_calls_per_run", "Tool calls per run", ["agent"],
                               buckets=(0, 1, 2, 3, 4, 6, 8, 12))
TOOL_CALLS = Counter("precheck_tool_calls_total", "Tool calls", ["tool"])

LLM_CALLS = Counter("precheck_llm_calls_total", "LLM calls", ["agent", "outcome"])
LLM_RETRIES = Counter("precheck_llm_retries_total", "LLM call retries", ["agent"])
LLM_TOKENS = Counter("precheck_llm_tokens_total", "Tokens", ["agent", "kind"])
LLM_COST = Counter("precheck_llm_cost_usd_total", "Cost in USD at call time", ["agent", "step"])
BREAKER_STATE = Gauge("precheck_breaker_open", "1 when the provider circuit breaker is open")

VERIFY = Counter("precheck_verification_total", "Verifier outcomes", ["outcome"])
ESCALATIONS = Counter("precheck_escalations_total", "Escalations to specialist review", ["reason"])
GUARDRAIL = Counter("precheck_guardrail_total", "Guardrail triggers", ["kind"])

QUEUE_DEPTH = Gauge("precheck_queue_depth", "Runs waiting in queue")
REJECTED = Counter("precheck_rejected_requests_total", "Requests rejected at ingestion (quarantined)")
RATE_LIMITED = Counter("precheck_rate_limited_total", "Requests rejected by per-client rate limit")
CACHE = Counter("precheck_run_cache_total", "Whole-run cache lookups", ["result"])
FEEDBACK = Counter("precheck_feedback_total", "Caller feedback", ["verdict"])

INDEX_AGE = Gauge("precheck_index_built_timestamp_seconds", "Unix time the index was built")
INDEX_CHUNKS = Gauge("precheck_index_chunks", "Chunks in the index")
INDEX_THROUGHPUT = Gauge("precheck_index_chunks_per_second", "Ingest throughput of the last build")

LIVE_SCORE = Histogram("precheck_live_quality_score", "Sampled live-run quality score, 0 to 1", ["metric"],
                       buckets=(0, 0.25, 0.5, 0.75, 0.9, 1.0))
LIVE_SAMPLED = Counter("precheck_live_sampled_total", "Live runs picked for scoring", ["outcome"])
