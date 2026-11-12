"""Runtime configuration. Everything environment-specific comes from env vars (D4).

Endpoints and model identifiers have no defaults on purpose: a missing value fails loudly.
"""
import os


def req(name: str) -> str:
    v = os.environ.get(name)
    if v is None or v == "":
        raise RuntimeError(f"Missing required environment variable {name}. See .env.example")
    return v


def num(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def integer(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


class Settings:
    # --- infra (required) ---
    @property
    def redis_url(self) -> str: return req("REDIS_URL")
    @property
    def corpus_dir(self) -> str: return req("CORPUS_DIR")
    @property
    def index_dir(self) -> str: return req("INDEX_DIR")
    @property
    def prompts_dir(self) -> str: return req("PROMPTS_DIR")
    @property
    def prompt_version(self) -> str: return req("PROMPT_VERSION")
    @property
    def embed_model(self) -> str: return req("EMBED_MODEL")

    # --- llm (required) ---
    @property
    def llm_base_url(self) -> str: return req("LLM_BASE_URL").rstrip("/")
    @property
    def llm_api_key(self) -> str: return req("LLM_API_KEY")
    @property
    def llm_model(self) -> str: return req("LLM_MODEL")

    # --- tunables (defaulted) ---
    llm_timeout_s = property(lambda s: num("LLM_TIMEOUT_S", 30))
    llm_json_mode = property(lambda s: os.environ.get("LLM_JSON_MODE", "").lower() in ("1", "true", "yes"))
    llm_reasoning_effort = property(lambda s: os.environ.get("LLM_REASONING_EFFORT", ""))
    llm_rate_limit_wait_s = property(lambda s: num("LLM_RATE_LIMIT_WAIT_S", 0))  # >0: on a 429, wait as told (up to this long per wait) instead of failing
    llm_max_retries = property(lambda s: integer("LLM_MAX_RETRIES", 3))
    llm_backoff_base_s = property(lambda s: num("LLM_BACKOFF_BASE_S", 0.5))
    breaker_fail_threshold = property(lambda s: integer("BREAKER_FAIL_THRESHOLD", 4))
    breaker_reset_s = property(lambda s: num("BREAKER_RESET_S", 20))
    price_prompt_per_1k = property(lambda s: num("PRICE_PROMPT_PER_1K", 0.0))
    price_completion_per_1k = property(lambda s: num("PRICE_COMPLETION_PER_1K", 0.0))

    max_steps = property(lambda s: integer("MAX_STEPS", 14))
    max_tool_calls = property(lambda s: integer("MAX_TOOL_CALLS", 14))
    max_run_seconds = property(lambda s: num("MAX_RUN_SECONDS", 90))
    max_verify_attempts = property(lambda s: integer("MAX_VERIFY_ATTEMPTS", 2))
    min_relevance = property(lambda s: num("MIN_RELEVANCE", 0.25))

    score_sample_rate = property(lambda s: num("SCORE_SAMPLE_RATE", 0.25))
    worker_concurrency = property(lambda s: max(1, integer("WORKER_CONCURRENCY", 1)))

    rate_limit_per_min = property(lambda s: integer("RATE_LIMIT_PER_MIN", 10))
    run_cache_ttl_s = property(lambda s: integer("RUN_CACHE_TTL_S", 3600))
    client_header = property(lambda s: os.environ.get("API_ALLOWED_CLIENT_HEADER", "X-Client-Id"))


settings = Settings()
