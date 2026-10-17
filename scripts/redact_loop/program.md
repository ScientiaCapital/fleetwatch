# Redaction loop: instructions for the agent

You are improving `src/fleetwatch/redact.py` so it hides stream keys, passwords and credentialed URLs, without
hiding ordinary words. You edit that one file. A fixed script scores you. Lower is better.

Read `docs/design/redaction-loop.md` and `AGENTS.md` (hard rule 3) first. Read `src/fleetwatch/redact.py`,
`tests/test_redact.py`, `tests/redaction-cases.json` and `tests/redaction-holdout.json`.

## Setup (with the maintainer)

1. Agree a run tag, for example `oct17`. Create the branch `autoresearch/redact-<tag>` from `main`. It must not exist.
2. Create `results.tsv` with this header row, tab-separated: `commit	metric	status	description`. It is gitignored. Never commit it.
3. Run the evaluator once for the baseline and write it as the first row (`keep`, description `baseline`).

## What you may and may not do

- Edit only `src/fleetwatch/redact.py`. The evaluator refuses any other changed file, including new files.
- Never edit `tests/`, `scripts/`, `tool_policy.yaml`, `AGENTS.md`, or anything under `src/fleetwatch/epiphan/`.
- Add no dependency. Use the standard library.
- Never read, guess or ask for the hidden gate set. Never print a secret-looking value from anywhere but the cases.
- Every value you use is invented. Never paste text from a real capture, a real device or a real account.

## The loop

Repeat until the iteration cap or the time cap the maintainer gave you, then stop and report. Do not run past it.

1. Look at the failing lines from the last run. Pick one gap. Say what shape it is in one sentence.
2. Edit `redact.py`. Keep the change small.
3. `git commit -am "<one line>"`.
4. Run `uv run python scripts/redact_loop/run_eval.py > run.log 2>&1`, then `grep -E "^(metric|crash|forbidden)" run.log`.
5. If it printed `crash` or `forbidden`, read `tail -n 30 run.log`, fix a trivial cause once or twice, otherwise
   record `crash` and `git reset --hard HEAD~1`.
6. Append a row to `results.tsv`: short commit hash, metric, `keep` / `discard` / `crash`, one line on the idea.
7. If the metric is lower than the best so far, keep the commit. If it is equal or higher, `git reset --hard HEAD~1`.

## Judging a change

- The metric counts failing (case, function) pairs across the shared corpus and the dev set. Fewer is better. A
  "keep" case that stops passing is a failure, so over-redacting does not win.
- Simplicity counts. A tiny gain that adds a long, fragile regex is not worth keeping. The same score from less code
  is a win. Watch for patterns that could run for a very long time on odd input.
- Fix the shape, not the example. A case that passes only because its exact string is special-cased is a discard.
- If you think of a shape the dev set doesn't cover, describe it in your final report. Do not add it to any
  test file. The maintainer adds cases.

## When you finish

Report the baseline and final metric, the kept changes, and any shapes you found that no case covers. Leave the
branch for review. It becomes a normal pull request: it still needs the full test suite, the replay digest, and
the hidden gate set run by the maintainer.
