"""Independent answer key for the evaluation (L3).

This is NOT the system under test. It is plain code that applies the rules in the supplied documents to a proposal
and says what the right answer is:
  ZR  = Zonal Regulations of the Revised Master Plan-2015, Bengaluru, Amendment Regulations 2025
        (Notification UDD 235 MNJ 2025(E), 05.01.2026): Table 8, regulation 3.1(ii)/(iii), notes to Table 9,
        regulation 4.1.2 note (b)
  BL  = Karnataka Municipal Corporations Model Building Bye-Laws 2017: definition 149 (stilt floor), 12.9 (lifts)
Not in the documents, so deliberately not checked here (the system must say so, never guess): Table 9, Table 10
(FAR, coverage), parking norms, rules for zones other than residential.
"""


def _row(plot: float):
    if plot <= 60:
        return "ZR.T8.r1"
    if plot <= 150:
        return "ZR.T8.r2"
    if plot <= 250:
        return "ZR.T8.r3"
    if plot <= 4000:
        return "ZR.T8.r4"
    return "ZR.T8.r5"


def reference(req: dict, injection: bool = False) -> dict:
    plot, road, h = req["plot_area_sqm"], req["road_width_m"], req["building_height_m"]
    stilt = req.get("stilt_height_m") or 0
    h_ex = h - stilt
    small = plot <= 250
    resid = req["building_use"] == "residential" and req["zone"] == "residential"
    g = {"front": req["front_setback_m"], "rear": req["rear_setback_m"],
         "left": req["left_setback_m"], "right": req["right_setback_m"]}
    depth, width = req.get("plot_depth_m"), req.get("plot_width_m")

    violations, review = set(), False
    applicable = {"ZR.4.1.2.b", "BL.5.2.7.1"}

    table8 = h_ex <= 12.0 or (small and h <= 15.0)
    applicable.add("ZR.3.1.ii" if h_ex <= 12.0 else "ZR.3.1.iii")
    if table8:
        row = _row(plot)
        applicable.add(row)
        if row == "ZR.T8.r1":
            low = g["front"] < 0.75 or max(g["left"], g["right"]) < 0.60
        elif row == "ZR.T8.r2":
            low = g["front"] < 0.90 or g["rear"] < 0.70 or max(g["left"], g["right"]) < 0.70
        elif row == "ZR.T8.r3":
            low = g["front"] < 1.0 or g["rear"] < 0.80 or g["left"] < 0.80 or g["right"] < 0.80
        elif row == "ZR.T8.r4":
            if not depth or not width:
                low, review = False, True           # the share of depth/width cannot be worked out
            else:
                low = (g["front"] < 0.12 * depth or g["rear"] < 0.08 * depth
                       or g["left"] < 0.08 * width or g["right"] < 0.08 * width)
        else:
            low = min(g.values()) < 5.0
        if low:
            violations.add(row)
    else:
        review = True                               # Table 9 is not in the documents

    if small:
        applicable.add("ZR.T9.N1")
        if h_ex > 12.0:
            violations.add("ZR.T9.N1")
    if not resid:
        review = True                               # the height rule is written for residential main zone only
    elif road < 9.0 and h > 15.0:
        violations.add("ZR.4.1.2.b")
    if h >= 15.0:
        applicable.update({"ZR.T9.N6", "BL.12.9"})
        if h > 21.0:
            review = True
    if stilt > 0:
        applicable.add("BL.2.149")
        if stilt < 2.4 or stilt > 3.75:
            violations.add("BL.2.149")
        elif stilt > 2.7:
            review = True                           # allowed only with mechanical parking; the form does not say

    if injection or review:
        route = "specialist-review"
    elif violations:
        route = "revise-and-recheck"
    else:
        route = "clear-for-submission"
    return {"route": route, "violations": violations, "applicable": applicable}
