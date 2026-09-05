#!/usr/bin/env python3
"""
Phase C - aggregate per-model predictions into one final label per skill.

Reads predictions_raw.csv (long format, one row per skill x model), applies
majority voting with deterministic tie-breaks, computes inter-model agreement,
and writes:
  - greenSkills_categorised.csv (wide format)
  - agreement_report.md
"""

from __future__ import annotations

import argparse
import math
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from taxonomy import PrimaryCategory, SUBSTANTIVE


CATEGORIES: List[str] = [c.value for c in PrimaryCategory]


def category_sort_key(category: str) -> int:
    if category in CATEGORIES:
        return CATEGORIES.index(category)
    return len(CATEGORIES) + 1


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def parse_confidence(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        return float(value)
    except Exception:
        return None


def vote_primary(votes: Sequence[Tuple[str, Optional[float]]]) -> Tuple[str, str]:
    """
    Return (final_primary, agreement_level).

    Tie-break sequence (spec):
      1) most common category
      2) if tied, prefer SUBSTANTIVE
      3) if still tied, highest confidence among tied categories
      4) final deterministic fallback: lowest category code
    """
    categories = [cat for cat, _ in votes if cat in CATEGORIES]
    if not categories:
        raise ValueError("No valid categories in votes")

    counts = Counter(categories)
    max_votes = max(counts.values())
    top = [cat for cat, n in counts.items() if n == max_votes]

    if len(top) == 1:
        winner = top[0]
        n_votes = len(categories)
        if max_votes == n_votes:
            level = "unanimous"
        elif max_votes > n_votes / 2:
            level = "majority"
        else:
            level = "plurality"
        return winner, level

    # Any top-count tie is classified as tie_resolved.
    level = "tie_resolved"

    substantive_top = [cat for cat in top if cat in SUBSTANTIVE]
    pool = substantive_top if substantive_top else top
    if len(pool) == 1:
        return pool[0], level

    best_conf = float("-inf")
    best_cats: List[str] = []
    for cat in pool:
        confs = [conf for vote_cat, conf in votes if vote_cat == cat and conf is not None]
        if not confs:
            continue
        cat_best = max(confs)
        if cat_best > best_conf:
            best_conf = cat_best
            best_cats = [cat]
        elif cat_best == best_conf:
            best_cats.append(cat)

    if len(best_cats) == 1:
        return best_cats[0], level

    fallback_pool = best_cats if best_cats else pool
    return sorted(fallback_pool, key=category_sort_key)[0], level


def fleiss_kappa(count_matrix: Sequence[Sequence[int]]) -> float:
    """Compute Fleiss' kappa for an item x category count matrix."""
    if not count_matrix:
        return float("nan")

    m = pd.DataFrame(count_matrix, columns=CATEGORIES, dtype=float)
    n_items = len(m)

    row_sums = m.sum(axis=1)
    if n_items == 0 or row_sums.empty:
        return float("nan")

    n_raters = row_sums.iloc[0]
    if n_raters < 2 or not (row_sums == n_raters).all():
        return float("nan")

    p_j = m.sum(axis=0) / (n_items * n_raters)
    p_i = ((m.pow(2).sum(axis=1) - n_raters) / (n_raters * (n_raters - 1))).mean()
    p_e = (p_j.pow(2)).sum()

    denom = 1 - p_e
    if denom == 0:
        return float("nan")
    return float((p_i - p_e) / denom)


def kappa_band(kappa: float) -> str:
    if math.isnan(kappa):
        return "not defined"
    if kappa <= 0.20:
        return "slight"
    if kappa <= 0.40:
        return "fair"
    if kappa <= 0.60:
        return "moderate"
    if kappa <= 0.80:
        return "substantial"
    return "almost perfect"


def aggregate_predictions(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    required = {"skill_id", "label_text", "model", "primary_category"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    models = sorted(df["model"].dropna().astype(str).unique().tolist())
    expected_raters = len(models)

    rows: List[Dict[str, Any]] = []
    fleiss_rows: List[List[int]] = []
    tie_resolved_cases: List[Dict[str, Any]] = []

    for skill_id, group in df.groupby("skill_id", sort=True):
        row: Dict[str, Any] = {
            "skill_id": skill_id,
            "label_text": group["label_text"].iloc[0],
        }

        votes: List[Tuple[str, Optional[float]]] = []
        overlays: List[bool] = []

        # Pre-create per-model columns so output is consistently wide.
        for model in models:
            row[f"{model}_primary"] = None
            row[f"{model}_overlay"] = None
            row[f"{model}_conf"] = None

        for model in models:
            model_rows = group[group["model"] == model]
            if model_rows.empty:
                continue

            rec = model_rows.iloc[-1]
            cat = str(rec.get("primary_category", ""))
            conf = parse_confidence(rec.get("confidence"))
            overlay = parse_bool(rec.get("education_overlay"))

            row[f"{model}_primary"] = cat
            row[f"{model}_overlay"] = overlay
            row[f"{model}_conf"] = conf

            if cat in CATEGORIES:
                votes.append((cat, conf))
                overlays.append(overlay)

        if not votes:
            continue

        final_primary, agreement_level = vote_primary(votes)
        final_overlay = sum(overlays) > (len(overlays) / 2)

        row["final_primary"] = final_primary
        row["final_overlay"] = final_overlay
        row["agreement_level"] = agreement_level
        row["n_models"] = len(votes)
        rows.append(row)

        if agreement_level == "tie_resolved":
            tie_resolved_cases.append(
                {
                    "skill_id": skill_id,
                    "label_text": row["label_text"],
                    "final_primary": final_primary,
                    "votes": [
                        {
                            "category": cat,
                            "confidence": conf,
                        }
                        for cat, conf in votes
                    ],
                }
            )

        if len(votes) == expected_raters:
            fleiss_rows.append([sum(1 for cat, _ in votes if cat == c) for c in CATEGORIES])

    out = pd.DataFrame(rows)
    if out.empty:
        metrics = {
            "models": models,
            "expected_raters": expected_raters,
            "kappa": float("nan"),
            "kappa_items": 0,
            "agreement_counts": {},
            "final_distribution": {c: 0 for c in CATEGORIES},
            "pairwise": [],
            "tie_resolved_cases": [],
        }
        return out, metrics

    agreement_counts = out["agreement_level"].value_counts().to_dict()
    final_distribution = (
        out["final_primary"].value_counts().reindex(CATEGORIES).fillna(0).astype(int).to_dict()
    )

    pairwise: List[Dict[str, Any]] = []
    for a, b in combinations(models, 2):
        col_a = f"{a}_primary"
        col_b = f"{b}_primary"
        subset = out[[col_a, col_b]].dropna()
        overlap = len(subset)
        agreement = float((subset[col_a] == subset[col_b]).mean()) if overlap else float("nan")
        pairwise.append(
            {
                "model_a": a,
                "model_b": b,
                "overlap": overlap,
                "agreement": agreement,
            }
        )

    metrics = {
        "models": models,
        "expected_raters": expected_raters,
        "kappa": fleiss_kappa(fleiss_rows),
        "kappa_items": len(fleiss_rows),
        "agreement_counts": agreement_counts,
        "final_distribution": final_distribution,
        "pairwise": pairwise,
        "tie_resolved_cases": tie_resolved_cases,
    }
    return out, metrics


def write_agreement_report(path: Path, out: pd.DataFrame, metrics: Dict[str, Any]) -> None:
    n = len(out)
    kappa = metrics["kappa"]
    kappa_items = metrics["kappa_items"]
    models = metrics["models"]
    agreement_counts = metrics["agreement_counts"]

    lines: List[str] = []
    lines.append("# Phase C - Inter-Model Agreement Report")
    lines.append("")
    lines.append(f"- Skills categorised: **{n}**")
    lines.append(f"- Models: {', '.join(models) if models else '(none)'}")
    lines.append(
        f"- Fleiss' κ (primary category): **{kappa:.3f}** ({kappa_band(kappa)})"
        if not math.isnan(kappa)
        else "- Fleiss' κ (primary category): **NaN** (not defined)"
    )
    lines.append(
        "- Fleiss' κ is a chance-corrected measure of inter-model agreement "
        "on primary-category labels (1.0 = perfect agreement, 0.0 = chance-level)."
    )
    lines.append(
        f"- Fleiss' κ computed on complete cases only: **{kappa_items}** skills "
        f"(requires all {metrics['expected_raters']} models present per skill)"
    )
    lines.append("")

    lines.append("## Agreement level breakdown")
    lines.append("")
    for level in ["unanimous", "majority", "plurality", "tie_resolved"]:
        count = int(agreement_counts.get(level, 0))
        pct = (count / n) if n else 0.0
        lines.append(f"- {level}: {count} ({pct:.1%})")
    lines.append("")

    lines.append("## Pairwise raw agreement")
    lines.append("")
    pairwise_df = pd.DataFrame(metrics["pairwise"])
    if pairwise_df.empty:
        lines.append("No model pairs available.")
    else:
        pairwise_df["agreement"] = pairwise_df["agreement"].map(
            lambda x: f"{x:.1%}" if pd.notna(x) else "NaN"
        )
        pairwise_df = pairwise_df[["model_a", "model_b", "overlap", "agreement"]]
        lines.append(pairwise_df.to_markdown(index=False))
    lines.append("")

    lines.append("## Final category distribution")
    lines.append("")
    dist_df = pd.DataFrame(
        {"category": CATEGORIES, "count": [metrics["final_distribution"].get(c, 0) for c in CATEGORIES]}
    )
    lines.append(dist_df.to_markdown(index=False))
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="categorize/output_categorize/predictions_raw.csv", help="Long-format predictions CSV")
    parser.add_argument(
        "--output",
        default="categorize/output_categorize/greenSkills_categorised.csv",
        help="Wide-format aggregated CSV",
    )
    parser.add_argument(
        "--report",
        default="categorize/output_categorize/agreement_report.md",
        help="Agreement report markdown path",
    )
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    report_path = Path(args.report)

    df = pd.read_csv(in_path, dtype=str)
    out, metrics = aggregate_predictions(df)

    out.to_csv(out_path, index=False)
    write_agreement_report(report_path, out, metrics)

    print(f"Skills categorised: {len(out)}")
    print(f"Models: {', '.join(metrics['models']) if metrics['models'] else '(none)'}")
    if math.isnan(metrics["kappa"]):
        print("Fleiss' kappa: NaN")
    else:
        print(f"Fleiss' kappa: {metrics['kappa']:.3f} ({kappa_band(metrics['kappa'])})")

    tie_cases = metrics.get("tie_resolved_cases", [])
    print(f"Tie-resolved cases: {len(tie_cases)}")
    for case in tie_cases:
        votes_str = ", ".join(
            f"{v['category']} ({v['confidence'] if v['confidence'] is not None else 'NA'})"
            for v in case["votes"]
        )
        print(
            f"  - {case['skill_id']} | final={case['final_primary']} | "
            f"label={case['label_text']} | votes=[{votes_str}]"
        )

    print(f"Wrote {out_path} and {report_path}")


if __name__ == "__main__":
    main()
