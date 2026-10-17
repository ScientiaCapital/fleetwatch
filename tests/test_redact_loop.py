"""The redaction loop's fixed evaluator: it must score honestly and refuse a change to any other file."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_eval", ROOT / "scripts" / "redact_loop" / "run_eval.py")
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)

CASES = [
    {"id": "leaky", "input": "password=FAKE1", "must_not_contain": ["FAKE1"]},
    {"id": "kept", "input": "room: Hall A", "must_not_contain": [], "must_contain": ["Hall A"]},
]


def test_a_redactor_that_does_nothing_leaks_the_secret_case():
    out = run_eval.failures(CASES, {"noop": lambda text: text})
    assert out == ["leaky [noop]: leaks 'FAKE1'"]


def test_a_redactor_that_masks_everything_destroys_the_kept_case():
    out = run_eval.failures(CASES, {"all": lambda _: "[redacted]"})
    assert out == ["kept [all]: destroys 'Hall A'"]


def test_a_redactor_that_is_not_idempotent_fails():
    out = run_eval.failures(CASES[1:], {"grows": lambda text: text + "!"})
    assert out == ["kept [grows]: not idempotent"]


def test_a_good_redactor_scores_zero():
    def masks_the_password(text):
        return text.replace("FAKE1", "[redacted]")

    assert run_eval.failures(CASES, {"ok": masks_the_password}) == []


@pytest.mark.parametrize(
    ("changed", "bad"),
    [
        ({"src/fleetwatch/redact.py"}, []),
        (set(), []),
        ({"src/fleetwatch/redact.py", "tests/test_redact.py"}, ["tests/test_redact.py"]),
        ({"tests/redaction-cases.json"}, ["tests/redaction-cases.json"]),
        ({"src/fleetwatch/epiphan/mcp.py", "tool_policy.yaml"}, ["src/fleetwatch/epiphan/mcp.py", "tool_policy.yaml"]),
        ({"scripts/redact_loop/run_eval.py"}, ["scripts/redact_loop/run_eval.py"]),
    ],
)
def test_only_redact_py_may_change(changed, bad):
    assert run_eval.forbidden(changed) == bad


def test_the_shipped_case_files_are_well_formed():
    for path in (run_eval.SHARED, run_eval.DEV):
        data = json.loads(path.read_text(encoding="utf-8"))
        ids = [c["id"] for c in data["cases"]]
        assert len(ids) == len(set(ids)), f"{path.name}: duplicate ids"
        assert all("must_not_contain" in c and "input" in c for c in data["cases"])
    shared = {c["id"] for c in run_eval.load_cases(run_eval.SHARED)}
    assert not shared & {c["id"] for c in run_eval.load_cases(run_eval.DEV)}, "a dev id repeats a shared id"


def test_the_dev_set_has_known_gaps_and_keep_cases_that_pass_today():
    """The baseline the loop starts from: the 'keep' cases already pass, so only real gaps count against the metric."""
    from fleetwatch.redact import redact, scrub_text

    out = run_eval.failures(run_eval.load_cases(run_eval.DEV), {"scrub_text": scrub_text, "redact": redact})
    assert out, "the dev set should have gaps for the loop to close; if it is empty, add new cases"
    assert not [line for line in out if line.startswith("keep-")], "a 'keep' case fails: the loop would over-redact"
