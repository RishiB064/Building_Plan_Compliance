"""A1-A3, F3-F7: the reasoning layer, built with LangGraph.

Topology (two agents with distinct responsibilities, supervised by a top-level graph):

  classify -> research (Research Agent: decide <-> act tool loop) -> assess (Assessor Agent)
           -> verify (deterministic) -+-> route            (verified, or retries exhausted -> escalate)
                                      +-> retry -> research (different strategy, gaps handed back)
  Any LLMUnavailable in research/assess -> degraded (retrieval-only evidence, escalate, never confident)

Hand-off contract: the Research Agent hands over only the passages it retrieved (Toolbox.retrieved); the
Assessor sees nothing else, and the verifier rejects any citation that does not resolve to those passages.
If the Research Agent fails mid-run the supervisor routes to `degraded`; if the Assessor fails, same.
Loops are bounded by steps, tool calls and wall-clock time (A1).
"""
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from . import guardrails, llm, metrics, plain, tracing
from .config import settings
from .tools import TOOL_SPECS, Toolbox

DISCLAIMER = ("Findings are evidence for a licensed architect or engineer to evaluate. "
              "This service never certifies a plan as compliant.")

# Coverage checks: provisions that must have been retrieved AND cited for the request to be verified.
# Heuristic, corpus-agnostic regexes. These encode the section-4.1 trap: height limits by road width live
# apart from the setback tables, so a single similarity pass over the setback question misses them.
def _table8_applies(p: dict) -> bool:
    """Table 8 (buildings up to 12 m, not counting the stilt floor; up to 15 m on plots of 250 sq m or less)."""
    h = p.get("building_height_m", 0) - p.get("stilt_height_m", 0)
    return h <= 12 or (p.get("plot_area_sqm", 0) <= 250 and p.get("building_height_m", 0) <= 15)


COVERAGE = [
    # "id": the retrieved clause id must match (a search that only touched text mentioning the table is not enough:
    # the exact row for this plot has to have been read). "when": only required for some proposals.
    {"name": "setbacks", "id": r"^ZR\.T8\.r\d+$", "when": _table8_applies,
     "gap": "the gap table (Table 8) row for this plot size was not retrieved; list the whole of Table 8 "
            "(list_table) and pick the row by the plot area"},
    {"name": "height_by_road_width", "all": [r"height", r"road width"],
     "gap": "no provision limiting building height by road width retrieved; "
            "search 'maximum height road width less than 9.0 m'"},
    {"name": "small_plot_height", "id": r"^ZR\.T9\.N1$", "when": lambda p: p.get("plot_area_sqm", 0) <= 250,
     "gap": "the height limit for plots up to 250 sq m was not retrieved; "
            "search 'maximum permissible building height plots up to 250 sq.m excluding stilt floor'"},
]


def coverage_matches(cov: dict, retrieved: dict) -> list:
    """Clause ids that satisfy one coverage entry (by id pattern, or by text patterns)."""
    if "id" in cov:
        return [cid for cid in retrieved if re.search(cov["id"], cid)]
    return [cid for cid, h in retrieved.items() if all(re.search(r, h["text"], re.I) for r in cov["all"])]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip(" \"'`.")


def load_prompt(name: str) -> str:
    return (Path(settings.prompts_dir) / settings.prompt_version / name).read_text()


class RunState(TypedDict, total=False):
    proposal: dict
    flags: dict
    classification: dict
    attempt: int
    strategy: str
    gaps: list
    findings: list
    conflicts: list
    exemption_possible: bool
    verification: dict
    route: str
    reason: str
    degraded: bool
    degraded_reason: str
    reason_code: str


class ResearchState(TypedDict, total=False):
    steps: int
    pending: dict
    observations: list
    stop: str


class Orchestrator:
    def __init__(self, proposal: dict, index, emit: Callable[[dict], None] | None = None):
        self.proposal = proposal
        self.tb = Toolbox(proposal, index)
        self._emit = emit or (lambda e: None)
        self.t0 = time.time()
        self.deadline = self.t0 + settings.max_run_seconds
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "by_step": []}
        self.steps = 0
        self.index_version = index.version
        self.graph = self._build()

    # ----- tracing & LLM plumbing -----
    def emit(self, agent: str, step: str, kind: str, **data):
        self._emit({"ts": time.time(), "agent": agent, "step": step, "kind": kind,
                    "prompt_version": settings.prompt_version, **data})

    def _llm(self, agent: str, step: str, prompt: str) -> dict:
        if time.time() > self.deadline:
            raise llm.LLMUnavailable("run time budget exhausted before model call")
        t = time.time()
        with tracing.span(f"llm.{agent}", agent=agent, step=step, model=settings.llm_model) as sp:
            res = llm.chat([{"role": "user", "content": prompt}], agent, step)
            if sp is not None:
                sp.set_attribute("prompt_tokens", res.prompt_tokens)
                sp.set_attribute("completion_tokens", res.completion_tokens)
                sp.set_attribute("cost_usd", res.cost_usd)
                sp.set_attribute("retries", res.retries)
        u = self.usage
        u["prompt_tokens"] += res.prompt_tokens
        u["completion_tokens"] += res.completion_tokens
        u["cost_usd"] = round(u["cost_usd"] + res.cost_usd, 8)
        u["by_step"].append({"agent": agent, "step": step, "prompt_tokens": res.prompt_tokens,
                             "completion_tokens": res.completion_tokens, "cost_usd": res.cost_usd})
        metrics.STEP_LATENCY.labels(agent, step.split("_")[0]).observe(time.time() - t)
        self.emit(agent, step, "llm", latency_ms=round((time.time() - t) * 1000, 1), retries=res.retries,
                  prompt_tokens=res.prompt_tokens, completion_tokens=res.completion_tokens,
                  cost_usd=res.cost_usd, prompt=prompt[-1500:], response=res.text[:1500])
        return {"text": res.text}

    # ----- graph construction -----
    def _build(self):
        g = StateGraph(RunState)
        for name, fn in (("classify", self.n_classify), ("research", self.n_research), ("assess", self.n_assess),
                         ("verify", self.n_verify), ("retry", self.n_retry), ("route", self.n_route),
                         ("degraded", self.n_degraded)):
            g.add_node(name, self._t(name, fn))
        g.set_entry_point("classify")
        g.add_edge("classify", "research")
        g.add_conditional_edges("research", lambda s: "degraded" if s.get("degraded") else "assess",
                                {"degraded": "degraded", "assess": "assess"})
        g.add_conditional_edges("assess", lambda s: "degraded" if s.get("degraded") else "verify",
                                {"degraded": "degraded", "verify": "verify"})
        g.add_conditional_edges("verify", self.after_verify, {"retry": "retry", "route": "route"})
        g.add_edge("retry", "research")
        g.add_edge("route", END)
        g.add_edge("degraded", END)

        # Research Agent inner loop
        r = StateGraph(ResearchState)
        r.add_node("decide", self._t("research.decide", self.r_decide))
        r.add_node("act", self._t("research.act", self.r_act))
        r.set_entry_point("decide")
        r.add_conditional_edges("decide", lambda s: "act" if s.get("pending") else "end", {"act": "act", "end": END})
        r.add_edge("act", "decide")
        self.research_graph = r.compile()
        return g.compile()

    @staticmethod
    def _t(name, fn):
        """Wrap a graph node so each step shows up as a span."""
        def inner(state):
            with tracing.span(f"agent.{name}"):
                return fn(state)
        return inner

    def run(self) -> dict:
        final = self.graph.invoke({"proposal": self.proposal, "attempt": 0, "strategy": "primary", "gaps": []},
                                  {"recursion_limit": 80})
        return self._result(final)

    # ----- supervisor nodes -----
    def n_classify(self, s: RunState) -> dict:
        p = self.proposal
        screen = guardrails.screen_input(p.get("notes", ""))
        for k in ("injection_detected", "toxic"):
            if screen[k]:
                metrics.GUARDRAIL.labels(k).inc()
        if screen["injection_detected"] or screen["toxic"]:
            # untrusted text never reaches a model once flagged
            self.proposal["notes"] = "[notes removed by input guardrail]"
        cls = {"use_class": p["building_use"], "zone": p["zone"], "plot_area_sqm": p["plot_area_sqm"],
               "road_width_m": p["road_width_m"], "building_height_m": p["building_height_m"],
               "num_floors": p["num_floors"]}
        self.emit("supervisor", "classify", "classify", classification=cls, guardrails=screen)
        return {"flags": screen, "classification": cls}

    def n_research(self, s: RunState) -> dict:
        try:
            self._run_research(s)
        except llm.LLMUnavailable as e:
            self.emit("research", "failed", "error", error=str(e))
            return {"degraded": True, "degraded_reason": str(e)}
        return {}

    def _run_research(self, s: RunState):
        self._rs = {"attempt": s.get("attempt", 0), "strategy": s.get("strategy", "primary"),
                    "gaps": s.get("gaps", [])}
        state = {"steps": 0, "pending": {}, "observations": [], "stop": ""}
        out = self.research_graph.invoke(state, {"recursion_limit": 60})
        metrics.STEPS_PER_RUN.labels("researcher").observe(out.get("steps", 0))
        metrics.TOOL_CALLS_PER_RUN.labels("researcher").observe(self.tb.calls)
        self.emit("research", "done", "agent_done", steps=out.get("steps"), stop=out.get("stop"),
                  retrieved=sorted(self.tb.retrieved))
        return out

    def n_assess(self, s: RunState) -> dict:
        passages = sorted(self.tb.retrieved.values(), key=lambda h: -h.get("score", 0))[:24]
        prompt = load_prompt("assess.txt").format(
            attempt=s.get("attempt", 0),
            request_json=json.dumps({k: v for k, v in self.proposal.items() if k != "notes"}),
            notes=guardrails.wrap_untrusted(self.proposal.get("notes", "")),
            passages_json=json.dumps([{"clause_id": h["clause_id"], "text": h["text"]} for h in passages]),
        )
        try:
            text = self._llm("assessor", f"assess_{s.get('attempt', 0)}", prompt)["text"]
        except llm.LLMUnavailable as e:
            self.emit("assessor", "failed", "error", error=str(e))
            return {"degraded": True, "degraded_reason": str(e)}
        try:
            data = llm.parse_json(text)
        except ValueError:
            data = {"findings": []}
        findings = data.get("findings") if isinstance(data.get("findings"), list) else []
        for f in findings:
            if isinstance(f, dict) and isinstance(f.get("reason"), str):
                f["reason"], changed = guardrails.filter_output(f["reason"])
                if changed:
                    metrics.GUARDRAIL.labels("output_filtered").inc()
        findings = [self._fix_arithmetic(f) for f in findings if isinstance(f, dict)]
        return {"findings": findings,
                "conflicts": data.get("conflicts") or [],
                "exemption_possible": bool(data.get("exemption_possible"))}

    def _fix_arithmetic(self, f: dict) -> dict:
        """A model can write the right numbers and the wrong verdict (actual 4, needs at least 5, "satisfied").
        When a finding carries its own numbers, the verdict is recomputed from them in plain code."""
        a, lim, kind = f.get("actual"), f.get("limit"), f.get("limit_type")
        num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
        if f.get("status") in ("satisfied", "violated") and num(a) and num(lim) and kind in ("at most", "at least"):
            ok = a <= lim + 1e-9 if kind == "at most" else a >= lim - 1e-9
            right = "satisfied" if ok else "violated"
            if right != f["status"]:
                self.emit("verifier", "arithmetic", "arithmetic_fix", clause_id=f.get("clause_id"),
                          model_said=f["status"], numbers=f"{a} {kind} {lim}", corrected_to=right)
                metrics.VERIFY.labels("arithmetic_fixed").inc()
                f["status"] = right
        return f

    def n_verify(self, s: RunState) -> dict:
        """A3: deterministic check. Every citation must resolve to retrieved text."""
        t = time.time()
        problems, gaps = [], []
        findings = s.get("findings", [])
        retrieved = self.tb.retrieved
        out_of_corpus = self.tb.max_score < settings.min_relevance
        if out_of_corpus:
            problems.append(f"best retrieval score {self.tb.max_score:.2f} below relevance threshold "
                            f"{settings.min_relevance}: proposal appears outside the corpus")
        if not findings:
            problems.append("no findings produced")
        cited = set()
        for f in findings:
            cid, q = f.get("clause_id"), f.get("evidence_quote", "")
            if cid not in retrieved:
                problems.append(f"citation {cid!r} does not resolve to a retrieved passage")
                continue
            if f.get("status") not in {"satisfied", "violated", "needs_review", "info"}:
                problems.append(f"{cid}: invalid status {f.get('status')!r}")
            if len(_norm(q)) < 10 or _norm(q) not in _norm(retrieved[cid]["text"]):
                problems.append(f"{cid}: evidence_quote is not a verbatim span of the retrieved clause")
            cited.add(cid)
        for cov in COVERAGE:
            if "when" in cov and not cov["when"](self.proposal):
                continue
            matching = coverage_matches(cov, retrieved)
            if not matching:
                gaps.append(cov["gap"])
                problems.append(f"coverage: {cov['name']} not retrieved")
            elif not (set(matching) & cited) and findings:
                gaps.append(f"retrieved {matching} but no finding cites it; assess it")
                problems.append(f"coverage: {cov['name']} retrieved but not assessed")
        ok = not problems
        metrics.VERIFY.labels("ok" if ok else "failed").inc()
        v = {"ok": ok, "problems": problems, "out_of_corpus": out_of_corpus, "cited": sorted(cited)}
        self.emit("verifier", f"verify_{s.get('attempt', 0)}", "verify", latency_ms=round((time.time() - t) * 1000, 1), **v)
        return {"verification": v, "gaps": gaps}

    def after_verify(self, s: RunState) -> str:
        v = s["verification"]
        if v["ok"] or v["out_of_corpus"]:
            return "route"
        if s.get("attempt", 0) + 1 >= settings.max_verify_attempts or time.time() > self.deadline:
            return "route"
        return "retry"

    def n_retry(self, s: RunState) -> dict:
        self.emit("supervisor", "retry", "retry", gaps=s.get("gaps"), problems=s["verification"]["problems"])
        return {"attempt": s.get("attempt", 0) + 1, "strategy": "alternate", "findings": []}

    def n_route(self, s: RunState) -> dict:
        """F6/F7: choose a route and state the reason. Escalate rather than guess."""
        v, findings, flags = s["verification"], s.get("findings", []), s.get("flags", {})
        reasons = []
        if flags.get("injection_detected"):
            route, code = "specialist-review", "injection"
            reasons.append("applicant notes contained an instruction-like pattern; notes were discarded and a human must review")
        elif v["out_of_corpus"]:
            route, code = "specialist-review", "out_of_corpus"
            reasons.append("the proposal falls outside the indexed corpus; no grounded finding can be made")
        elif not v["ok"]:
            route, code = "specialist-review", "verify_failed"
            reasons.append("self-verification failed after retries: " + "; ".join(v["problems"][:4]))
        elif s.get("conflicts"):
            route, code = "specialist-review", "conflict"
            reasons.append(f"retrieved provisions conflict: {s['conflicts']}")
        elif s.get("exemption_possible"):
            route, code = "specialist-review", "exemption"
            reasons.append("an exemption or relaxation may apply and needs human judgement")
        elif any(f["status"] == "needs_review" for f in findings):
            route, code = "specialist-review", "needs_review"
            reasons.append("one or more provisions could not be evaluated from the data given: " +
                           ", ".join(f["clause_id"] for f in findings if f["status"] == "needs_review"))
        elif any(f["status"] == "violated" for f in findings):
            route, code = "revise-and-recheck", "violated"
            reasons.append("violations found in: " + ", ".join(f["clause_id"] for f in findings if f["status"] == "violated"))
        else:
            route, code = "clear-for-submission", "clear"
            reasons.append("no violation found among the retrieved provisions; this is not a certification")
        if route == "specialist-review":
            metrics.ESCALATIONS.labels(code).inc()
        reason, _ = guardrails.filter_output("; ".join(reasons))
        self.emit("supervisor", "route", "route", route=route, reason=reason, reason_code=code)
        return {"route": route, "reason": reason, "reason_code": code}

    def n_degraded(self, s: RunState) -> dict:
        """A5 fallback: no LLM. Run fixed retrieval from the request's own fields, attach it, escalate."""
        p = self.proposal
        for q in (f"setbacks gap from plot edge table 8 plot area {p['plot_area_sqm']} sq.m",
                  f"maximum building height road width less than 9.0 m (road {p['road_width_m']} m)"):
            self.tb._record(self.tb.index.search(q, 3))
        reason = (f"degraded mode: model provider unavailable ({s.get('degraded_reason', 'unknown')}). "
                  "Retrieval-only evidence is attached; no finding was produced.")
        metrics.ESCALATIONS.labels("degraded").inc()
        self.emit("supervisor", "degraded", "route", route="specialist-review", reason=reason)
        return {"route": "specialist-review", "reason": reason, "reason_code": "degraded", "findings": [],
                "verification": {"ok": False, "problems": ["degraded"], "out_of_corpus": False, "cited": []}}

    # ----- Research Agent nodes -----
    def r_decide(self, s: ResearchState) -> dict:
        rs = self._rs
        if s["steps"] >= settings.max_steps or self.tb.calls >= settings.max_tool_calls:
            return {"pending": {}, "stop": "step_or_tool_limit"}
        if time.time() > self.deadline:
            return {"pending": {}, "stop": "time_limit"}
        obs = "\n".join(s["observations"][-12:]) or "(none yet)"
        prompt = load_prompt("decide.txt").format(
            tools=json.dumps(TOOL_SPECS, indent=1), step=s["steps"], attempt=rs["attempt"],
            strategy=rs["strategy"], gaps=json.dumps(rs["gaps"]),
            request_json=json.dumps({k: v for k, v in self.proposal.items() if k != "notes"}),
            observations=obs)
        text = self._llm("researcher", f"decide_{rs['attempt']}_{s['steps']}", prompt)["text"]
        try:
            act = llm.parse_json(text)
        except ValueError:
            return {"pending": {}, "stop": "unparseable_action", "steps": s["steps"] + 1}
        if act.get("action") == "tool" and act.get("tool") in TOOL_SPECS:
            return {"pending": act}
        return {"pending": {}, "stop": "finish"}

    def r_act(self, s: ResearchState) -> dict:
        act = s["pending"]
        t = time.time()
        with tracing.span(f"tool.{act['tool']}", tool=act["tool"], query=str((act.get("args") or {}).get("query", ""))[:200]):
            res = self.tb.call(act["tool"], act.get("args") or {})
        metrics.TOOL_CALLS.labels(act["tool"]).inc()
        metrics.STEP_LATENCY.labels("researcher", "tool").observe(time.time() - t)
        ids = [h["clause_id"] for h in res.get("hits", [])]
        self.emit("researcher", f"tool_{s['steps']}", "tool", tool=act["tool"], args=act.get("args"),
                  why=act.get("why"), latency_ms=res.get("_latency_ms"), returned=ids,
                  passages=[{"clause_id": h["clause_id"], "score": h.get("score"), "text": h["text"]}
                            for h in res.get("hits", [])], error=res.get("error"))
        if "hits" in res:
            # only ids and plain titles: the full text of every retrieved passage is kept for the assessor, and
            # showing the researcher less keeps each model call small
            line = f"[{act['tool']} {json.dumps(act.get('args'))}] -> retrieved " + "; ".join(
                f"{h['clause_id']} ({plain.rule_title(h['text'])})" for h in res["hits"]) + \
                ". Full text of these is kept for the assessor; no need to re-read them."
        else:
            line = f"[{act['tool']} {json.dumps(act.get('args'))}] -> {json.dumps({k: v for k, v in res.items() if k != '_latency_ms'})}"
        return {"observations": s["observations"] + [line], "steps": s["steps"] + 1, "pending": {}}

    # ----- result assembly -----
    def _result(self, final: dict) -> dict:
        v = final.get("verification", {})
        cited = set(v.get("cited", []))
        verified = bool(v.get("ok"))
        findings = final.get("findings", []) if verified else []
        titles = {cid: plain.rule_title(h["text"]) for cid, h in self.tb.retrieved.items()}
        for f in findings:
            f["rule"] = titles.get(f.get("clause_id"), f.get("clause_id"))
            for k in ("actual", "limit"):  # optional numbers for the UI meters; never trust their type
                if isinstance(f.get(k), bool) or not isinstance(f.get(k), (int, float)):
                    f.pop(k, None)
            if f.get("limit_type") not in ("at most", "at least"):
                f.pop("limit_type", None)
            if not isinstance(f.get("unit"), str):
                f.pop("unit", None)
        evidence = {cid: self.tb.retrieved[cid] for cid in (cited if verified else self.tb.retrieved)
                    if cid in self.tb.retrieved}
        return {
            "route": final["route"], "reason": final["reason"], "reason_code": final.get("reason_code", ""),
            "plain": plain.build(final["route"], final.get("reason_code", ""), findings, bool(final.get("degraded")), titles),
            "findings": findings,
            "verified": verified, "degraded": bool(final.get("degraded")),
            "classification": final.get("classification"), "guardrails": final.get("flags"),
            "evidence": [{"clause_id": c, "title": titles.get(c, c), "text": h["text"], "source": h.get("source"), "page": h.get("page"),
                          "score": h.get("score")} for c, h in evidence.items()],
            "evidence_note": None if verified else "UNVERIFIED retrieval evidence only - no findings are asserted",
            "verification": v, "attempts": final.get("attempt", 0) + 1,
            "tool_calls": self.tb.calls, "prompt_version": settings.prompt_version,
            "index_version": self.index_version, "usage": self.usage,
            "elapsed_s": round(time.time() - self.t0, 3), "disclaimer": DISCLAIMER,
        }
