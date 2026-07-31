"""Run the whole pipeline in order, from the project root folder:

    python run_all.py

It runs Step 1 (enhance), Step 2 (categorize, aggregate, merge) and Step 4 (prompt).
Step 3 (select) is skipped here because it is a manual choice in the browser. Make
sure you have run Step 3 and saved select/output_select/selected_skills.csv before
Step 4, otherwise Step 4 will stop with a clear message.

Each command must succeed before the next one runs.
"""

import subprocess
import sys

STEPS = [
    ("Step 1: enhance", ["python", "enhance/enhance_green_skills.py"]),
    ("Step 2a: categorize", ["python", "categorize/categorize.py"]),
    ("Step 2b: aggregate", ["python", "categorize/aggregate.py"]),
    ("Step 2c: merge", ["python", "categorize/merge_categories.py"]),
    ("Step 4a: build prompts", ["python", "prompt/build_prompts.py"]),
    ("Step 4b: generate responses", ["python", "prompt/generate_responses.py"]),
]


def main():
    for label, cmd in STEPS:
        print(f"\n=== {label} ===")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"\n{label} failed (exit {result.returncode}). Stopping.")
            sys.exit(result.returncode)

    print("\n=== Checks ===")
    subprocess.run(["python", "check_outputs.py"])
    print("\nAll steps finished.")


if __name__ == "__main__":
    main()
