# Explaining the project in plain words

## The one-sentence version

"You type in your building's numbers, and it tells you which building rules you would break, before you send the
plan to the city office. It never approves a plan; it hands anything unclear to a person."

## The 30-second story

Getting a building plan approved means the city checks it against hundreds of rules. One broken rule and the plan
comes back, and you lose weeks. Some rules depend on several numbers at once. The hard part is that some limits are
written in a different place from the rule you are looking at. Example: a setback table says how far you must be from
the plot edge, but the height limit for your road width is in another clause that the table never mentions.
A normal search finds the first and misses the second. So we built a small team of programs that look things up,
compare, and then check their own work.

## The team (say it like this)

- **The researcher** looks up the rules. If it only found the gap table, it keeps searching for other limits.
- **The assessor** compares your numbers to each rule and writes the verdict in plain English, quoting the rule.
- **The checker** makes sure every quote really is in the rulebook and that the must-have rules were covered.
  If not, the researcher tries again. If it still fails, the answer is "ask a person". It never guesses.

## Demo, in this order (5 minutes)

1. **Open http://localhost:8000.** Press **Check my plan** with the pre-filled numbers.
   Say: "Two problems. See the bars: red means over the limit, the black line is the limit." Open *Show the exact rule text*
   on the height one: "every answer points at the real rule".
2. **Press "Within the limits", check.** "No problems found, but notice it says this is not an approval."
3. **Press "A sneaky note", check.** "Someone tried to trick it with a note. It ignored the note and sent the case to a person."
4. **Press "Numbers that don't add up", check.** "Bad data never even starts a check."
5. **Open Grafana (http://localhost:3000).** "One screen per agent: how fast, how many AI calls, what it costs,
   how often the checker sends an answer back."
6. **Open Jaeger (http://localhost:16686), service `api`, Find Traces, click one.** "This is one check from the moment
   it arrives to the answer: each search and each AI call is a bar."
7. **Break it on purpose.** `docker compose exec worker python -m precheck.cli stub-mode down`, press **Check my plan** three
   times. "The AI is down, so it stops calling it and says a person must look. No made-up answers."
   Undo: `docker compose exec worker python -m precheck.cli stub-mode ok`.
8. **Show the test results.** `docker compose --profile tools run --rm eval python -m eval.run --compare`, or open `eval/results/latest.md`.
   "27 test cases with known right answers. A simple one-search version escalated about half and never got a confident
   answer wrong; the agent version got all of them right. That is why the agent loop exists."

## Likely questions, short answers

- **Why does it take a few seconds?** Each check is about 7 AI calls in a row. The page returns at once and checks the
  result as it is ready, so one slow check never freezes the page.
- **How do you know it is not making things up?** Every finding must quote the rule word for word. A program checks that
  automatically. If any quote is not in the rule, the answer is not returned.
- **What if the AI service is down?** A circuit breaker stops calling it, the answer says so, and a person is asked to look.
- **What stops someone tricking it through the notes box?** Instruction-like text is detected, the notes are thrown away,
  and the case goes to a person. Notes are also fenced off so the AI treats them as data.
- **Why two agents and not one?** One finds and one judges. The judge can only use what the finder gave it, so it cannot
  invent a rule, and each has a clear job we can test separately.
- **Why a queue?** Checks are slow and the AI provider has limits. A queue lets us add workers without touching the web part.
- **What does it cost?** Every AI call's tokens and cost are recorded per step (Grafana, "Cost per check").
- **How did you test it?** 27 cases against an independent answer key, plus 18 automatic tests, plus a load test.

## Be upfront about these limits

- The rule text is real, but only part of it: no Table 9, no floor area/coverage/parking tables. A "clear" answer means "no broken rule among the ones we hold". Say that out loud.
- The evaluation numbers use the scripted stub model and an offline embedder. Run it again with a real model for the real score.
- A job whose worker is killed mid-check can stay "running" (known gap, workaround in the runbook).
- It checks the numbers on the form. It cannot read drawings.
