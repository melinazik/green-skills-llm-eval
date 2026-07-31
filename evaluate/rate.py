"""Rating tool for Step 5 (evaluation). DRAFT, the rubric is provisional.

Shows each collected answer and asks the rater to score it 1 to 5 on four criteria:
  clarity      - understandable, well structured, accessible
  depth        - connects the skill to real context and sustainability reasoning
  relevance    - matches the intended ESCO skill
  pedagogical  - guides the learner with steps, examples, reasoning

Each rater runs it with their own name, so scores stay separate (needed for
inter-rater agreement):

    python evaluate/rate.py --rater maria
    python evaluate/rate.py --rater maria --limit 20    # a short session

It is resumable: answers you already scored are skipped. Scores are saved to
evaluate/output_evaluate/ratings_<rater>.csv after every entry.

The same file can be produced from the browser, in the Rate answers tab of
viewer/index.html. Both write the same columns, so evaluate/stats.py reads either.
"""

import argparse
import csv
from pathlib import Path

import pandas as pd

from rubric import CRITERIA

RESPONSES = Path("prompt/output_prompt/responses.csv")

# one rating is one (skill, prompt, model) cell, keyed the same way as Step 4
KEY_COLS = ["conceptUri", "prompt_number", "llm"]
OUTPUT_COLS = [
    "conceptUri",
    "preferredLabel",
    "prompt_number",
    "prompt_category",
    "llm",
    "rater",
] + CRITERIA


def load_done(path):
    """The (skill, prompt, model) cells this rater has already scored."""
    if not path.exists():
        return set()

    df = pd.read_csv(path, dtype=str).fillna("")
    missing = [c for c in KEY_COLS if c not in df.columns]
    if missing:
        raise SystemExit(
            f"{path} is missing the columns {missing}. It is probably an older ratings\n"
            "file from before the Step 4 schema changed. Move it aside before rating."
        )

    return set(zip(df["conceptUri"], df["prompt_number"], df["llm"]))


def ask_score(name):
    while True:
        raw = input(f"    {name} (1-5, s to skip this answer, q to stop): ").strip().lower()
        if raw in {"s", "q"}:
            return raw
        if raw in {"1", "2", "3", "4", "5"}:
            return int(raw)
        print("    please type a number 1 to 5, or s to skip, or q to stop")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rater", required=True, help="your name, keeps scores separate")
    parser.add_argument("--input", default=str(RESPONSES))
    parser.add_argument("--limit", type=int, default=None, help="stop after N answers")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"missing {input_path} - run Step 4 first")

    out_dir = Path("evaluate/output_evaluate")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ratings_{args.rater}.csv"

    df = pd.read_csv(input_path, dtype=str).fillna("")
    missing = [c for c in KEY_COLS + ["response_text", "prompt_text"] if c not in df.columns]
    if missing:
        raise SystemExit(f"{input_path} is missing the columns {missing}")

    if "error" in df.columns:
        df = df[df["error"] == ""]           # only score answers that succeeded
    df = df[df["response_text"] != ""]       # and that are not empty

    done = load_done(out_path)
    todo = [
        row for _, row in df.iterrows()
        if (row["conceptUri"], row["prompt_number"], row["llm"]) not in done
    ]

    print(f"{len(df)} answers available, {len(done)} already rated by {args.rater}, "
          f"{len(todo)} to go")
    if not todo:
        print("nothing left to rate")
        return

    write_header = not out_path.exists()
    rated = 0

    with open(out_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS)
        if write_header:
            writer.writeheader()

        for row in todo:
            if args.limit is not None and rated >= args.limit:
                print(f"\nreached --limit {args.limit}")
                break

            print("\n" + "=" * 70)
            print(f"skill : {row['preferredLabel']}")
            print(f"model : {row['llm']}   prompt {row['prompt_number']} "
                  f"{row.get('prompt_category', '')} {row.get('prompt_name', '')}".rstrip())
            print(f"question: {row['prompt_text']}")
            print("-" * 70)
            print(row["response_text"])
            print("-" * 70)

            scores = {}
            stop = False
            skipped = False
            for c in CRITERIA:
                s = ask_score(c)
                if s == "q":
                    stop = True
                    break
                if s == "s":
                    skipped = True
                    break
                scores[c] = s

            if stop:
                break
            if skipped:
                continue

            writer.writerow({
                "conceptUri": row["conceptUri"],
                "preferredLabel": row["preferredLabel"],
                "prompt_number": row["prompt_number"],
                "prompt_category": row.get("prompt_category", ""),
                "llm": row["llm"],
                "rater": args.rater,
                **scores,
            })
            f.flush()
            rated += 1

    print(f"\nrated {rated} answers this session, saved to {out_path}")


if __name__ == "__main__":
    main()
