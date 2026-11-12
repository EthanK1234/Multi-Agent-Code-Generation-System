from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # must run before llm builds its client

from setup import run_setup, ReferenceValidationError  # noqa: E402
from loop import run_loop  # noqa: E402


TASK = """
Sliding Window Median

You are given an integer array nums and an integer k. There is a sliding window
of size k which is moving from the very left of the array to the very right.
Each time the sliding window moves right by one position.

The median is the middle value in an ordered list. If the size of the list is
even, the median is the mean of the two middle values.

Return the median array for each window in the original array. Answers within
10^-5 of the actual value will be accepted.

Example 1:
Input: nums = [1,3,-1,-3,5,3,6,7], k = 3
Output: [1.0,-1.0,-1.0,3.0,5.0,6.0]

Example 2:
Input: nums = [1,2,3,4,2,3,1,4,2], k = 3
Output: [2.0,3.0,3.0,3.0,2.0,3.0,2.0]

Constraints:
1 <= k <= nums.length <= 10^5
-2^31 <= nums[i] <= 2^31 - 1

Use this signature:
class Solution:
    def medianSlidingWindow(self, nums: List[int], k: int) -> List[float]:
"""

SPEC_EXAMPLES = [
    (([1, 3, -1, -3, 5, 3, 6, 7], 3), [1.0, -1.0, -1.0, 3.0, 5.0, 6.0]),
    (([1, 2, 3, 4, 2, 3, 1, 4, 2], 3), [2.0, 3.0, 3.0, 3.0, 2.0, 3.0, 2.0]),
]

SETUP_ATTEMPTS = 2
OUTPUT_DIR = Path("output")


def main() -> None:
    artifacts = None
    for attempt in range(1, SETUP_ATTEMPTS + 1):
        try:
            artifacts = run_setup(TASK, SPEC_EXAMPLES, n_inputs=300)
            break
        except ReferenceValidationError as e:
            print(f"[main] reference failed validation (attempt {attempt}):\n{e}")
    if artifacts is None:
        print("[main] could not build a valid reference. Stopping.")
        return

    result = run_loop(artifacts, samples=3, max_iterations=3)

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "solution.py").write_text(result.code)
    (OUTPUT_DIR / "reference.py").write_text(artifacts.reference_code)
    (OUTPUT_DIR / "test_solution.py").write_text(artifacts.tests)

    print(f"\n[main] status: {result.status} after {result.iterations} iteration(s)")
    print(f"[main] history: {result.history}")
    print(f"[main] saved to {OUTPUT_DIR}/")
    print("\n--- FINAL CODE ---")
    print(result.code)


if __name__ == "__main__":
    main()