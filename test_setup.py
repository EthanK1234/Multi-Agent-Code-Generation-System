# checks to make sure setup.py works with no errors
import pytest

import setup

# simulated LLM call
REFERENCE = '''
from typing import List
class Solution:
    def medianSlidingWindow(self, nums: List[int], k: int) -> List[float]:
        out = []
        for i in range(len(nums) - k + 1):
            w = sorted(nums[i:i + k])
            out.append(float(w[k // 2]) if k % 2 else (w[k // 2 - 1] + w[k // 2]) / 2)
        return out
'''

GEN = '''
import random
def gen_case(rng):
    n = rng.randint(1, 8)
    nums = [rng.randint(-5, 5) for _ in range(n)]
    return (nums, rng.randint(1, n))
'''

SPEC = [(([1, 3, -1, -3, 5, 3, 6, 7], 3), [1, -1, -1, 3, 5, 6])]


@pytest.fixture(autouse=True) # runs before every cal
def _patch_agents(monkeypatch): # monkeypatch dynamically updates 
    monkeypatch.setattr(setup, "generate_reference_and_gen", lambda task: (REFERENCE, GEN))
    monkeypatch.setattr(setup, "generate_tests", lambda task: "import solution")


def test_setup_builds_validated_cache():
    artifacts = setup.run_setup("median", SPEC, n_inputs=50, log=lambda m: None)
    assert len(artifacts.reference_cache) == 50
    assert len(artifacts.inputs) == 50
    assert artifacts.tests == "import solution"


def test_broken_reference_is_rejected(monkeypatch):
    broken = REFERENCE.replace("w[k // 2]", "w[0]")
    monkeypatch.setattr(setup, "generate_reference_and_gen", lambda task: (broken, GEN))
    with pytest.raises(setup.ReferenceValidationError):
        setup.run_setup("median", SPEC, n_inputs=10, log=lambda m: None)


def test_bare_function_reference_loads():
    bare = '''
def solve(nums, k):
    out = []
    for i in range(len(nums) - k + 1):
        w = sorted(nums[i:i + k])
        out.append(float(w[k // 2]) if k % 2 else (w[k // 2 - 1] + w[k // 2]) / 2)
    return out
'''
    fn, gen = setup._load_reference(bare, GEN)
    assert fn([1, 3, -1, -3, 5, 3, 6, 7], 3) == [1, -1, -1, 3, 5, 6]


def test_input_key_is_hashable():
    key = setup.input_key(([1, 2, 3], 2))
    assert isinstance(hash(key), int)