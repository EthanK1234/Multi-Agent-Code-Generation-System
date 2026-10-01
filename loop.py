from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from llm import call, fenced_blocks, FAST_MODEL
from setup import SetupArtifacts, input_key, _approx_equal


# ── Result types ─────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    code: str
    pytest_passed: bool = False
    pytest_summary: str = ""
    n_passed: int = 0
    checked: bool = False # ran differential?
    mismatches: List[Dict[str, str]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """Passed pytest AND matched the reference on every cached input."""
        return self.pytest_passed and self.checked and not self.mismatches


@dataclass
class LoopResult:
    status: str # "success" or "max_iterations_reached"
    code: str
    iterations: int
    history: List[Dict[str, Any]] = field(default_factory=list)



_GENERATOR_SYSTEM = """You are a strong competitive programmer. Write a correct, efficient Python solution to the given problem.

Rules:
- Output ONE fenced ```python``` block containing the full module. No prose.
- Match the exact function/class signature the spec states. If the spec shows `class Solution` with a method, use exactly that.
- If a test suite is provided, it imports your module as `solution` - expose exactly what it calls.
- Standard library only.
- Prefix any helper methods on `Solution` with an underscore, so the class has exactly one public method.
- If feedback from a previous attempt is provided, it contains concrete failing inputs with the correct expected output. Fix the underlying bug; do not special-case those inputs."""


def generate_candidate(task: str, tests: str, feedback: Optional[str] = None) -> str:
    user = f"# Task\n{task}\n\n# Test suite (interface contract)\n```python\n{tests}\n```\n"
    if feedback:
        user += f"\n# Your previous best attempt failed\n{feedback}\n\nWrite a corrected solution."
    blocks = fenced_blocks(call(_GENERATOR_SYSTEM, user, model=FAST_MODEL))
    if not blocks:
        raise ValueError("generator returned no fenced python block")
    return max(blocks, key=len)



def run_pytest(code: str, tests: str, timeout: int = 30) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="magl_pytest_") as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "solution.py").write_text(code)
        (tmpdir / "test_solution.py").write_text(tests)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "test_solution.py", "-q",
                 "--tb=short", "-p", "no:cacheprovider", f"--rootdir={tmpdir}"],
                cwd=tmpdir, capture_output=True, text=True, timeout=timeout,
            )
            output, returncode = proc.stdout + proc.stderr, proc.returncode
        except subprocess.TimeoutExpired:
            output, returncode = f"pytest timed out after {timeout}s (infinite loop?)", -1

    n_passed = _count(r"(\d+) passed", output)
    return {
        "passed": returncode == 0 and n_passed > 0,
        "n_passed": n_passed,
        "summary": output[-2000:],
    }


def _count(pattern: str, text: str) -> int:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else 0


# differential check against the reference cache 

_HARNESS = '''
import json
import solution

def _entry(mod):
    cls = getattr(mod, "Solution", None)
    if cls is not None:
        inst = cls()
        methods = [m for m in dir(inst) if not m.startswith("_") and callable(getattr(inst, m))]
        if len(methods) != 1:
            raise ValueError(f"expected 1 public method on Solution, found {methods}")
        return getattr(inst, methods[0])
    fns = [v for k, v in vars(mod).items()
           if callable(v) and not k.startswith("_") and not isinstance(v, type)
           and getattr(v, "__module__", None) == mod.__name__]
    if len(fns) != 1:
        raise ValueError(f"could not resolve a unique entry point: {[f.__name__ for f in fns]}")
    return fns[0]

fn = _entry(solution)
with open("inputs.json") as f:
    inputs = json.load(f)

results = []
for args in inputs:
    try:
        results.append({"ok": True, "out": fn(*args)})
    except Exception as e:
        results.append({"ok": False, "err": f"{type(e).__name__}: {e}"})

print(json.dumps(results, default=repr))
'''


def run_differential(
    code: str,
    artifacts: SetupArtifacts,
    tol: float = 1e-5,
    timeout: int = 30,
    max_report: int = 5,
) -> List[Dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="magl_diff_") as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "solution.py").write_text(code)
        (tmpdir / "inputs.json").write_text(json.dumps([list(a) for a in artifacts.inputs]))
        (tmpdir / "harness.py").write_text(_HARNESS)
        try:
            proc = subprocess.run(
                [sys.executable, "harness.py"],
                cwd=tmpdir, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return [{"input": "(all)", "problem": f"timed out after {timeout}s"}]

    if proc.returncode != 0:
        return [{"input": "(all)", "problem": "harness crashed: " + proc.stderr[-500:]}]
    try:
        results = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return [{"input": "(all)", "problem": "unreadable harness output"}]

    mismatches = []
    for args, result in zip(artifacts.inputs, results):
        expected = artifacts.reference_cache[input_key(args)]
        if not result["ok"]:
            problem = f"raised {result['err']}"
        elif not _approx_equal(result["out"], expected, tol):
            problem = f"got {result['out']!r}, expected {expected!r}"
        else:
            continue
        mismatches.append({"input": repr(args), "problem": problem})
        if len(mismatches) >= max_report:
            break
    return mismatches


def evaluate(code: str, artifacts: SetupArtifacts) -> Candidate:
    candidate = Candidate(code=code)
    result = run_pytest(code, artifacts.tests)
    candidate.pytest_passed = result["passed"]
    candidate.n_passed = result["n_passed"]
    candidate.pytest_summary = result["summary"]
    if candidate.pytest_passed:
        candidate.mismatches = run_differential(code, artifacts)
        candidate.checked = True
    return candidate


def rank_key(candidate: Candidate):
    return (candidate.clean, candidate.pytest_passed, candidate.n_passed,
            -len(candidate.mismatches))


def build_feedback(candidate: Candidate) -> str:
    if not candidate.pytest_passed:
        return "## Test failures\n```\n" + candidate.pytest_summary + "\n```"
    lines = ["## Wrong answers on these inputs (expected values are correct)"]
    for m in candidate.mismatches:
        lines.append(f"- input {m['input']}: {m['problem']}")
    return "\n".join(lines)



def run_loop(
    artifacts: SetupArtifacts,
    samples: int = 3,
    max_iterations: int = 3,
    log: Callable[[str], None] = print,
) -> LoopResult:
    feedback: Optional[str] = None
    best: Optional[Candidate] = None
    history: List[Dict[str, Any]] = []

    for iteration in range(1, max_iterations + 1):
        log(f"\n=== iteration {iteration} ===")

        log(f"[generate] {samples} candidates...")
        with ThreadPoolExecutor(max_workers=samples) as pool:
            codes = list(pool.map(
                lambda _: generate_candidate(artifacts.task, artifacts.tests, feedback),
                range(samples),
            ))

        log("[evaluate] pytest filter + differential check...")
        with ThreadPoolExecutor(max_workers=samples) as pool:
            candidates = list(pool.map(lambda c: evaluate(c, artifacts), codes))

        winner = max(candidates, key=rank_key)
        if best is None or rank_key(winner) > rank_key(best):
            best = winner

        n_pytest = sum(c.pytest_passed for c in candidates)
        n_clean = sum(c.clean for c in candidates)
        log(f"[select] {n_pytest}/{samples} passed pytest, {n_clean}/{samples} clean")
        history.append({"iteration": iteration, "pytest_passed": n_pytest, "clean": n_clean})

        if winner.clean:
            log("[done] candidate passed pytest and matched the reference.")
            return LoopResult("success", winner.code, iteration, history)

        feedback = build_feedback(winner)

    log(f"[stop] no clean candidate after {max_iterations} iterations.")
    return LoopResult("max_iterations_reached", best.code, max_iterations, history)