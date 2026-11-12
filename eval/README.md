# Evaluation set

`scenarios.jsonl` has 27 cases: 17 normal proposals (including boundary values and "plot bigger than the rulebook covers"),
1 harmless-notes case (must not trigger a guardrail), 2 prompt-injection attempts, 1 rude-notes case, 6 invalid requests.

`reference.py` is the independent answer key. It applies the rule numbers to each proposal in plain code, so the
expected route and the expected broken rules are not decided by the system being tested.
**When you switch to the real bye-laws, rewrite `reference.py` from the real clauses and adjust the scenario numbers.**

## Run it

```bash
docker compose --profile tools run --rm eval                              # one run, real index and the model in .env
docker compose --profile tools run --rm eval python -m eval.run --compare # baseline (one search only) vs agent loop
docker compose --profile tools run --rm eval python -m eval.run --check   # exit code 1 if a target is missed
python -m eval.run --offline --compare                                    # no Docker: stub + dev embedder (dev only)
```
Results are written to `eval/results/` (`latest.md` plus JSON per run).

## Metrics

| Metric | Meaning | TruLens name it mirrors |
|---|---|---|
| route_accuracy | chose the right outcome | answer correctness |
| violation_recall / precision | found the broken rules, and only those | answer correctness |
| context_recall | the rules that apply were really retrieved | context relevance |
| groundedness | every quote is word for word from the rule it cites | groundedness |
| guardrail_catch / false_positive | injection caught; harmless notes not flagged | (ours) |
| avg_tool_calls / seconds / tokens / cost | what a check costs | (ours) |

These are computed from the rule text, so they need no judge model and cost nothing. TruLens itself is not installed
(it needs a judge model and would add weight to the image). If you want its dashboard, call
`precheck.scoring.score_result` and the same retrieval records from a TruLens custom feedback function.

## Before and after (L2 / L3)

`--compare` changes one thing: the research agent may make only **1 tool call** (baseline) vs up to 8 (agent loop).
On the real regulation text with the stub model: baseline passes 16 of 35, escalates every normal case and finds none of the
violations (it never gives a wrong confident answer, it just cannot finish the job); the agent loop passes 35 of 35.
That is the measured value of the agent loop. These numbers come from the **scripted stub**, so they show the system
works as designed, not how a real model behaves.

To compare prompt versions with a real model: set `PROMPT_VERSION=v1`, run the eval, set `PROMPT_VERSION=v2`, run it again,
and compare the two `latest.md` files. The stub ignores prompt wording, so with the stub the two versions score the same.
