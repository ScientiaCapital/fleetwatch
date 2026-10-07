## What and why

<!-- One concern per PR. Link the issue if there is one. -->

## Evidence

<!-- The commands you ran and what they printed. Required. -->

```
uv run python -m pytest -q
uv run fleetwatch digest --replay tests/fixtures
```

## Checklist

- [ ] PR title follows Conventional Commits (`fix(scope): ...`); it becomes the squash commit and the release note
- [ ] No real device names, IDs, IPs, serials, stream keys or people's names in code, fixtures or this PR
- [ ] No write tool added to the `read` list, and the guard is not weakened
- [ ] New tool results go through `redact()`; new secret shapes have a test in `tests/test_redact.py`
- [ ] Docs updated if behaviour or setup changed
