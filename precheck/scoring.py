"""L3/L6: deterministic quality scores, shared by the offline evaluation and the live sampler.

Three of these mirror the usual RAG "triad" that TruLens reports, but computed from the rule text itself,
so they need no judge model and cost nothing:
  groundedness      every finding's quote is word for word in the rule it cites            (TruLens: groundedness)
  citation_resolves every cited rule was really retrieved                                  (TruLens: context grounding)
  coverage          every must-have topic (setbacks, height by road width) was retrieved AND cited
                                                                                            (TruLens: context relevance, by recall)
  no_certification  no result text says "compliant / approved / certified"                 (our safety rule)
Escalated runs carry no findings, so they are counted separately instead of being scored.
"""
import re

from . import guardrails
from .agents import COVERAGE, _norm, coverage_matches


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def score_result(result: dict) -> dict:
    if not result.get("verified") or result.get("degraded"):
        return {"scored": False, "why": "escalated_or_degraded"}
    findings = result.get("findings", [])
    ev = {e["clause_id"]: e["text"] for e in result.get("evidence", [])}
    grounded = []
    for f in findings:
        q = _norm(f.get("evidence_quote", ""))
        grounded.append(1.0 if len(q) >= 10 and q in _norm(ev.get(f.get("clause_id"), "")) else 0.0)
    resolves = [1.0 if f.get("clause_id") in ev else 0.0 for f in findings]
    cited = {f.get("clause_id") for f in findings}
    cov = []
    for c in COVERAGE:
        if "when" in c:  # conditional on the proposal, which the result does not carry: left out of the sampled score
            continue
        matching = set(coverage_matches(c, {cid: {"text": t} for cid, t in ev.items()}))
        cov.append(1.0 if matching & cited else 0.0)
    texts = [f.get("reason", "") for f in findings] + [result.get("plain", {}).get("explain", ""), result.get("reason", "")]
    clean = 0.0 if any(p.search(t) for t in texts for p in guardrails.FORBIDDEN_OUTPUT) else 1.0
    scores = {"groundedness": _mean(grounded), "citation_resolves": _mean(resolves),
              "coverage": _mean(cov), "no_certification": clean}
    scores["overall"] = _mean(list(scores.values()))
    return {"scored": True, **scores}
