"""Service-level tests: API + queue + worker against fake Redis, tracing, scoring, evaluation smoke test.
No Docker, no network. Run:  python tests/test_service.py   (or pytest -q tests)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_pipeline as base  # noqa: E402  (sets up env, stub model, index)

import fakeredis  # noqa: E402
import fakeredis.aioredis  # noqa: E402
import redis.asyncio as aioredis  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from precheck import api, scoring, store, tracing, worker  # noqa: E402

SERVER = fakeredis.FakeServer()
aioredis.from_url = lambda *a, **k: fakeredis.aioredis.FakeRedis(server=SERVER, decode_responses=True)
RSYNC = fakeredis.FakeRedis(server=SERVER, decode_responses=True)
os.environ["SCORE_SAMPLE_RATE"] = "1.0"
os.environ["RATE_LIMIT_PER_MIN"] = "1000"
HDR = {"X-Client-Id": "tests"}


def submit_and_run(client, body=None, headers=HDR):
    r = client.post("/v1/runs", json=body or base.BASE, headers=headers)
    assert r.status_code == 202, r.text
    j = r.json()
    if j["status"] == "queued":
        assert worker.process_one(RSYNC, base.INDEX, j["run_id"]) == "succeeded"
    return j, client.get(f"/v1/runs/{j['run_id']}").json()


def test_submit_poll_plain_result_cache_trace_feedback_quality():
    RSYNC.flushall()
    with TestClient(api.app) as c:
        j, d = submit_and_run(c)
        assert d["status"] == "succeeded" and d["result"]["route"] == "revise-and-recheck"
        assert d["result"]["plain"]["headline"] == "2 things to fix"
        # same proposal again: answered from the whole-run cache, no worker needed
        j2 = c.post("/v1/runs", json=base.BASE, headers=HDR).json()
        assert j2["cached"] is True and j2["status"] == "succeeded"
        # trace has every step
        tr = c.get(f"/v1/runs/{j['run_id']}/trace").json()
        kinds = {e["kind"] for e in tr["events"]}
        assert {"classify", "tool", "llm", "verify", "route"} <= kinds
        # feedback + automatic quality sampling
        assert c.post(f"/v1/runs/{j['run_id']}/feedback", json={"verdict": "unsatisfactory", "reason": "x"}).status_code == 201
        assert c.get("/v1/feedback/summary").json()["unsatisfactory"] == 1
        q = c.get("/v1/quality/summary").json()
        assert q["sampled_runs"] == 1 and q["average"]["overall"] == 1.0
        assert c.get("/v1/runs/nope").status_code == 404
        assert b"Plan checker" in c.get("/").content
        assert b"precheck_runs_total" in c.get("/metrics").content


def test_bad_input_is_quarantined_and_never_queued():
    RSYNC.flushall()
    with TestClient(api.app) as c:
        r = c.post("/v1/runs", json={**base.BASE, "road_width_m": -3}, headers=HDR)
        assert r.status_code == 422 and r.json()["run_started"] is False
        assert RSYNC.llen(store.QUEUE) == 0
        q = c.get("/v1/quarantine").json()
        assert q and q[0]["errors"][0]["field"] == "road_width_m"
        assert c.post("/v1/runs", content=b"not json", headers=HDR).status_code == 422


def test_rate_limit_per_client():
    RSYNC.flushall()
    os.environ["RATE_LIMIT_PER_MIN"] = "3"
    try:
        with TestClient(api.app) as c:
            codes = [c.post("/v1/runs", json={**base.BASE, "building_height_m": 12 + i * 0.1}, headers={"X-Client-Id": "greedy"}).status_code
                     for i in range(5)]
            assert codes[:3] == [202, 202, 202] and codes[3:] == [429, 429]
            assert c.post("/v1/runs", json={**base.BASE, "building_height_m": 12}, headers={"X-Client-Id": "polite"}).status_code == 202
    finally:
        os.environ["RATE_LIMIT_PER_MIN"] = "1000"


def test_worker_redis_timeout_regression():
    """Incident INC-001: BLPOP waits 5 s, so the client socket timeout must be longer or an idle worker crashes."""
    r = worker.make_redis()
    assert r.connection_pool.connection_kwargs["socket_timeout"] > 5


def test_worker_marks_crashed_run_as_failed_with_safe_result():
    RSYNC.flushall()
    RSYNC.hset(store.run_key("bad1"), mapping={"status": "queued", "created_at": 0, "proposal": "{not json"})
    assert worker.process_one(RSYNC, base.INDEX, "bad1") == "failed"  # corrupted job: marked failed, worker survives
    # a run whose orchestrator explodes is stored as failed with an escalation result, never a confident answer
    RSYNC.hset(store.run_key("bad2"), mapping={"status": "queued", "created_at": 0, "proposal": json.dumps({"x": 1})})
    assert worker.process_one(RSYNC, base.INDEX, "bad2") == "failed"
    h = RSYNC.hgetall(store.run_key("bad2"))
    res = json.loads(h["result"])
    assert h["status"] == "failed" and res["route"] == "specialist-review" and res["findings"] == []
    assert res["plain"]["verdict"] == "needs_expert"


def test_scoring_detects_tampered_quote():
    res, _ = base.run()
    good = scoring.score_result(res)
    assert good["scored"] and good["overall"] == 1.0
    res["findings"][0]["evidence_quote"] = "words that are not in the rule at all"
    assert scoring.score_result(res)["groundedness"] < 1.0
    assert scoring.score_result({"verified": False})["scored"] is False


def test_tracing_spans_follow_the_run_across_the_queue():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exp = InMemorySpanExporter()
    assert tracing.setup("tests", exporter=exp)
    try:
        RSYNC.flushall()
        with tracing.span("api.submit") as sp:
            tp = tracing.current_traceparent()
            trace_id = sp.get_span_context().trace_id
        assert tp.startswith("00-")
        RSYNC.hset(store.run_key("tr1"), mapping={"status": "queued", "created_at": 0, "traceparent": tp,
                                                   "proposal": json.dumps(base.BASE)})
        assert worker.process_one(RSYNC, base.INDEX, "tr1") == "succeeded"
        names = {s.name for s in exp.get_finished_spans()}
        assert {"run", "agent.research", "agent.verify", "tool.search_corpus", "llm.researcher", "llm.assessor"} <= names
        run_span = next(s for s in exp.get_finished_spans() if s.name == "run")
        assert run_span.context.trace_id == trace_id, "worker span must continue the API's trace"
        assert all(s.context.trace_id == trace_id for s in exp.get_finished_spans() if s.name.startswith(("agent.", "llm.", "tool.")))
    finally:
        tracing._tracer = None


def test_tracing_is_a_noop_when_off():
    assert tracing._tracer is None
    with tracing.span("x") as sp:
        assert sp is None
    assert tracing.current_traceparent() == ""


def test_eval_harness_smoke():
    sys.path.insert(0, base.ROOT)
    from eval import run as ev
    scenarios = {json.loads(line)["id"]: json.loads(line) for line in open(os.path.join(base.ROOT, "eval", "scenarios.jsonl"))}
    assert len(scenarios) >= 20
    for sid in ("s01", "s02", "s10", "s24", "s27", "s31"):
        row = ev.run_one(scenarios[sid], base.INDEX)
        assert row["ok"], row
    s = ev.summarise([ev.run_one(scenarios[i], base.INDEX) for i in ("s01", "s02", "s04", "s24", "s27")])
    assert s["route_accuracy"] == 1.0 and s["violation_recall"] == 1.0 and s["guardrail_catch"] == 1.0


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except Exception as e:  # noqa: BLE001
                import traceback
                fails += 1
                print("FAIL", name, type(e).__name__, e)
                traceback.print_exc()
    sys.exit(1 if fails else 0)
