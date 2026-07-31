"""Statistics for Step 5 (evaluation). DRAFT, extends as the rubric is finalized.

Reads every evaluate/output_evaluate/ratings_*.csv and reports:
  - mean and standard deviation per model and per criterion
  - mean per prompt category (C1 to C4), which is why Step 4 asks two prompts
    per category instead of one
  - inter-rater agreement (exact-match percent and Cohen's kappa) when there are
    two or more raters
  - for the LLM judges (evaluate/judge.py): self-preference bias, and how far
    each judge sits from the human raters

    python evaluate/stats.py
    python evaluate/stats.py --humans-only    # ignore the LLM judges

Human raters and LLM judges are kept apart everywhere, because an LLM judge is
evidence about the judge as much as about the answer. A rater whose name starts
with "llm:" is a judge.

Deeper tests (t-test or ANOVA between models) are left as a next step once the
rubric and the number of raters are fixed.
"""

import argparse
from itertools import combinations
from pathlib import Path

import pandas as pd

from rubric import CRITERIA

RATINGS_DIR = Path("evaluate/output_evaluate")

# one rating is one (skill, prompt, model) cell, keyed the same way as Step 4
KEY_COLS = ["conceptUri", "prompt_number", "llm"]

JUDGE_PREFIX = "llm:"


def is_judge(rater) -> bool:
    return str(rater).startswith(JUDGE_PREFIX)


def load_all_ratings():
    files = sorted(RATINGS_DIR.glob("ratings_*.csv"))
    if not files:
        raise SystemExit(
            f"no ratings found in {RATINGS_DIR}\n"
            "Rate some answers first, either with evaluate/rate.py or in the\n"
            "Rate answers tab of viewer/index.html."
        )

    frames = []
    for f in files:
        df = pd.read_csv(f)
        missing = [c for c in KEY_COLS + ["rater"] + CRITERIA if c not in df.columns]
        if missing:
            raise SystemExit(f"{f} is missing the columns {missing}")
        frames.append(df)

    print(f"read {len(files)} ratings file(s): {', '.join(f.name for f in files)}")
    return pd.concat(frames, ignore_index=True)


def cohens_kappa(a, b):
    """Cohen's kappa for two equal-length label sequences (nominal)."""
    pairs = list(zip(a, b))
    n = len(pairs)
    if n == 0:
        return None

    labels = sorted(set(a) | set(b))
    observed = sum(1 for x, y in pairs if x == y) / n
    expected = sum((sum(1 for x, _ in pairs if x == k) / n) *
                   (sum(1 for _, y in pairs if y == k) / n) for k in labels)
    if expected == 1:
        return 1.0
    return (observed - expected) / (1 - expected)


def report_scores(df, title):
    """The per-model, per-category and per-prompt tables for one set of raters."""
    print(f"\n########## {title} ##########")
    print(f"{len(df)} ratings from {df['rater'].nunique()} rater(s): "
          f"{sorted(df['rater'].unique())}")

    print("\n=== Mean (SD) per model and criterion ===")
    for model, g in df.groupby("llm"):
        parts = [f"{c}={g[c].mean():.2f} (sd {g[c].std():.2f})" for c in CRITERIA]
        print(f"  {model}: " + ", ".join(parts))

    print("\n=== Overall mean per model (all criteria) ===")
    for model, g in df.groupby("llm"):
        print(f"  {model}: {g['overall'].mean():.2f}  (n={len(g)})")

    if "prompt_category" in df.columns and df["prompt_category"].notna().any():
        print("\n=== Overall mean per prompt category ===")
        for cat, g in df.groupby("prompt_category"):
            per_model = ", ".join(
                f"{m}={gm['overall'].mean():.2f}" for m, gm in g.groupby("llm")
            )
            print(f"  {cat}: {g['overall'].mean():.2f}  ({per_model})")

    print("\n=== Overall mean per prompt ===")
    for pnum, g in df.groupby("prompt_number"):
        print(f"  P{pnum}: {g['overall'].mean():.2f}  (n={len(g)})")


def report_agreement(df, title):
    """Exact match and Cohen's kappa for every pair of raters in df."""
    raters = sorted(df["rater"].unique())
    if len(raters) < 2:
        print(f"\n({title}: needs at least two raters, found {len(raters)})")
        return

    print(f"\n=== {title} ===")
    for r1, r2 in combinations(raters, 2):
        d1 = df[df["rater"] == r1].set_index(KEY_COLS)
        d2 = df[df["rater"] == r2].set_index(KEY_COLS)
        common = d1.index.intersection(d2.index)
        if len(common) == 0:
            print(f"  {r1} vs {r2}: no shared answers")
            continue

        print(f"  {r1} vs {r2} ({len(common)} shared answers):")
        for c in CRITERIA:
            a = d1.loc[common, c].tolist()
            b = d2.loc[common, c].tolist()
            exact = sum(1 for x, y in zip(a, b) if x == y) / len(common) * 100
            k = cohens_kappa(a, b)
            print(f"    {c}: exact {exact:.0f}%, kappa {k:.2f}")


def report_discrimination(df, title):
    """Does a rater actually distinguish between answers, or repeat one value?

    A judge that returns the same score to everything produces means and agreement
    figures that look ordinary while carrying no information. It has happened here:
    an early judge prompt contained a filled-in JSON example and the models copied
    its numbers. This check is what makes that visible without reading the CSVs.
    """
    print(f"\n=== {title} ===")
    print("  (distinct values used, and how often the most common one is repeated)")

    flagged = []
    for rater, g in df.groupby("rater"):
        parts = []
        for c in CRITERIA:
            counts = g[c].value_counts()
            share = counts.iloc[0] / len(g) * 100
            parts.append(f"{c}={g[c].nunique()}v/{share:.0f}%")
            if share >= 90:
                flagged.append((rater, c, int(counts.index[0]), share))
        print(f"  {rater}: " + "  ".join(parts))

    if flagged:
        print("\n  WARNING: these criteria are effectively constant, so their means and")
        print("  their agreement figures carry no information:")
        for rater, c, value, share in flagged:
            print(f"    {rater} gave {c}={value} to {share:.0f}% of the answers")
        print("  Treat those columns as missing data, not as evidence.")


def report_self_preference(judged):
    """How much higher a judge scores its own answers than other judges score them.

    Both halves are measured on the same answers, so the gap is not explained by
    those answers simply being better.
    """
    if "self_judged" not in judged.columns:
        return

    print("\n=== Self-preference bias of the LLM judges ===")
    print("  (own score minus what the other judges gave the same answers)")

    any_row = False
    for judge, g in judged.groupby("rater"):
        author = str(judge)[len(JUDGE_PREFIX):]
        own = judged[(judged["rater"] == judge) & (judged["llm"] == author)]
        if own.empty:
            continue

        others = judged[(judged["rater"] != judge) & (judged["llm"] == author)]
        others = others.set_index(KEY_COLS)
        own_idx = own.set_index(KEY_COLS)
        common = own_idx.index.intersection(others.index)
        if len(common) == 0:
            print(f"  {judge}: no other judge scored the same answers")
            continue

        mine = own_idx.loc[common, "overall"].groupby(level=[0, 1, 2]).mean()
        theirs = others.loc[common, "overall"].groupby(level=[0, 1, 2]).mean()
        gap = (mine - theirs).mean()
        any_row = True
        print(f"  {judge} on its own answers: {mine.mean():.2f} vs {theirs.mean():.2f} "
              f"from the others  ->  {gap:+.2f}  (n={len(mine)})")

    if not any_row:
        print("  not enough overlap to measure it yet")


def report_judge_vs_human(human, judged):
    """Does a judge rank the models the way the people do?"""
    if human.empty or judged.empty:
        return

    print("\n=== LLM judges against the human raters ===")
    human_rank = human.groupby("llm")["overall"].mean().sort_values(ascending=False)
    print("  humans rank the models: " +
          " > ".join(f"{m} ({v:.2f})" for m, v in human_rank.items()))

    for judge, g in judged.groupby("rater"):
        rank = g.groupby("llm")["overall"].mean().sort_values(ascending=False)
        same = list(rank.index) == list(human_rank.index)
        shift = rank.mean() - human_rank.mean()
        print(f"  {judge}: " + " > ".join(f"{m} ({v:.2f})" for m, v in rank.items()) +
              f"   [{'same order' if same else 'DIFFERENT order'}, "
              f"scores {shift:+.2f} vs humans]")


def main():
    parser = argparse.ArgumentParser(description="Report the Step 5 ratings")
    parser.add_argument("--humans-only", action="store_true",
                        help="ignore the LLM judges")
    args = parser.parse_args()

    df = load_all_ratings()
    df = df.drop_duplicates(subset=KEY_COLS + ["rater"], keep="last")
    df["overall"] = df[CRITERIA].mean(axis=1)

    print(f"{len(df)} ratings over {df['conceptUri'].nunique()} skills, "
          f"{df['prompt_number'].nunique()} prompts, {df['llm'].nunique()} models")

    judged = df[df["rater"].map(is_judge)]
    human = df[~df["rater"].map(is_judge)]

    if args.humans_only:
        judged = judged.iloc[0:0]

    if not human.empty:
        report_scores(human, "HUMAN RATERS")
        report_discrimination(human, "Do the human raters discriminate?")
        report_agreement(human, "Inter-rater agreement, humans (per criterion)")
    else:
        print("\n(no human ratings yet)")

    if not judged.empty:
        report_scores(judged, "LLM JUDGES")
        report_discrimination(judged, "Do the judges discriminate?")
        report_agreement(judged, "Agreement between the judges (per criterion)")
        report_self_preference(judged)

        if not human.empty:
            both = pd.concat([human, judged], ignore_index=True)
            report_agreement(both, "Agreement between every rater, humans and judges")
            report_judge_vs_human(human, judged)
        else:
            print("\n(no human ratings yet, so the judges cannot be checked against people)")


if __name__ == "__main__":
    main()
