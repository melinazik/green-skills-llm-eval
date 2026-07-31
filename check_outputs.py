"""Quick sanity checks on the outputs of each step.

Run it any time after running some steps to confirm the files exist and look right:

    python check_outputs.py

It only reads files, it never changes anything. Each check prints OK, WARN, or MISSING.
"""

from pathlib import Path

import pandas as pd

ENHANCED = Path("enhance/output_enhance/greenSkillsCollection_enhanced.csv")
PREDICTIONS = Path("categorize/output_categorize/predictions_raw.csv")
CATEGORISED = Path("categorize/output_categorize/greenSkills_categorised.csv")
SELECTED = Path("select/output_select/selected_skills.csv")
RESPONSES = Path("prompt/output_prompt/responses.csv")


def check(label, path, fn):
    if not path.exists():
        print(f"[MISSING] {label}: {path}")
        return
    try:
        fn(pd.read_csv(path))
    except Exception as e:
        print(f"[WARN] {label}: {e}")


def main():
    check("Step 1 enhance", ENHANCED, lambda df: print(
        f"[OK] Step 1 enhance: {len(df)} skills, "
        f"{'has category columns' if any('Level' in c or 'category' in c.lower() for c in df.columns) else 'no category columns?'}"
    ))

    check("Step 2 predictions", PREDICTIONS, lambda df: print(
        f"[OK] Step 2 predictions: {len(df)} rows, "
        f"{df['model'].nunique() if 'model' in df else '?'} models, "
        f"{df['skill_id'].nunique() if 'skill_id' in df else '?'} skills"
    ))

    check("Step 2 categorised", CATEGORISED, lambda df: print(
        f"[OK] Step 2 categorised: {len(df)} skills, "
        f"final groups: {sorted(df['final_primary'].dropna().unique()) if 'final_primary' in df else '?'}"
    ))

    check("Step 3 selected", SELECTED, lambda df: print(
        f"[OK] Step 3 selected: {len(df)} skills chosen"
    ))

    def check_responses(df):
        errors = (df["error"].fillna("") != "").sum() if "error" in df else "?"
        empty = (df["response_text"].fillna("") == "").sum() if "response_text" in df else "?"
        models = df["llm"].nunique() if "llm" in df else "?"
        skills = df["conceptUri"].nunique() if "conceptUri" in df else "?"
        prompts = df["prompt_number"].nunique() if "prompt_number" in df else "?"
        cut = (df["finish_reason"].fillna("") == "length").sum() if "finish_reason" in df else "?"
        print(f"[OK] Step 4 responses: {len(df)} rows, {skills} skills, {prompts} prompts, "
              f"{models} models, {errors} errors, {empty} empty answers, {cut} cut off")

    check("Step 4 responses", RESPONSES, check_responses)


if __name__ == "__main__":
    main()
