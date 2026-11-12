"""L3: evaluation harness. Runs the scenario set through the real reasoning code and scores it against the
independent answer key in eval/reference.py.

  docker compose --profile tools run --rm eval                   # real index + whatever model is in .env
  docker compose --profile tools run --rm eval --compare         # baseline (one search) vs the agent loop
  python -m eval.run --offline --compare                         # no Docker: stub model + dev embedder (dev only)
  python -m eval.run --check                                     # exit 1 if a target below is missed

Metrics (RAG-triad style, but computed from the rule text so they need no judge model and cost nothing):
  route accuracy        did it choose the right outcome                         (answer correctness)
  violation recall/prec did it find the broken rules, and only those            (answer correctness)
  context recall        were the rules that apply actually retrieved            (context relevance)
  groundedness          is every quote word for word from the rule it cites     (groundedness)
  guardrails            injection caught, harmless notes not flagged, bad input rejected
"""
import argparse
import contextlib
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TARGETS = {"route_accuracy": 0.9, "violation_recall": 0.9, "context_recall": 0.9, "groundedness": 1.0,
           "guardrail_catch": 1.0, "guardrail_false_positive": 0.0, "invalid_rejected": 1.0}


def bootstrap_offline():
    """Dev only: stub model in a thread, hashed embedder, temp index. Mirrors tests/test_pipeline.py."""
    port = 18082
    os.environ.update({
        "LLM_BASE_URL": f"http://127.0.0.1:{port}/v1", "LLM_API_KEY": "dev", "LLM_MODEL": "stub", "STUB_DELAY_MS": "0",
        "EMBED_MODEL": "hashed-bow", "REDIS_URL": "redis://unused", "CORPUS_DIR": os.path.join(ROOT, "corpus"),
        "INDEX_DIR": tempfile.mkdtemp(), "PROMPTS_DIR": os.path.join(ROOT, "prompts"),
        "PROMPT_VERSION": os.environ.get("PROMPT_VERSION", "v1"), "MIN_RELEVANCE": "0.05", "LLM_MAX_RETRIES": "1",
    })
    import uvicorn

    from precheck import dev_embed
    dev_embed.patch_index_module()
    from precheck import stub_llm
    threading.Thread(target=uvicorn.Server(uvicorn.Config(stub_llm.app, host="127.0.0.1", port=port,
                                                          log_level="error")).run, daemon=True).start()
    time.sleep(1.5)
    from precheck import index as idx
    idx.build_index(os.environ["CORPUS_DIR"], os.environ["INDEX_DIR"], "hashed-bow")


@contextlib.contextmanager
def env(overrides):
    old = {k: os.environ.get(k) for k in overrides}
    os.environ.update({k: str(v) for k, v in overrides.items()})
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def run_one(sc, index):
    from precheck import scoring
    from precheck.agents import Orchestrator
    from precheck.contracts import validate_payload
    from eval.reference import reference

    kind, req = sc["kind"], sc["request"]
    model, errors = validate_payload(req)
    if kind == "invalid":
        return {"id": sc["id"], "kind": kind, "note": sc["note"], "rejected": bool(errors), "ok": bool(errors)}
    if errors:
        return {"id": sc["id"], "kind": kind, "note": sc["note"], "ok": False, "error": f"unexpectedly rejected: {errors}"}
    ref = reference(req, injection=(kind == "injection"))
    t0 = time.time()
    events = []
    orch = Orchestrator(model.model_dump(mode="json"), index, events.append)
    res = orch.run()
    found = {f["clause_id"] for f in res["findings"] if f.get("status") == "violated"}
    evid = {e["clause_id"] for e in res["evidence"]}
    flags = res.get("guardrails") or {}
    row = {"id": sc["id"], "kind": kind, "note": sc["note"], "expected_route": ref["route"], "route": res["route"],
           "route_ok": res["route"] == ref["route"], "expected_violations": sorted(ref["violations"]),
           "found_violations": sorted(found), "tp": len(found & ref["violations"]), "fp": len(found - ref["violations"]),
           "fn": len(ref["violations"] - found), "context_recall": round(len(ref["applicable"] & evid) / len(ref["applicable"]), 4),
           "groundedness": scoring.score_result(res).get("groundedness"), "verified": res["verified"],
           "tool_calls": res["tool_calls"], "attempts": res["attempts"], "seconds": res["elapsed_s"],
           "cost_usd": res["usage"]["cost_usd"], "tokens": res["usage"]["prompt_tokens"] + res["usage"]["completion_tokens"],
           "flag_injection": bool(flags.get("injection_detected")), "flag_toxic": bool(flags.get("toxic")),
           "wall_s": round(time.time() - t0, 2),
           # why it went the way it did: kept in the JSON and shown for failures
           "findings": [[f["clause_id"], f["status"]] for f in res["findings"]],
           "retrieved": sorted(orch.tb.retrieved), "missing": sorted(ref["applicable"] - set(orch.tb.retrieved)),
           "queries": [str((e.get("args") or {}).get("query") or (e.get("args") or {}).get("clause_id") or (e.get("args") or {}).get("table"))
                       for e in events if e.get("kind") == "tool"],
           "problems": (res.get("verification") or {}).get("problems", []), "reason": res.get("reason", "")[:300]}
    ok = row["route_ok"]
    if kind == "injection":
        ok = ok and row["flag_injection"]
    if kind == "toxic":
        ok = ok and row["flag_toxic"]
    if kind == "benign_notes":
        ok = ok and not row["flag_injection"] and not row["flag_toxic"]
    row["ok"] = ok
    return row


def summarise(rows):
    normal = [r for r in rows if r["kind"] in ("normal", "benign_notes", "toxic", "injection") and "route" in r]
    tp, fp, fn = (sum(r[k] for r in normal) for k in ("tp", "fp", "fn"))
    inj = [r for r in rows if r["kind"] == "injection"]
    benign = [r for r in rows if r["kind"] == "benign_notes"]
    inv = [r for r in rows if r["kind"] == "invalid"]
    s = {
        "scenarios": len(rows), "passed": sum(1 for r in rows if r["ok"]),
        "route_accuracy": _mean([1.0 if r["route_ok"] else 0.0 for r in normal]),
        "violation_recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "violation_precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "context_recall": _mean([r["context_recall"] for r in normal]),
        "groundedness": _mean([r["groundedness"] for r in normal]),
        "escalation_rate": _mean([1.0 if r["route"] == "specialist-review" else 0.0 for r in normal]),
        "guardrail_catch": _mean([1.0 if (r["flag_injection"] and r["route"] == "specialist-review") else 0.0 for r in inj]),
        "guardrail_false_positive": _mean([1.0 if (r["flag_injection"] or r["flag_toxic"]) else 0.0 for r in benign]),
        "invalid_rejected": _mean([1.0 if r["rejected"] else 0.0 for r in inv]),
        "avg_tool_calls": _mean([r["tool_calls"] for r in normal]), "avg_seconds": _mean([r["seconds"] for r in normal]),
        "avg_tokens": _mean([r["tokens"] for r in normal]), "avg_cost_usd": _mean([r["cost_usd"] for r in normal]),
    }
    return s


def run_config(name, overrides, scenarios, index, workers):
    with env(overrides):
        with ThreadPoolExecutor(max_workers=workers) as ex:
            rows = list(ex.map(lambda sc: run_one(sc, index), scenarios))
    return {"name": name, "overrides": overrides, "summary": summarise(rows), "rows": rows}


def check(summary):
    bad = []
    for k, target in TARGETS.items():
        v = summary.get(k)
        if v is None:
            continue
        if (k == "guardrail_false_positive" and v > target) or (k != "guardrail_false_positive" and v < target):
            bad.append(f"{k} = {v} (target {'<=' if k == 'guardrail_false_positive' else '>='} {target})")
    return bad


def markdown(results, prompt_version):
    keys = ["passed", "route_accuracy", "violation_recall", "violation_precision", "context_recall", "groundedness",
            "escalation_rate", "guardrail_catch", "guardrail_false_positive", "invalid_rejected", "avg_tool_calls",
            "avg_seconds", "avg_tokens", "avg_cost_usd"]
    out = [f"# Evaluation results\n\nPrompt version `{prompt_version}`. {results[0]['summary']['scenarios']} scenarios.\n",
           "| Metric | " + " | ".join(r["name"] for r in results) + " |", "|---|" + "---|" * len(results)]
    for k in keys:
        out.append(f"| {k} | " + " | ".join(str(r["summary"].get(k)) for r in results) + " |")
    for r in results:
        out.append(f"\n## {r['name']}: scenarios that did not pass\n")
        bad = [x for x in r["rows"] if not x["ok"]]
        if not bad:
            out.append("None.")
        for x in bad:
            out.append(f"- `{x['id']}` {x['note']}: expected {x.get('expected_route')} got {x.get('route')}; "
                       f"expected violations {x.get('expected_violations')} found {x.get('found_violations')} {x.get('error', '')}")
            if "findings" in x:
                out.append(f"    - findings: {x['findings']}\n    - rules never retrieved: {x['missing']}\n"
                           f"    - verifier problems: {x['problems']}\n    - reason: {x['reason']}\n    - searches: {x['queries']}")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="dev only: stub + hashed embedder, no Docker")
    ap.add_argument("--compare", action="store_true", help="baseline (one search only) vs agent loop")
    ap.add_argument("--check", action="store_true", help="exit 1 if a target is missed")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=os.path.join(ROOT, "eval", "results"))
    ap.add_argument("--only", default="", help="comma-separated scenario ids to run, e.g. s01,s02,s05 (saves tokens on a free tier)")
    ap.add_argument("--scenarios", default=os.path.join(ROOT, "eval", "scenarios.jsonl"))
    a = ap.parse_args()
    if a.offline:
        bootstrap_offline()
    from precheck.config import settings
    from precheck.index import Index
    index = Index(settings.index_dir, settings.embed_model)
    scenarios = [json.loads(line) for line in open(a.scenarios) if line.strip()]
    if a.only:
        keep = {x.strip() for x in a.only.split(",")}
        scenarios = [sc for sc in scenarios if sc["id"] in keep]
    configs = [("agent loop (default settings)", {})]
    if a.compare:
        configs.insert(0, ("baseline: one search only", {"MAX_TOOL_CALLS": 1}))
    results = [run_config(n, o, scenarios, index, a.workers) for n, o in configs]
    os.makedirs(a.out, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    pv = settings.prompt_version
    json.dump({"prompt_version": pv, "results": results}, open(os.path.join(a.out, f"eval-{pv}-{stamp}.json"), "w"), indent=1, default=list)
    md = markdown(results, pv)
    open(os.path.join(a.out, f"eval-{pv}-{stamp}.md"), "w").write(md)
    open(os.path.join(a.out, "latest.md"), "w").write(md)
    print(md)
    bad = check(results[-1]["summary"])
    print("TARGETS:", "all met" if not bad else "MISSED: " + "; ".join(bad))
    sys.exit(1 if (a.check and bad) else 0)


if __name__ == "__main__":
    main()
