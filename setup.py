from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

from llm import call, fenced_blocks, FAST_MODEL, STRONG_MODEL


@dataclass
class SetupArtifacts:
    task: str
    tests: str                              # pytest suite source
    reference_code: str                     # brute-force reference source
    gen_case_code: str                      # defines gen_case(rng)
    reference_cache: Dict[Any, Any] = field(default_factory=dict)   # key -> answer
    inputs: List[Tuple] = field(default_factory=list)               # arg tuples


def input_key(args: Tuple) -> Any:
    def freeze(x):
        return tuple(freeze(i) for i in x) if isinstance(x, (list, tuple)) else x
    return freeze(args)


_REFERENCE_GEN_SYSTEM = """You write correctness-checking machinery for an algorithmic problem. A separate, optimized solution will be validated by running it against your reference on the inputs your generator produces. Your job is to be OBVIOUSLY correct, not fast.

Output EXACTLY TWO fenced python blocks, reference first, in this order.

BLOCK 1 - THE REFERENCE: a brute-force reference solution.
- Correctness is the ONLY goal. Slow is fine. Brute force is PREFERRED - full enumeration, re-sorting every step, O(n^2) or O(2^n), whatever is simplest to verify by eye. Do NOT optimize; optimization is where bugs hide, and a bug here silently corrupts every answer downstream.
- Match the EXACT function/class signature the spec states, so it is a drop-in reference. If the spec shows `class Solution` with a method, match that exactly.
- Standard library only.

BLOCK 2 - THE INPUT GENERATOR: a single function `gen_case(rng)` that takes a random.Random and returns a tuple of arguments matching the solution's signature.
- Respect EVERY constraint in the spec - an out-of-spec input (e.g. k > len(nums)) causes a false mismatch and wastes a debugging cycle. Enforce constraints in code.
- Bias toward SMALL inputs: the brute-force reference must finish fast on every case. Keep sizes tiny (single digits) even when the spec allows huge inputs.
- Deliberately hit edge cases: empty, size 1, all-equal, all-duplicate, negative, min/max boundary values, and the smallest/largest legal size.
- Import only `random`.

Output ONLY the two ```python``` blocks. No prose, no explanation."""


def generate_reference_and_gen(task: str) -> Tuple[str, str]:
    user = f"# Task\n{task}\n\nOutput the two python blocks now: reference first, then gen_case."
    blocks = fenced_blocks(call(_REFERENCE_GEN_SYSTEM, user, model=STRONG_MODEL))
    if len(blocks) < 2:
        raise ValueError(f"expected 2 python blocks (reference, gen_case), got {len(blocks)}")

    reference_code, gen_case_code = blocks[0], blocks[1]
    if "def gen_case" not in gen_case_code:
        for block in blocks:
            if "def gen_case" in block:
                gen_case_code = block
            else:
                reference_code = block
    return reference_code, gen_case_code


_TESTS_SYSTEM = """You are a senior QA engineer writing a pytest suite from a problem spec you will be given. You have NOT seen any implementation - write tests from the spec alone. This suite is a cheap pre-filter, so favor assertions you can be CERTAIN are correct over clever ones you might get wrong.

Rules:
- Use pytest. Import the solution as `import solution`. Import only `pytest` and `solution`.
- Each test is one small function named `test_<what_it_checks>`.
- Cover three buckets: (1) the spec's own worked examples, (2) edge cases (empty, size 1, boundary, min/max, duplicates), (3) error cases if the spec documents any (use `pytest.raises`).

CHOOSING EXPECTED VALUES - this is the part that matters most:
- Use an exact-value assertion ONLY when the input/output pair is written verbatim in the spec. Copy those values character-for-character. Never recompute them.
- For any case YOU invent, do NOT hand-compute the answer - you will get hard problems wrong. Instead assert a PROPERTY that must hold:
    * range/bound:   assert lo <= result <= hi   (derived from constraints)
    * length/shape:  assert len(result) == expected_length
    * invariant:     a rule the answer must satisfy, checked against the input
    * monotonicity:  tighten an input, assert the answer moves the right way
- Above each assertion, add a one-line comment naming its source, e.g.
    # spec example 1
  or
    # property: output length == len(nums) - k + 1
- Use `pytest.approx` for floats.

Output ONE fenced ```python``` block. No prose outside it."""


def generate_tests(task: str) -> str:
    user = (
        f"# Task specification\n{task}\n\n"
        "Write the pytest suite. Solution module is named `solution`. "
        "Output one ```python``` block."
    )
    blocks = fenced_blocks(call(_TESTS_SYSTEM, user, model=FAST_MODEL))
    if not blocks:
        raise ValueError("tester returned no fenced python block")
    return max(blocks, key=len)



def _load_reference(reference_code: str, gen_case_code: str) -> Tuple[Callable, Callable]:
    namespace: Dict[str, Any] = {}
    exec(reference_code, namespace)
    exec(gen_case_code, namespace)

    if "gen_case" not in namespace:
        raise ValueError("input generator did not define gen_case(rng)")
    return _resolve_entry(namespace), namespace["gen_case"]


def _resolve_entry(namespace: Dict[str, Any]) -> Callable:
    cls = namespace.get("Solution")
    if cls is not None:
        instance = cls()
        methods = [m for m in dir(instance)
                   if not m.startswith("_") and callable(getattr(instance, m))]
        if len(methods) != 1:
            raise ValueError(f"expected 1 public method on Solution, found {methods}")
        return getattr(instance, methods[0])

    functions = [
        value for name, value in namespace.items()
        if callable(value)
        and not name.startswith("_")
        and name != "gen_case"
        and not isinstance(value, type)
        and getattr(value, "__module__", None) != "builtins"
    ]
    if len(functions) != 1:
        raise ValueError(f"could not resolve a unique reference entry point: {functions}")
    return functions[0]


def build_reference_cache(
    reference_fn: Callable,
    gen_case: Callable,
    n: int = 300,
    seed: int = 0,
) -> Tuple[Dict[Any, Any], List[Tuple]]:
    rng = random.Random(seed)
    cache: Dict[Any, Any] = {}
    inputs: List[Tuple] = []

    attempts = 0
    while len(cache) < n and attempts < n * 5:
        attempts += 1
        args = gen_case(rng)
        if not isinstance(args, tuple):
            args = (args,)
        key = input_key(args)
        if key not in cache:
            cache[key] = reference_fn(*args)
            inputs.append(args)
    return cache, inputs


def sanity_check_reference(
    reference_fn: Callable,
    spec_examples: List[Tuple[Tuple, Any]],
    tol: float = 1e-5,
) -> List[str]:
    failures = []
    for args, expected in spec_examples:
        got = reference_fn(*args)
        if not _approx_equal(got, expected, tol):
            failures.append(f"input={args!r}: reference gave {got!r}, spec says {expected!r}")
    return failures


def _approx_equal(a: Any, b: Any, tol: float) -> bool:
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_approx_equal(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(a - b) <= tol
        except TypeError:
            return False
    return a == b



def run_setup(
    task: str,
    spec_examples: List[Tuple[Tuple, Any]],
    n_inputs: int = 300,
    log: Callable[[str], None] = print,
) -> SetupArtifacts:
    log("[setup] generating reference + input generator...")
    reference_code, gen_case_code = generate_reference_and_gen(task)

    log("[setup] generating pytest suite...")
    tests = generate_tests(task)

    log("[setup] loading reference + gen_case...")
    reference_fn, gen_case = _load_reference(reference_code, gen_case_code)

    log(f"[setup] validating reference against {len(spec_examples)} spec example(s)...")
    failures = sanity_check_reference(reference_fn, spec_examples)
    if failures:
        raise ReferenceValidationError(failures)
    log("[setup] reference validated ok")

    log(f"[setup] building reference cache ({n_inputs} inputs)...")
    cache, inputs = build_reference_cache(reference_fn, gen_case, n=n_inputs)
    log(f"[setup] cached {len(cache)} input->answer pairs.")

    return SetupArtifacts(
        task=task,
        tests=tests,
        reference_code=reference_code,
        gen_case_code=gen_case_code,
        reference_cache=cache,
        inputs=inputs,
    )


class ReferenceValidationError(Exception):

    def __init__(self, failures: List[str]):
        self.failures = failures
        super().__init__(
            "reference failed spec-example validation - regenerate it:\n  "
            + "\n  ".join(failures)
        )