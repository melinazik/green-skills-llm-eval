"""Run the whole pipeline in order, from the project root folder:

    python run_all.py                  # full run
    python run_all.py --limit 5        # first 5 skills only, for a quick test
    python run_all.py --mock           # no LLM calls at all, just the plumbing

It runs Step 1 (enhance), Step 2 (categorize, aggregate, merge) and Step 4 (prompt).
Step 3 (select) is skipped here because it is a manual choice in the browser. Make
sure you have run Step 3 and saved select/output_select/selected_skills.csv before
Step 4, otherwise Step 4 will stop with a clear message.

--limit caps the number of skills in the two steps that call the models, so a test
run takes minutes instead of hours. The models run on the CPU, so a full run is
slow: count on roughly 20 seconds per call, and Step 4 makes 8 prompts x 3 models
= 24 calls per skill.

Each command must succeed before the next one runs.
"""

import argparse
import subprocess
import sys

# accepts_limit / accepts_mock say which scripts understand those flags.
# Only the steps that call the models do.
STEPS = [
    ("Step 1: enhance", ["python", "enhance/enhance_green_skills.py"], False, False),
    ("Step 2a: categorize", ["python", "categorize/categorize.py"], True, True),
    ("Step 2b: aggregate", ["python", "categorize/aggregate.py"], False, False),
    ("Step 2c: merge", ["python", "categorize/merge_categories.py"], False, False),
    ("Step 4: generate responses", ["python", "prompt/generate_responses.py"], True, True),
]


def main():
    parser = argparse.ArgumentParser(description="Run the whole pipeline in order")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="cap the number of skills in the steps that call the models (test runs)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="use synthetic answers instead of calling the models (offline test)",
    )
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be 1 or more")

    if args.limit is not None:
        print(f"Test run: at most {args.limit} skills in the steps that call the models")
    if args.mock:
        print("Mock run: no model is called, the answers are synthetic")

    for label, base_cmd, accepts_limit, accepts_mock in STEPS:
        cmd = list(base_cmd)
        if accepts_limit and args.limit is not None:
            cmd += ["--limit", str(args.limit)]
        if accepts_mock and args.mock:
            cmd += ["--mock"]

        print(f"\n=== {label} ===")
        print(" ".join(cmd))
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"\n{label} failed (exit {result.returncode}). Stopping.")
            sys.exit(result.returncode)

    print("\n=== Checks ===")
    subprocess.run(["python", "check_outputs.py"])
    print("\nAll steps finished.")


if __name__ == "__main__":
    main()
