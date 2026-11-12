"""Command line helpers.

  python -m precheck.cli ingest            build/refresh the index (idempotent)
  python -m precheck.cli index-info        show when the index was built and from what
  python -m precheck.cli check-llm         one tiny call to the configured model: proves the URL, key and model work
  python -m precheck.cli stub-mode down    (stub only) make the stub model fail, to demo the circuit breaker; ok to undo
"""
import argparse
import json

from .config import settings


def main():
    ap = argparse.ArgumentParser(prog="precheck")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest", help="parse, chunk, embed and index the corpus (idempotent)")
    sub.add_parser("index-info", help="print index freshness metadata")
    sub.add_parser("check-llm", help="send one tiny request to the configured model")
    sub.add_parser("probe", help="run the main example once with the real model and show what happens at each step")
    sm = sub.add_parser("stub-mode", help="switch the stub model between ok, down and slow")
    sm.add_argument("mode", choices=["ok", "down", "slow"])
    args = ap.parse_args()

    if args.cmd == "ingest":
        from .index import build_index

        print(json.dumps(build_index(settings.corpus_dir, settings.index_dir, settings.embed_model), indent=2))
    elif args.cmd == "index-info":
        import os

        print(open(os.path.join(settings.index_dir, "meta.json")).read())
    elif args.cmd == "check-llm":
        from . import llm

        res = llm.chat([{"role": "user", "content": "Reply with exactly the word: ok"}], "check", "ping")
        print(json.dumps({"ok": True, "model": settings.llm_model, "latency_s": round(res.latency_s, 2),
                          "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
                          "cost_usd": res.cost_usd, "reply": res.text[:80]}, indent=2))
    elif args.cmd == "probe":
        from . import llm
        from .agents import Orchestrator, load_prompt
        from .index import Index
        from .tools import TOOL_SPECS

        req = {"plot_area_sqm": 200, "road_width_m": 5.5, "building_height_m": 13.5, "num_floors": 4,
               "built_up_area_sqm": 400, "ground_coverage_pct": 60, "parking_spaces": 1, "stilt_height_m": 0,
               "plot_depth_m": 20, "plot_width_m": 10, "front_setback_m": 0.8, "rear_setback_m": 1.0,
               "left_setback_m": 1.0, "right_setback_m": 1.0, "building_use": "residential", "zone": "residential",
               "notes": ""}
        prompt = load_prompt("decide.txt").format(tools=json.dumps(TOOL_SPECS, indent=1), step=0, attempt=0,
                                                  strategy="primary", gaps="[]", request_json=json.dumps(req),
                                                  observations="(none yet)")
        print("== 1. first model reply to the research prompt (raw)")
        try:
            r = llm.chat([{"role": "user", "content": prompt}], "probe", "decide")
            print(repr(r.text[:700]), f"\n   tokens in/out: {r.prompt_tokens}/{r.completion_tokens}")
            try:
                print("   parsed OK:", llm.parse_json(r.text))
            except Exception as e:  # noqa: BLE001
                print("   COULD NOT PARSE:", type(e).__name__, e)
        except Exception as e:  # noqa: BLE001
            print("   CALL FAILED:", e)
            return
        print("== 2. full run of the main example")
        events = []
        orch = Orchestrator(req, Index(settings.index_dir, settings.embed_model), events.append)
        res = orch.run()
        print("   route:", res["route"], "| reason_code:", res["reason_code"], "| tool calls:", res["usage"].get("tool_calls", res.get("tool_calls")))
        print("   reason:", res["reason"][:300])
        for f in res.get("findings", []):
            print("   -", f["clause_id"], f["status"])
        for e in events:
            if e.get("kind") in ("error", "agent_done"):
                print("   EVENT:", {k: v for k, v in e.items() if k in ("agent", "step", "kind", "error", "stop", "steps")})
    elif args.cmd == "stub-mode":
        import httpx

        base = settings.llm_base_url.removesuffix("/v1")
        print(httpx.post(f"{base}/admin/mode", json={"mode": args.mode}, timeout=5).json())


if __name__ == "__main__":
    main()
