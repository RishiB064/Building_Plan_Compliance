"""Agent worker: pops run ids from Redis, executes the LangGraph run, stores result + trace.

Two ways to scale (A7): more processes (`docker compose up --scale worker=3`) or more threads per process
(WORKER_CONCURRENCY). The model call is the slow part and it waits on the network, so threads help until the
CPU (embedding + search) becomes the limit.
"""
import json
import random
import threading
import time

import redis
from prometheus_client import start_http_server

from . import logging_setup, metrics, scoring, store, tracing
from .agents import Orchestrator
from .config import settings
from .index import Index

logger = logging_setup.setup("worker")


def make_redis():
    # socket_timeout must exceed the BLPOP wait below, or idle polling raises TimeoutError and the worker crashes
    return redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=30,
                          socket_connect_timeout=5, health_check_interval=15)


def process_one(r, index, run_id: str) -> str | None:
    """Run one queued job to completion. Returns the final status, or None if the run id is unknown."""
    h = r.hgetall(store.run_key(run_id))
    if not h:
        return None
    logging_setup.correlation_id.set(h.get("correlation_id", run_id))
    r.hset(store.run_key(run_id), mapping={"status": "running", "started_at": time.time()})
    tkey = store.trace_key(run_id)
    t0 = time.time()

    def emit(ev, _k=tkey):
        r.rpush(_k, json.dumps(ev, default=str))
        r.expire(_k, 86400)

    with tracing.span("run", parent_carrier=h.get("traceparent"), run_id=run_id,
                      correlation_id=h.get("correlation_id", "")):
        try:
            proposal = json.loads(h["proposal"])  # inside the try: a corrupted job must fail, not crash the worker
            result = Orchestrator(proposal, index, emit).run()
            result["run_id"] = run_id
            now = time.time()
            r.hset(store.run_key(run_id), mapping={"status": "succeeded", "result": json.dumps(result),
                                                   "finished_at": now})
            if result["verified"] and not result["degraded"] and h.get("cache_hash"):
                r.setex(store.cache_key(h["cache_hash"]), settings.run_cache_ttl_s, json.dumps(result))
            outcome, route, status = ("degraded" if result["degraded"] else "ok"), result["route"], "succeeded"
            metrics.E2E_LATENCY.observe(now - float(h["created_at"]))
            _sample_score(r, run_id, result)
        except Exception as e:  # a run that did not complete is never returned as a confident answer
            logger.exception("run failed")
            r.hset(store.run_key(run_id), mapping={
                "status": "failed", "finished_at": time.time(), "error": f"{type(e).__name__}: {e}",
                "result": json.dumps({"route": "specialist-review", "verified": False, "findings": [],
                                      "reason": "the run did not complete; no determination was made",
                                      "plain": {"verdict": "needs_expert", "headline": "A person needs to look at this",
                                                "explain": "The check did not finish, so no answer was made.",
                                                "next_step": "Send this case to a licensed architect or engineer.",
                                                "problems": [], "degraded": True},
                                      "disclaimer": "No result. Escalate to a human reviewer."})})
            outcome, route, status = "failed", "specialist-review", "failed"
    metrics.RUNS.labels(outcome, route).inc()
    metrics.RUN_LATENCY.observe(time.time() - t0)
    logging_setup.log(logger, "run finished", run_id=run_id, outcome=outcome, route=route,
                      seconds=round(time.time() - t0, 2))
    return status


def _sample_score(r, run_id: str, result: dict):
    """L6: score a sample of live runs automatically and keep the numbers."""
    if random.random() >= settings.score_sample_rate:
        return
    sc = scoring.score_result(result)
    if not sc.get("scored"):
        metrics.LIVE_SAMPLED.labels("escalated").inc()
        return
    metrics.LIVE_SAMPLED.labels("scored").inc()
    for k in ("groundedness", "citation_resolves", "coverage", "no_certification", "overall"):
        if sc.get(k) is not None:
            metrics.LIVE_SCORE.labels(k).observe(sc[k])
    r.rpush(store.SCORES, json.dumps({"run_id": run_id, "ts": time.time(), **sc}))
    r.ltrim(store.SCORES, -500, -1)


def loop(r, index, stop: threading.Event | None = None):
    while not (stop and stop.is_set()):
        try:
            item = r.blpop(store.QUEUE, timeout=5)
            if item:
                process_one(r, index, item[1])
        except redis.exceptions.RedisError as e:  # Redis restarting or blipping: wait and carry on, do not exit
            logger.warning("redis error in worker loop, retrying: %s", e)
            time.sleep(1)


def main():
    tracing.setup("worker")
    r = make_redis()
    index = Index(settings.index_dir, settings.embed_model)
    meta = index.meta
    metrics.INDEX_AGE.set(meta["built_at"])
    metrics.INDEX_CHUNKS.set(meta["chunk_count"])
    metrics.INDEX_THROUGHPUT.set(meta.get("chunks_per_second") or 0)
    start_http_server(9100)
    n = settings.worker_concurrency
    logging_setup.log(logger, "worker ready", index_version=index.version, chunks=meta["chunk_count"], threads=n)
    for _ in range(n - 1):
        threading.Thread(target=loop, args=(make_redis(), index), daemon=True).start()
    loop(r, index)


if __name__ == "__main__":
    main()
