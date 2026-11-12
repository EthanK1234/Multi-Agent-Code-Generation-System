import itertools
import threading

import pytest

import loop
import setup


REFERENCE = '''
class Solution:
    def medianSlidingWindow(self, nums, k):
        out = []
        for i in range(len(nums) - k + 1):
            w = sorted(nums[i:i + k])
            out.append(float(w[k // 2]) if k % 2 else (w[k // 2 - 1] + w[k // 2]) / 2)
        return out
'''

GEN = '''
def gen_case(rng):
    n = rng.randint(1, 8)
    nums = [rng.randint(-5, 5) for _ in range(n)]
    return (nums, rng.randint(1, n))
'''


TESTS = '''
import solution
def test_spec_example():
    assert solution.Solution().medianSlidingWindow([1, 3, -1, -3, 5, 3, 6, 7], 3) == [1, -1, -1, 3, 5, 6]
'''

SPEC = [(([1, 3, -1, -3, 5, 3, 6, 7], 3), [1, -1, -1, 3, 5, 6])]


CORRECT = '''
import bisect
class Solution:
    def medianSlidingWindow(self, nums, k):
        window = sorted(nums[:k])
        out = []
        for i in range(k, len(nums) + 1):
            if k % 2:
                out.append(float(window[k // 2]))
            else:
                out.append((window[k // 2 - 1] + window[k // 2]) / 2)
            if i == len(nums):
                break
            window.pop(bisect.bisect_left(window, nums[i - k]))
            bisect.insort(window, nums[i])
        return out
'''


SNEAKY = '''
class Solution:
    def medianSlidingWindow(self, nums, k):
        return [float(sorted(nums[i:i + k])[k // 2]) for i in range(len(nums) - k + 1)]
'''


BROKEN = '''
class Solution:
    def medianSlidingWindow(self, nums, k):
        return []
'''


@pytest.fixture
def artifacts(monkeypatch):
    monkeypatch.setattr(setup, "generate_reference_and_gen", lambda task: (REFERENCE, GEN))
    monkeypatch.setattr(setup, "generate_tests", lambda task: TESTS)
    return setup.run_setup("median", SPEC, n_inputs=100, log=lambda m: None)


def test_correct_candidate_is_clean(artifacts):
    candidate = loop.evaluate(CORRECT, artifacts)
    assert candidate.pytest_passed
    assert candidate.clean


def test_sneaky_bug_passes_pytest_but_is_caught(artifacts):
    candidate = loop.evaluate(SNEAKY, artifacts)
    assert candidate.pytest_passed
    assert candidate.mismatches
    assert not candidate.clean


def test_broken_candidate_fails_pytest(artifacts):
    candidate = loop.evaluate(BROKEN, artifacts)
    assert not candidate.pytest_passed
    assert not candidate.checked   


def test_loop_picks_the_clean_candidate(artifacts, monkeypatch):
    codes = itertools.cycle([BROKEN, SNEAKY, CORRECT])
    lock = threading.Lock()

    def fake_generate(task, tests, feedback=None):
        with lock:
            return next(codes)

    monkeypatch.setattr(loop, "generate_candidate", fake_generate)
    result = loop.run_loop(artifacts, samples=3, max_iterations=2, log=lambda m: None)
    assert result.status == "success"
    assert result.iterations == 1
    assert result.code == CORRECT


def test_feedback_drives_the_fix(artifacts, monkeypatch):
    seen_feedback = []

    def fake_generate(task, tests, feedback=None):
        seen_feedback.append(feedback)
        return CORRECT if feedback else SNEAKY

    monkeypatch.setattr(loop, "generate_candidate", fake_generate)
    result = loop.run_loop(artifacts, samples=1, max_iterations=3, log=lambda m: None)
    assert result.status == "success"
    assert result.iterations == 2
    assert "expected" in seen_feedback[1]  


def test_gives_up_after_max_iterations(artifacts, monkeypatch):
    monkeypatch.setattr(loop, "generate_candidate", lambda task, tests, feedback=None: SNEAKY)
    result = loop.run_loop(artifacts, samples=1, max_iterations=2, log=lambda m: None)
    assert result.status == "max_iterations_reached"
    assert result.iterations == 2