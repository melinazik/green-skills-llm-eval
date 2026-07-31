#!/usr/bin/env python3
"""
Build prompt/prompts.csv, the prompt set used by Step 4 (generate_responses.py).

Design: 4 pedagogical categories x 2 prompts each = 8 prompts.
Categories map to the revised Bloom taxonomy (Anderson & Krathwohl, 2001);
two prompts per category allow category-level aggregation (the supervisor's
request) with better reliability than a single item.

Placeholders use str.format syntax against columns of the skills CSV.
System prompt is intentionally EMPTY and constant across all prompts, so that
the user prompt is the sole manipulation.

Usage (from the project root folder):
    python prompt/build_prompts.py
"""
import csv
from pathlib import Path

OUTPUT_CSV = Path(__file__).resolve().parent / "prompts.csv"

ROWS = [
    # ---- C1 Conceptual Explanation (Bloom: Understand) — varies AUDIENCE ----
    dict(prompt_number=1, prompt_name="explain_beginner",
         prompt_category="C1", category_name="Conceptual Explanation",
         bloom_level="Understand", variation="audience: novice",
         source="supervisor #2",
         system_prompt="",
         prompt_text="Explain the concept of {preferredLabel} to a beginner with no prior background in sustainability."),

    dict(prompt_number=2, prompt_name="explain_technical",
         prompt_category="C1", category_name="Conceptual Explanation",
         bloom_level="Understand", variation="audience: technical student",
         source="supervisor #6",
         system_prompt="",
         prompt_text="Explain {preferredLabel} to a university student in a technical field such as engineering or computer science."),

    # ---- C2 Practical Application (Bloom: Apply) — varies CONTEXT ----
    dict(prompt_number=3, prompt_name="apply_organisational",
         prompt_category="C2", category_name="Practical Application",
         bloom_level="Apply", variation="context: organisational/industrial",
         source="supervisor #3",
         system_prompt="",
         prompt_text="Provide a practical example of how {preferredLabel} can be applied in a real-world organizational or industrial context."),

    dict(prompt_number=4, prompt_name="apply_professional",
         prompt_category="C2", category_name="Practical Application",
         bloom_level="Apply", variation="context: professional practice",
         source="thesis plan #2",
         system_prompt="",
         prompt_text="How can professionals apply {preferredLabel} to promote environmental sustainability in their day-to-day work?"),

    # ---- C3 Sustainability Rationale (Bloom: Analyse/Evaluate) — varies STANCE ----
    dict(prompt_number=5, prompt_name="rationale_contribution",
         prompt_category="C3", category_name="Sustainability Rationale",
         bloom_level="Analyse", variation="stance: contribution",
         source="supervisor #4",
         system_prompt="",
         prompt_text="How does {preferredLabel} contribute to environmental sustainability and long-term resource efficiency?"),

    dict(prompt_number=6, prompt_name="rationale_tradeoffs",
         prompt_category="C3", category_name="Sustainability Rationale",
         bloom_level="Evaluate", variation="stance: benefits and limitations",
         source="new (balances C3)",
         system_prompt="",
         prompt_text="What are the main benefits and limitations of {preferredLabel} as a means of reducing environmental impact?"),

    # ---- C4 Instructional Design (Bloom: Create) — varies FORM ----
    dict(prompt_number=7, prompt_name="design_learning_path",
         prompt_category="C4", category_name="Instructional Design",
         bloom_level="Create", variation="form: learning path",
         source="supervisor #5",
         system_prompt="",
         prompt_text="Suggest a step-by-step learning path for someone who wants to acquire skills related to {preferredLabel}."),

    dict(prompt_number=8, prompt_name="design_teaching_scenario",
         prompt_category="C4", category_name="Instructional Design",
         bloom_level="Create", variation="form: teaching scenario",
         source="thesis plan #3",
         system_prompt="",
         prompt_text="Imagine you are teaching a beginner about {preferredLabel}. Give a simple real-world example and explain how you would check that they have understood it."),
]

FIELDS = ["prompt_number", "prompt_name", "prompt_category", "category_name",
          "bloom_level", "variation", "source", "system_prompt", "prompt_text"]

with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=FIELDS, quoting=csv.QUOTE_ALL)
    w.writeheader()
    for r in ROWS:
        w.writerow(r)

print(f"wrote {OUTPUT_CSV} — {len(ROWS)} prompts in "
      f"{len({r['prompt_category'] for r in ROWS})} categories")
for r in ROWS:
    print(f"  P{r['prompt_number']} [{r['prompt_category']}/{r['bloom_level']:10s}] {r['prompt_name']}")
