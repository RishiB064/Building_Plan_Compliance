"""A1: tools the agent chooses to call. Retrieval is a tool, not a fixed stage.

Tools:
  search_corpus(query, k)   semantic search over bye-law chunks
  get_clause(clause_id)     exact lookup by identifier (follow a reference, re-read a clause)
  list_table(table)         every row of one table (e.g. "Table 8"), so the row for this proposal can be chosen
  get_request_field(name)   (still implemented, but not offered to the model: the proposal is already in its prompt)
Every passage returned is recorded in `retrieved`; the verifier only accepts citations that resolve to it.
"""
import re
import time

from . import guardrails

TOOL_SPECS = {
    "search_corpus": {"args": {"query": "str", "k": "int (1-6, default 4)"},
                      "desc": "Semantic search over the bye-law corpus. Returns clause_id, text, score."},
    "get_clause": {"args": {"clause_id": "str"},
                   "desc": "Fetch one clause by its identifier, e.g. 'ZR.T8.r3' or 'ZR.4.1.2.b'."},
    "list_table": {"args": {"table": "str, e.g. 'Table 8'"},
                   "desc": "Return ALL rows of one table. Use it when a table applies: pick the row from the proposal's own numbers."},
}

REQUEST_FIELDS = {"plot_area_sqm", "road_width_m", "building_height_m", "num_floors", "built_up_area_sqm",
                  "ground_coverage_pct", "parking_spaces", "building_use", "zone", "notes", "stilt_height_m",
                  "plot_depth_m", "plot_width_m", "front_setback_m", "rear_setback_m", "left_setback_m", "right_setback_m"}


class Toolbox:
    def __init__(self, proposal: dict, index):
        self.proposal, self.index = proposal, index
        self.retrieved: dict[str, dict] = {}
        self.max_score = 0.0
        self.calls = 0

    def _record(self, hits: list[dict]):
        for h in hits:
            self.retrieved.setdefault(h["clause_id"], h)
            self.max_score = max(self.max_score, h.get("score", 0.0))

    def call(self, name: str, args: dict) -> dict:
        self.calls += 1
        t0 = time.time()
        try:
            if name == "search_corpus":
                q = str(args.get("query", ""))[:300]
                k = max(1, min(6, int(args.get("k", 4))))
                hits = self.index.search(q, k)
                self._record(hits)
                res = {"hits": [{"clause_id": h["clause_id"], "score": h["score"], "text": h["text"]} for h in hits]}
            elif name == "get_clause":
                h = self.index.get(str(args.get("clause_id", "")))
                if h:
                    self._record([h])
                res = {"hits": [h]} if h else {"error": "unknown clause_id"}
            elif name == "list_table":
                m = re.search(r"(\d+)", str(args.get("table", "")))
                rows = self.index.table_rows(m.group(1)) if m else []
                self._record(rows)
                res = {"hits": rows} if rows else {"error": "no such table; try search_corpus"}
            elif name == "get_request_field":
                f = str(args.get("name", ""))
                if f not in REQUEST_FIELDS:
                    res = {"error": f"unknown field; choose from {sorted(REQUEST_FIELDS)}"}
                elif f == "notes":
                    res = {"value": guardrails.wrap_untrusted(self.proposal.get("notes", ""))}
                else:
                    res = {"value": self.proposal.get(f)}
            else:
                res = {"error": f"unknown tool {name}"}
        except Exception as e:  # tool failures are data for the agent, not crashes
            res = {"error": f"{type(e).__name__}: {e}"}
        res["_latency_ms"] = round((time.time() - t0) * 1000, 1)
        return res
