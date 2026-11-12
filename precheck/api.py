"""A4, F1, F2, F8, D1, D7, L6: the FastAPI front door.

Why asynchronous: a run makes several model calls and takes tens of seconds. Holding an HTTP request open
that long ties up connections, trips proxy timeouts and couples client latency to provider latency. We
return 202 + run_id immediately and the caller polls GET /v1/runs/{id}. Redis (a list used as a queue)
sits between submission and execution so workers scale independently of the API.
"""
import json
import os
import time
import uuid
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from . import logging_setup, metrics, store, tracing
from .config import settings
from .contracts import CONTRACT_VERSION, contract_document, validate_payload

logger = logging_setup.setup("api")
app = FastAPI(title="Building Plan Compliance Pre-Check", version="1.0.0",
              description="Produces evidence-backed findings. Never certifies compliance.")
tracing.setup("api")
tracing.instrument_fastapi(app)
r: aioredis.Redis | None = None
INDEX_META: dict = {}
INDEX_VERSION = "unknown"


@app.on_event("startup")
async def startup():
    global r, INDEX_META, INDEX_VERSION
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        INDEX_META = json.load(open(os.path.join(settings.index_dir, "meta.json")))
        from .index import _sha
        INDEX_VERSION = _sha(json.dumps(INDEX_META["corpus_versions"], sort_keys=True), INDEX_META["embed_model"])[:12]
        metrics.INDEX_AGE.set(INDEX_META["built_at"])
        metrics.INDEX_CHUNKS.set(INDEX_META["chunk_count"])
        metrics.INDEX_THROUGHPUT.set(INDEX_META.get("chunks_per_second") or 0)
    except FileNotFoundError:
        logger.warning("index meta not found; run the ingest step")


@app.middleware("http")
async def correlation(request: Request, call_next):
    cid = request.headers.get("X-Correlation-Id") or uuid.uuid4().hex[:16]
    logging_setup.correlation_id.set(cid)
    t = time.time()
    resp = await call_next(request)
    resp.headers["X-Correlation-Id"] = cid
    logging_setup.log(logger, "request", path=request.url.path, method=request.method,
                      status=resp.status_code, ms=round((time.time() - t) * 1000, 1))
    return resp


# ---------- submission ----------

@app.post("/v1/runs", status_code=202, summary="Submit a building proposal")
async def submit(request: Request):
    cid = logging_setup.correlation_id.get()
    client = request.headers.get(settings.client_header, request.client.host if request.client else "anon")

    # per-client rate limit (D7): a looping client could exhaust the model budget in minutes
    bucket = f"rl:{client}:{int(time.time() // 60)}"
    n = await r.incr(bucket)
    if n == 1:
        await r.expire(bucket, 120)
    if n > settings.rate_limit_per_min:
        metrics.RATE_LIMITED.inc()
        return JSONResponse({"error": "rate_limited", "limit_per_min": settings.rate_limit_per_min},
                            status_code=429, headers={"Retry-After": "60"})

    # ingestion boundary: validate BEFORE anything is queued (F2, D1)
    try:
        payload = await request.json()
    except Exception:
        payload = None
    model, errors = validate_payload(payload)
    if errors:
        qid = uuid.uuid4().hex[:12]
        await r.lpush(store.QUARANTINE, json.dumps({"id": qid, "ts": time.time(), "client": client,
                                                    "correlation_id": cid, "payload": payload, "errors": errors}, default=str))
        await r.ltrim(store.QUARANTINE, 0, 999)
        metrics.REJECTED.inc()
        logging_setup.log(logger, "request quarantined", quarantine_id=qid, n_errors=len(errors))
        return JSONResponse({"error": "validation_failed", "quarantine_id": qid, "field_errors": errors,
                             "run_started": False}, status_code=422)

    proposal = model.model_dump(mode="json")
    run_id = uuid.uuid4().hex[:16]
    now = time.time()

    # whole-run cache (D7): same proposal + prompt + index => same answer, no model spend
    ch = store.cache_hash(proposal, settings.prompt_version, INDEX_VERSION)
    cached = await r.get(store.cache_key(ch))
    if cached:
        metrics.CACHE.labels("hit").inc()
        await r.hset(store.run_key(run_id), mapping={"status": "succeeded", "created_at": now, "finished_at": now,
                                                     "client": client, "cached": "1", "result": cached,
                                                     "proposal": json.dumps(proposal), "correlation_id": cid})
        await r.expire(store.run_key(run_id), 86400)
        return JSONResponse({"run_id": run_id, "status": "succeeded", "cached": True,
                             "poll": f"/v1/runs/{run_id}"}, status_code=202)
    metrics.CACHE.labels("miss").inc()

    await r.hset(store.run_key(run_id), mapping={"status": "queued", "created_at": now, "client": client,
                                                 "proposal": json.dumps(proposal), "cache_hash": ch,
                                                 "correlation_id": cid, "traceparent": tracing.current_traceparent()})
    await r.expire(store.run_key(run_id), 86400)
    await r.rpush(store.QUEUE, run_id)
    logging_setup.log(logger, "run queued", run_id=run_id)
    return {"run_id": run_id, "status": "queued", "cached": False, "poll": f"/v1/runs/{run_id}"}


@app.get("/v1/runs/{run_id}", summary="Poll a run")
async def get_run(run_id: str):
    h = await r.hgetall(store.run_key(run_id))
    if not h:
        raise HTTPException(404, "unknown run_id (or expired)")
    out = {"run_id": run_id, "status": h["status"], "cached": h.get("cached") == "1",
           "created_at": float(h["created_at"])}
    if h.get("result"):
        out["result"] = json.loads(h["result"])
    if h.get("error"):
        out["error"] = h["error"]
    if h.get("finished_at"):
        out["e2e_seconds"] = round(float(h["finished_at"]) - float(h["created_at"]), 3)
    return out


@app.get("/v1/runs/{run_id}/trace", summary="Full reconstruction of a run (D5)")
async def get_trace(run_id: str):
    h = await r.hgetall(store.run_key(run_id))
    if not h:
        raise HTTPException(404, "unknown run_id (or expired)")
    events = [json.loads(x) for x in await r.lrange(store.trace_key(run_id), 0, -1)]
    return {"run_id": run_id, "correlation_id": h.get("correlation_id"), "status": h["status"], "events": events}


# ---------- feedback (L6) ----------

class Feedback(BaseModel):
    verdict: str = Field(pattern="^(satisfactory|unsatisfactory)$")
    reason: str = Field(default="", max_length=1000)


@app.post("/v1/runs/{run_id}/feedback", status_code=201)
async def feedback(run_id: str, fb: Feedback):
    if not await r.exists(store.run_key(run_id)):
        raise HTTPException(404, "unknown run_id")
    await r.rpush(store.FEEDBACK, json.dumps({"run_id": run_id, "verdict": fb.verdict, "reason": fb.reason,
                                              "ts": time.time()}))
    metrics.FEEDBACK.labels(fb.verdict).inc()
    return {"stored": True}


@app.get("/v1/quality/summary", summary="Automatic scores of sampled live runs (L6)")
async def quality_summary():
    rows = [json.loads(x) for x in await r.lrange(store.SCORES, 0, -1)]
    def avg(k):
        xs = [x[k] for x in rows if x.get(k) is not None]
        return round(sum(xs) / len(xs), 3) if xs else None
    return {"sampled_runs": len(rows), "sample_rate": settings.score_sample_rate,
            "average": {k: avg(k) for k in ("groundedness", "citation_resolves", "coverage", "no_certification", "overall")},
            "lowest": sorted(rows, key=lambda x: x.get("overall") or 0)[:3]}


@app.get("/v1/feedback/summary")
async def feedback_summary():
    rows = [json.loads(x) for x in await r.lrange(store.FEEDBACK, 0, -1)]
    sat = sum(1 for x in rows if x["verdict"] == "satisfactory")
    return {"total": len(rows), "satisfactory": sat, "unsatisfactory": len(rows) - sat,
            "satisfaction_rate": round(sat / len(rows), 3) if rows else None,
            "recent_reasons": [x["reason"] for x in rows[-10:] if x["verdict"] == "unsatisfactory" and x["reason"]]}


# ---------- contract, quarantine, health, metrics ----------

@app.get("/v1/contract", summary="Published data contract (D1)")
async def contract():
    return contract_document()


@app.get("/v1/quarantine", summary="Rejected requests with field-level errors")
async def quarantine(limit: int = 20):
    return [json.loads(x) for x in await r.lrange(store.QUARANTINE, 0, max(0, min(limit, 200) - 1))]


@app.get("/health")
async def health():
    try:
        await r.ping()
    except Exception as e:
        return JSONResponse({"status": "unhealthy", "redis": str(e)}, status_code=503)
    return {"status": "ok", "contract_version": CONTRACT_VERSION, "prompt_version": settings.prompt_version,
            "index": {"built_at": INDEX_META.get("built_at_iso"), "chunks": INDEX_META.get("chunk_count"),
                      "corpus_versions": INDEX_META.get("corpus_versions"), "version": INDEX_VERSION}}


@app.get("/metrics")
async def prom():
    try:
        metrics.QUEUE_DEPTH.set(await r.llen(store.QUEUE))
    except Exception:
        pass
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def web():
    return (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text()
