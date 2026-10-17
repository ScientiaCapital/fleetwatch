"""The fixed evaluator for the redaction loop (docs/design/redaction-loop.md). The loop's agent never edits this file.

It scores src/fleetwatch/redact.py against the shared corpus plus a held-out dev set. The metric is how many
(case, function) pairs fail, lower is better. A case fails when a `must_not_contain` string survives redaction,
a `must_contain` string is destroyed, or redacting twice differs from redacting once.

It also refuses any change outside src/fleetwatch/redact.py: the loop edits one file, and this checks it.

    uv run python scripts/redact_loop/run_eval.py                      # shared corpus + dev set
    uv run python scripts/redact_loop/run_eval.py --gate PATH          # also a hidden gate set, kept outside the repo

Exit 0: scored (read `metric:`). Exit 2: a file other than redact.py changed. Exit 3: the code crashed, timed out
or broke the unit tests. All case values are invented.
"""

import argparse
import json
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = frozenset({"src/fleetwatch/redact.py"})
SHARED = ROOT / "tests" / "redaction-cases.json"
DEV = ROOT / "tests" / "redaction-holdout.json"
TIMEOUT_S = 60


def load_cases(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def failures(cases: list[dict[str, Any]], fns: dict[str, Callable[[Any], Any]]) -> list[str]:
    """One line per failing (case, function) pair."""
    out: list[str] = []
    for case in cases:
        for name, fn in fns.items():
            once = fn(case["input"])
            text = once if isinstance(once, str) else json.dumps(once)
            problems = [f"leaks {s!r}" for s in case["must_not_contain"] if s in text]
            problems += [f"destroys {s!r}" for s in case.get("must_contain", []) if s not in text]
            if fn(once) != once:
                problems.append("not idempotent")
            if problems:
                out.append(f"{case['id']} [{name}]: {'; '.join(problems)}")
    return out


def forbidden(changed: set[str], allowed: frozenset[str] = ALLOWED) -> list[str]:
    return sorted(changed - allowed)


def changed_files(base: str) -> set[str]:
    """Every file that differs from `base`, including uncommitted and untracked ones."""

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout

    merge_base = git("merge-base", "HEAD", base).strip()
    names = set(git("diff", "--name-only", merge_base).split())
    names |= set(git("ls-files", "--others", "--exclude-standard").split())
    return names


def unit_tests_pass() -> bool:
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "tests/test_redact.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return run.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gate", type=Path, help="a hidden held-out set, kept outside the repo; scored separately")
    ap.add_argument("--base", default="main", help="the branch the loop started from (default: main)")
    ap.add_argument("--no-tests", action="store_true", help="skip tests/test_redact.py (it takes about a second)")
    args = ap.parse_args()

    bad = forbidden(changed_files(args.base))
    if bad:
        print("metric: inf\nforbidden: " + ", ".join(bad) + "\nonly src/fleetwatch/redact.py may change")
        return 2

    def timed_out(*_: object) -> None:
        raise TimeoutError

    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(TIMEOUT_S)
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from fleetwatch.redact import redact, scrub_text

        fns = {"scrub_text": scrub_text, "redact": redact}
        found = failures(load_cases(SHARED) + load_cases(DEV), fns)
        gate = failures(load_cases(args.gate), fns) if args.gate else None
    except BaseException as e:  # noqa: BLE001  (any crash or timeout in redact.py scores as a failed run)
        print(f"metric: inf\ncrash: {type(e).__name__}: {e}")
        return 3
    finally:
        signal.alarm(0)
    if not args.no_tests and not unit_tests_pass():
        print("metric: inf\ncrash: tests/test_redact.py fails")
        return 3

    for line in found:
        print(f"  {line}")
    print(f"metric: {len(found)}")
    if gate is not None:
        print(f"gate_metric: {len(gate)}")  # the number only: the hidden cases are never printed
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
