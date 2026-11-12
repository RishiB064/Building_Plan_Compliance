"""Plain-language layer. The decision logic stays in agents.py; this only explains it in everyday words.

Everything here is deterministic (no model call), so the plain summary can never contradict the route.
"""
import re

# Jargon -> everyday words, applied to rule titles taken from the rulebook.
_SIMPLE = [
    (r"^Gap between the building and the plot edge.*", "Gap between the building and the plot edge"),
    (r"stilt floor", "open parking floor"),
]

VERDICT = {
    "revise-and-recheck": "needs_changes",
    "clear-for-submission": "looks_ok",
    "specialist-review": "needs_expert",
}

STATUS_WORDS = {"satisfied": "OK", "violated": "Problem", "needs_review": "Needs a person", "info": "For your information"}


def rule_title(text: str) -> str:
    """'Clause ZR.4.1.2.b - Height limit on a narrow road, residential main zone. ...' -> 'Height limit on a narrow road, residential main zone'."""
    m = re.match(r"^(?:Clause|Table)\s+[\w.]+\s+-\s+(.*?)\.(?:\s|$)", text or "")
    t = m.group(1) if m else (text or "")[:70]
    for pat, rep in _SIMPLE:
        t = re.sub(pat, rep, t, flags=re.I)
    return t[:1].upper() + t[1:]


def build(route: str, code: str, findings: list, degraded: bool, titles: dict) -> dict:
    problems = [{"rule": f.get("rule") or titles.get(f.get("clause_id"), f.get("clause_id")), "why": f.get("reason", "")}
                for f in findings if f.get("status") == "violated"]
    to_check = [f.get("rule") or titles.get(f.get("clause_id"), f.get("clause_id"))
                for f in findings if f.get("status") == "needs_review"]
    verdict = VERDICT.get(route, "needs_expert")
    n = len(problems)

    if verdict == "needs_changes":
        headline = f"{n} thing{'s' if n != 1 else ''} to fix"
        explain = "These rules are broken: " + "; ".join(p["rule"].lower() for p in problems) + "."
        nxt = "Change the design, then check it again."
    elif verdict == "looks_ok":
        headline = "No problems found"
        explain = ("None of the rules we looked up are broken. We did not check floor area, how much of the plot you "
                   "cover, or parking, because those tables are not in our documents. This is not an approval. "
                   "An architect or engineer still has to sign off.")
        nxt = "Have a licensed professional review the plan before you submit it."
    else:
        headline = "A person needs to look at this"
        explain = {
            "injection": "The notes contained text that looks like an instruction to the system. We ignored the notes.",
            "out_of_corpus": "The rules we have do not seem to cover this building, so we cannot say anything reliable.",
            "verify_failed": "We could not back up our own answer with the rulebook, so we are not giving one.",
            "conflict": "Two rules disagree with each other. A person has to decide which one wins.",
            "exemption": "A special-permission rule might apply. Only a person can decide that.",
            "needs_review": "Some rules need a judgement call, or more details than the form asks for"
                            + (": " + "; ".join(t.lower() for t in to_check) + "." if to_check else "."),
            "degraded": "The AI service was not available, so we only collected the rules that look relevant.",
        }.get(code, "The system was not sure enough to decide.")
        nxt = "Send this case to a licensed architect or engineer."
    return {"verdict": verdict, "headline": headline, "explain": explain, "next_step": nxt,
            "problems": problems, "degraded": degraded}
