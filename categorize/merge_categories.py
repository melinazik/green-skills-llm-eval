#!/usr/bin/env python3
"""
Merge aggregated thematic labels back into the enhanced ESCO source dataset.

Input 1: categorized CSV (from aggregate.py), expected to include:
  - skill_id
  - final_primary
  - <model>_primary and <model>_conf pairs

Input 2: source enhanced CSV, expected to include:
  - conceptUri

Output: source CSV plus two appended columns:
  - thematicCategory: full taxonomy description line for final_primary
  - thematicScoreBreakdown: per-model vote+confidence string,
    e.g. modelA=G1(0.9);modelB=G2(0.8)

Join mode: left join, source(conceptUri) = categorized(skill_id)
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List

import pandas as pd

from taxonomy import CLASSIFICATION_CARD


def build_category_map() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for line in CLASSIFICATION_CARD.splitlines():
        line = line.strip()
        if not line:
            continue
        code = line.split(" ", 1)[0]
        if code.startswith("G"):
            mapping[code] = line
    return mapping


def model_primary_columns(df: pd.DataFrame) -> List[str]:
    cols = [c for c in df.columns if c.endswith("_primary") and c != "final_primary"]
    return sorted(cols)


def build_score_breakdown(row: pd.Series, primary_cols: List[str]) -> str:
    parts: List[str] = []
    for primary_col in primary_cols:
        model = primary_col[: -len("_primary")]
        conf_col = f"{model}_conf"

        category = str(row.get(primary_col, "")).strip()
        if not category:
            continue

        conf = str(row.get(conf_col, "")).strip()
        conf_display = conf if conf else "NA"
        parts.append(f"{model}={category}({conf_display})")

    return ";".join(parts)


def default_output_path(source_csv: Path) -> Path:
    return source_csv.with_name(f"{source_csv.stem}_with_thematic.csv")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "categorized_csv",
        nargs="?",
        default="categorize/output_categorize/greenSkills_categorised.csv",
        help="Path to greenSkills_categorised.csv",
    )
    parser.add_argument(
        "source_csv",
        nargs="?",
        default="enhance/output_enhance/greenSkillsCollection_enhanced.csv",
        help="Path to greenSkillsCollection_enhanced.csv",
    )
    parser.add_argument(
        "output_csv",
        nargs="?",
        default="categorize/output_categorize/greenSkillsCollection_enhanced_with_thematic.csv",
        help="Optional output path",
    )
    args = parser.parse_args()

    categorized_path = Path(args.categorized_csv)
    source_path = Path(args.source_csv)
    output_path = Path(args.output_csv) if args.output_csv else default_output_path(source_path)

    categorized = pd.read_csv(categorized_path, dtype=str).fillna("")
    source = pd.read_csv(source_path, dtype=str).fillna("")

    required_cat_cols = {"skill_id", "final_primary"}
    missing_cat_cols = required_cat_cols - set(categorized.columns)
    if missing_cat_cols:
        raise ValueError(f"Categorized CSV missing columns: {sorted(missing_cat_cols)}")

    if "conceptUri" not in source.columns:
        raise ValueError("Source CSV missing required column: conceptUri")

    primary_cols = model_primary_columns(categorized)
    category_map = build_category_map()

    categorized = categorized.copy()
    categorized["thematicCategory"] = categorized["final_primary"].map(category_map).fillna("")
    categorized["thematicScoreBreakdown"] = categorized.apply(
        lambda row: build_score_breakdown(row, primary_cols), axis=1
    )

    merge_cols = ["skill_id", "thematicCategory", "thematicScoreBreakdown"]
    merged = source.merge(
        categorized[merge_cols],
        how="left",
        left_on="conceptUri",
        right_on="skill_id",
    )

    if "skill_id" in merged.columns:
        merged = merged.drop(columns=["skill_id"])

    merged["thematicCategory"] = merged["thematicCategory"].fillna("")
    merged["thematicScoreBreakdown"] = merged["thematicScoreBreakdown"].fillna("")

    merged.to_csv(output_path, index=False)

    matched = int((merged["thematicCategory"] != "").sum())
    total = len(merged)
    print(f"Wrote: {output_path}")
    print(f"Rows: {total} | Matched: {matched} | Unmatched: {total - matched}")
    print(
        "Model vote fields used: "
        + (", ".join(primary_cols) if primary_cols else "(none found)")
    )


if __name__ == "__main__":
    main()
