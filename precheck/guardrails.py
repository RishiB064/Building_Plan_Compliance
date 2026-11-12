"""L4: prompt-injection defence, toxicity detection, output filtering.

These are deliberately simple, inspectable heuristics. Free text is treated as untrusted data:
it is screened here, then wrapped in explicit delimiters when it reaches a model.
Upgrade path: swap `toxicity_score` for a classifier (e.g. detoxify) behind the same function.
"""
import re

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above) (instructions|prompts?|rules)",
    r"disregard (all |any |the )?(previous|prior|above|system)",
    r"(reveal|print|show|repeat) (your |the )?(system|hidden) (prompt|instructions)",
    r"you are now\b",
    r"act as (an? )?(admin|developer|system)",
    r"(mark|certify|declare|approve|stamp) (this|the) (plan|proposal|building)? ?(as )?(compliant|approved|certified)",
    r"route (this )?(to|as) clear-for-submission",
    r"</?\s*(system|assistant|tool)\s*>",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]

# Minimal toxicity lexicon. Intentionally small; replace with a model for production.
_TOXIC = [re.compile(p, re.I) for p in [
    r"\b(idiot|moron|stupid|useless)\b", r"\b(shut up)\b", r"\b(kill|hurt) (you|them|him|her)\b",
]]

EXEMPTION_HINT = re.compile(r"\b(exempt\w*|relaxation|waiver|condon\w+|regularis\w+|special permission)\b", re.I)

FORBIDDEN_OUTPUT = [re.compile(p, re.I) for p in [
    r"\b(is|are|was|will be) (fully |hereby )?(compliant|approved|certified|sanctioned)\b",
    r"\b(we |i )?(hereby )?(certify|certifies|approve|approves|sanction|sanctions)\b",
    r"\bplan (is|has been) (approved|sanctioned)\b",
]]


def screen_input(text: str) -> dict:
    hits = [p.pattern for p in _INJ if p.search(text or "")]
    tox = [p.pattern for p in _TOXIC if p.search(text or "")]
    return {
        "injection_detected": bool(hits),
        "injection_patterns": hits,
        "toxic": bool(tox),
        "exemption_hint": bool(EXEMPTION_HINT.search(text or "")),
    }


def neutralise(text: str) -> str:
    """Strip markup-like control tokens and cap length; the caller still wraps in delimiters."""
    t = re.sub(r"</?\s*\w+[^>]*>", " ", text or "")
    t = re.sub(r"[`]{3,}", " ", t)
    return t.strip()[:2000]


def wrap_untrusted(text: str) -> str:
    return f"<untrusted_notes>\n{neutralise(text)}\n</untrusted_notes>"


def filter_output(text: str) -> tuple[str, bool]:
    """Return (text, was_modified). The service may never certify or approve a plan."""
    changed = False
    out = text or ""
    for p in FORBIDDEN_OUTPUT:
        if p.search(out):
            out = p.sub("[determination removed: only a licensed professional can decide]", out)
            changed = True
    return out, changed
