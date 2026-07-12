# Evaluating LLMs as Teachers of Green Skills

This project measures how well Large Language Models
(LLMs) can teach "green skills",
the sustainability skills from the EU ESCO database.

Each green skill is enriched with its ESCO category, classified into a thematic
group, a representative sample is selected, and every LLM is then prompted to
teach it. The responses are collected for evaluation.

## Setup

```bash
pip install -r requirements.txt
```

## Step 1: enhance

Adds the ESCO category of each green skill.

The ESCO green skills list only has links to each skill's category, not the
category name. This step follows those links and writes the category names into
new columns.

Input (already in `data/esco/`, downloaded from the ESCO portal):

- `greenSkillsCollection_en.csv` (the 629 green skills)
- `broaderRelationsSkillPillar_en.csv` (which skill belongs under which category)
- `skillsHierarchy_en.csv` (the category names and codes)

Output: `data/greenSkillsCollection_enhanced.csv` (same skills, with category columns added).

Run:

```bash
cd enhance
python enhance_green_skills.py
```

How to check it worked: the script prints `Saved 629 rows to ...`, and the file
`data/greenSkillsCollection_enhanced.csv` is created. Open it and confirm it has
the new category columns filled in.
