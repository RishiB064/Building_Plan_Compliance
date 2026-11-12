"""D6: stub model service. Deterministic answers after a configurable delay.

Speaks the same OpenAI-compatible protocol as a real provider, so load tests and the evaluation exercise the
real client, agents, queue and index while sidestepping provider rate limits.

It is a stand-in for a *competent* model, not a rubber stamp: the assessor reads the numbers out of the rule
text it is handed and does the arithmetic. If a rule was never retrieved, the stub cannot judge it. That makes
retrieval gaps visible in the evaluation. It only understands the wording of the corpus in corpus/ (Bengaluru zonal regulations amendment 2025 and bye-law extracts).

Demo switches (test hooks, not secrets):
  POST /admin/mode {"mode": "ok" | "down" | "slow"}   "down" returns 503 (circuit-breaker demo)
  notes containing STUB_FAIL_VERIFY                    first assessment quotes text that is not in the rule
"""
import asyncio
import json
import os
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="llm-stub")
STATE = {"mode": "ok"}
DELAY = float(os.environ.get("STUB_DELAY_MS", "1500")) / 1000


def _grab(prompt: str, label: str) -> str:
    m = re.search(rf"^{label}: (.*)$", prompt, re.M)
    return m.group(1) if m else ""


def decide(prompt: str) -> dict:
    step = int(_grab(prompt, "STEP") or 0)
    req = json.loads(_grab(prompt, "REQUEST_JSON") or "{}")
    plan = [
        ("find the gaps required between a building and the plot edge",
         "Table 8 gap between the building and the plot edge setbacks site area", 6),
        ("check which gap table applies, by the height of the building",
         "setbacks total height up to 12.0 m excluding the stilt floor Table 8; buildings more than 12.0 m Table 9", 4),
        ("look for height limits, which are written separately from the gap table",
         "maximum permissible building height plots up to 250 sq.m excluding stilt floor", 4),
        ("look for the height limit that depends on the road in front",
         f"maximum height restricted if road width is less than 9.0 m (road {req.get('road_width_m')} m)", 4),
        ("check fire safety and lifts for tall buildings",
         "fire and life safety high-rise height 15.0 m and above lifts", 4),
        ("check the height of an open parking floor",
         "stilt floor open parking area at ground level minimum maximum height", 3),
        ("check what the rules say about floor area, plot coverage and parking",
         "FAR coverage parking requirements as per zonal regulations", 4),
    ]
    if step < len(plan):
        why, q, k = plan[step]
        return {"action": "tool", "tool": "search_corpus", "why": why, "args": {"query": q, "k": k}}
    return {"action": "finish", "why": "every topic has been searched"}


def _span(text: str) -> str:
    """A verbatim span of the rule: the rule sentence(s) after the title."""
    parts = text.split(". ")
    if len(parts) < 2:
        return text[:200]
    if parts[0].startswith(("Table", "Clause")):
        return ". ".join(parts[1:])[:220]
    return parts[1][:220]


def _row_applies(text: str, plot: float) -> bool:
    m = re.search(r"site area up to (\d+(?:\.\d+)?) sq\.m", text)
    if m:
        return plot <= float(m[1])
    m = re.search(r"site area above (\d+(?:\.\d+)?) sq\.m and up to (\d+(?:\.\d+)?) sq\.m", text)
    if m:
        return float(m[1]) < plot <= float(m[2])
    m = re.search(r"above (\d+(?:\.\d+)?) sq\.m: minimum setback", text)
    return bool(m) and plot > float(m[1])


def _side_rules(text: str, depth, width):
    """Read the required gaps out of the retrieved Table 8 row. Returns (rules, missing_dims).
    A rule is (side label, required metres, 'one' = at least one of left/right)."""
    m = re.search(r"minimum setback of (\d+(?:\.\d+)?) m on all sides", text)
    if m:
        v = float(m[1])
        return [("front", v), ("rear", v), ("left", v), ("right", v)], False
    if "percent of the depth" in text:
        if not depth or not width:
            return [], True
        pf = float(re.search(r"front side (\d+(?:\.\d+)?) percent", text)[1])
        pr = float(re.search(r"rear side (\d+(?:\.\d+)?) percent", text)[1])
        pl = float(re.search(r"left side (\d+(?:\.\d+)?) percent", text)[1])
        pg = float(re.search(r"right side (\d+(?:\.\d+)?) percent", text)[1])
        return [("front", pf / 100 * depth), ("rear", pr / 100 * depth),
                ("left", pl / 100 * width), ("right", pg / 100 * width)], False
    rules = [("front", float(re.search(r"front side (\d+(?:\.\d+)?)", text)[1]))]
    mr = re.search(r"rear side (\d+(?:\.\d+)?)", text)
    if mr:
        rules.append(("rear", float(mr[1])))
    one = re.search(r"left or right side (\d+(?:\.\d+)?) m on any one side", text)
    if one:
        rules.append(("one side", float(one[1])))
    else:
        rules.append(("left", float(re.search(r"left side (\d+(?:\.\d+)?)", text)[1])))
        rules.append(("right", float(re.search(r"right side (\d+(?:\.\d+)?)", text)[1])))
    return rules, False


def assess(prompt: str) -> dict:
    attempt = int(_grab(prompt, "ATTEMPT") or 0)
    req = json.loads(_grab(prompt, "REQUEST_JSON") or "{}")
    passages = {p["clause_id"]: p for p in json.loads(_grab(prompt, "PASSAGES_JSON") or "[]")}
    plot, road, h = req["plot_area_sqm"], req["road_width_m"], req["building_height_m"]
    stilt = req.get("stilt_height_m") or 0
    h_ex = round(h - stilt, 4)                       # height not counting an open parking (stilt) floor
    small = plot <= 250
    resid = req["building_use"] == "residential" and req["zone"] == "residential"
    given = {"front": req["front_setback_m"], "rear": req["rear_setback_m"],
             "left": req["left_setback_m"], "right": req["right_setback_m"]}
    fail = "STUB_FAIL_VERIFY" in prompt and attempt == 0
    findings = []

    def add(cid, status, reason, **extra):
        p = passages.get(cid)
        if p is None:                                 # the rule was never retrieved: the stub cannot judge it
            return
        findings.append({"clause_id": cid, "status": status, "reason": reason,
                         "evidence_quote": "fabricated text that is not in this rule" if fail else _span(p["text"]),
                         **extra})

    # --- which setback table applies ---
    table8 = h_ex <= 12.0 or (small and h <= 15.0)
    if h_ex <= 12.0:
        add("ZR.3.1.ii", "info", f"Your building is {h_ex:g} m tall, not counting any open parking floor, "
                                  "so the gap rules in Table 8 apply.")
    elif small and h <= 15.0:
        add("ZR.3.1.iii", "info", f"Your building is above 12 m, but the plot is {plot:g} sq m (250 or less) and the "
                                   f"building is {h:g} m or less including any parking floor, so Table 8 still applies.")
    else:
        add("ZR.3.1.iii", "needs_review", f"Your building is {h_ex:g} m tall, which is above 12 m. Its gap rules are in "
                                          "Table 9, and that table is not in the documents we have. A person has to check the gaps.")

    # --- Table 8 row for this plot ---
    if table8:
        for cid, p in passages.items():
            if not cid.startswith("ZR.T8.") or not _row_applies(p["text"], plot):
                continue
            rules, missing = _side_rules(p["text"], req.get("plot_depth_m"), req.get("plot_width_m"))
            if missing:
                add(cid, "needs_review", f"For a {plot:g} sq m plot the gaps are a share of the plot's depth and width, "
                                         "and the form did not give the depth and width. A person has to check the gaps.")
                continue
            worst, bad = None, []
            for side, need in rules:
                have = max(given["left"], given["right"]) if side == "one side" else given[side]
                label = "one of the two sides" if side == "one side" else f"the {side} gap"
                if have + 1e-9 < need:
                    bad.append(f"{label} is {have:g} m but at least {need:.2f} m is needed".replace(".00 m", " m"))
                gap = need - have
                if worst is None or gap > worst[0]:
                    worst = (gap, side, have, need)
            _, side, have, need = worst
            if bad:
                add(cid, "violated", "The gaps are too small: " + "; ".join(bad) + ".",
                    actual=round(have, 2), limit=round(need, 2), unit="m", limit_type="at least")
            else:
                add(cid, "satisfied", f"All the gaps meet the minimum for a {plot:g} sq m plot.",
                    actual=round(given["front"], 2), limit=round(dict(rules).get("front", need), 2),
                    unit="m", limit_type="at least")

    # --- height limit for small plots ---
    if small:
        add("ZR.T9.N1", "violated" if h_ex > 12.0 else "satisfied",
            f"Plots up to 250 sq m can be at most 12 m tall, not counting an open parking floor. Yours is {h_ex:g} m.",
            actual=h_ex, limit=12.0, unit="m", limit_type="at most")

    # --- height limit on a narrow road (written for homes in a residential area) ---
    if not resid:
        add("ZR.4.1.2.b", "needs_review", "This height limit is written for homes in a residential area. The height "
                                          "rules for other kinds of building or area are not in the documents we have.")
    elif road < 9.0:
        add("ZR.4.1.2.b", "violated" if h > 15.0 else "satisfied",
            f"The road in front is {road:g} m wide, which is under 9 m, so the building can be at most 15 m tall "
            f"including any parking floor. Yours is {h:g} m.", actual=h, limit=15.0, unit="m", limit_type="at most")
    else:
        add("ZR.4.1.2.b", "info", f"The road in front is {road:g} m wide. The 15 m height limit only applies to roads "
                                  "narrower than 9 m, so it does not limit this building.")

    # --- tall buildings ---
    if h >= 15.0:
        if h <= 21.0:
            add("ZR.T9.N6", "info", f"Your building is {h:g} m tall. Buildings 15 m and taller must follow the National "
                                    "Building Code fire and life safety rules, so fire safety papers will be needed.")
        else:
            add("ZR.T9.N6", "needs_review", f"Your building is {h:g} m tall. The rules we have only cover up to 21 m, "
                                            "so a person has to check this one.")
        add("BL.12.9", "info", f"Your building is {h:g} m tall, so it counts as high-rise and a lift is required.")

    # --- stilt (open parking) floor ---
    if stilt > 0:
        if stilt < 2.4 or stilt > 3.75:
            add("BL.2.149", "violated", f"An open parking floor must be between 2.4 m and 2.7 m high, or up to 3.75 m "
                                        f"with mechanical parking. Yours is {stilt:g} m.",
                actual=stilt, limit=2.7 if stilt > 3.75 else 2.4, unit="m",
                limit_type="at most" if stilt > 3.75 else "at least")
        elif stilt > 2.7:
            add("BL.2.149", "needs_review", f"Your open parking floor is {stilt:g} m high. That is only allowed with "
                                            "mechanical parking, and the form does not say. A person has to check.")
        else:
            add("BL.2.149", "satisfied", f"Your open parking floor is {stilt:g} m high, which is within 2.4 to 2.7 m.",
                actual=stilt, limit=2.7, unit="m", limit_type="at most")

    # --- what we cannot check ---
    add("BL.5.2.7.1", "info", "Not checked: how much floor area, how much of the plot you may cover, and how much parking "
                              "you need. The bye-laws leave those numbers to the zonal regulations, and those tables are not in "
                              "the documents we have.")
    return {"findings": findings, "conflicts": [], "exemption_possible": False}


@app.post("/admin/mode")
async def set_mode(body: dict):
    STATE["mode"] = body.get("mode", "ok")
    return STATE


@app.get("/health")
async def health():
    return {"status": "ok", "mode": STATE["mode"]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    if STATE["mode"] == "down":
        return JSONResponse({"error": "stub is in down mode"}, status_code=503)
    await asyncio.sleep(DELAY * (4 if STATE["mode"] == "slow" else 1))
    prompt = body["messages"][-1]["content"]
    out = decide(prompt) if "TASK: RESEARCH" in prompt else assess(prompt)
    text = json.dumps(out)
    return {"choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": len(text) // 4}}
