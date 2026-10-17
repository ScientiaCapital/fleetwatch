# Design: a redaction loop

Status: built as a local, human-gated tool. It is not part of CI and nothing in `src/` calls it.

## Why

Every tool result goes through `redact()` before it is parsed, stored, logged or shown to a model (AGENTS.md hard
rule 3). Recent fixes found gaps one at a time by hand. This borrows the loop from Karpathy's
[autoresearch](https://github.com/karpathy/autoresearch): an agent edits one file, a fixed script scores it, and a
change stays only if the score improves. Here the file is `src/fleetwatch/redact.py` and the score is how many
invented secret shapes still get through.

## Parts

| Part | Who edits it | What it is |
|---|---|---|
| `src/fleetwatch/redact.py` | the agent | The only file that may change. |
| `scripts/redact_loop/run_eval.py` | nobody in the loop | The fixed evaluator. |
| `scripts/redact_loop/program.md` | the maintainer | The agent's instructions. |
| `tests/redaction-cases.json` | both repos, together | The shared corpus. Byte-identical with the Epiphan Edge Claude Kit. |
| `tests/redaction-holdout.json` | the maintainer | The dev set: cases the loop may see. Not run by pytest. |
| a gate set outside the repo | the maintainer | Hidden cases the agent never sees. Run with `--gate PATH`. |

## The score

`run_eval.py` counts failing (case, function) pairs, for `scrub_text` and `redact`, over the shared corpus and the
dev set. A case fails if a `must_not_contain` string survives, a `must_contain` string is destroyed, or redacting
twice differs from redacting once. Lower is better. Every `keep-` case is there so that hiding everything does not
score well.

The script also refuses any change outside `redact.py`, and `tests/test_redact.py` must still pass. A crash, a
timeout (60 seconds) or a forbidden change scores as `inf` and the change is reverted.

## Two stages, each with a person in it

1. **Find gaps.** An agent proposes invented secret shapes that get through. A person decides which are in scope
   (stream keys, passwords and credentialed URLs; not every kind of token) and adds the keepers to the dev set. A
   case for the shared corpus goes to the Kit repo first.
2. **Close them.** The loop on a branch `autoresearch/redact-<tag>`, with an iteration and time cap. It ends in an
   ordinary pull request: the diff to `redact.py`, the full suite, the replay digest, and the hidden gate set.

## Limits

- **Overfitting.** The agent sees the dev set, so a good dev score proves little by itself. The hidden gate set
  decides.
- **No real data.** Every case is invented. Real captures stay off the repo and out of the loop.
- **Not "never stop".** autoresearch runs until interrupted. This stops at the cap.
- **Scope.** The dev set covers shapes in Epiphan results: stream keys, passwords, credentialed URLs. Bare vendor
  tokens (Slack, GitHub, AWS, JWT, PEM blocks) are a separate decision about scope, not something the loop settles.

## Run it

```bash
uv run python scripts/redact_loop/run_eval.py                   # baseline: the dev set has known gaps
uv run python scripts/redact_loop/run_eval.py --gate ~/private/redaction-gate.json
```
