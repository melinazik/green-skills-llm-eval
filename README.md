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

Input (already in `enhance/esco/`, downloaded from the ESCO portal):

- `greenSkillsCollection_en.csv` (the 629 green skills)
- `broaderRelationsSkillPillar_en.csv` (which skill belongs under which category)
- `skillsHierarchy_en.csv` (the category names and codes)

Output: `enhance/output_enhance/greenSkillsCollection_enhanced.csv` (same skills, with category columns added).

Run (from the project root folder):

```bash
python enhance/enhance_green_skills.py
```

## Step 2: categorize

Puts each skill in one thematic group. Several LLMs read the skill and each votes
for a group. The votes are combined into one final group per skill.

The 8 groups come from two known frameworks (HolonIQ and O\*NET):

- G1: Renewable energy and energy systems
- G2: Energy efficiency and green buildings
- G3: Circular economy, waste and resources
- G4: Environmental protection and nature
- G5: Sustainable agriculture, forestry and food
- G6: Sustainable transport and green manufacturing
- G7: Green management, policy and finance
- G8: Green technology, data and digital tools

There is also an "Education" flag for skills that are mainly about teaching. The
exact wording the models read is in `categorize/taxonomy.py`.

Models: this step uses local models through [Ollama](https://ollama.com), so no
API key is needed. Which models to use is set in `categorize/models_config.yaml`.

1. Install Ollama from https://ollama.com/download
2. Pull the models listed in `categorize/models_config.yaml`:

```bash
ollama pull llama3.2:1b
ollama pull gemma3:1b
ollama pull qwen2.5:1.5b
```

While it runs, `categorize.py` keeps a `checkpoint.jsonl` so it can continue if it
stops or crashes. If you re-run it, skills already in the checkpoint are skipped.
To start fresh, delete the checkpoint first:

```bash
rm categorize/output_categorize/checkpoint.jsonl categorize/output_categorize/verbose.log
```

Then run (from the project root folder):

```bash
python categorize/categorize.py   # each model votes a group per skill
python categorize/aggregate.py    # combine the votes into a final group
python categorize/merge_categories.py   # write the final group back onto the skills
```

It reads the enhanced skills from `enhance/output_enhance/` and writes everything
into `categorize/output_categorize/`:

- `predictions_raw.csv` (one row per skill and model)
- `greenSkills_categorised.csv` (one final group per skill)
- `greenSkillsCollection_enhanced_with_thematic.csv` (the enhanced skills with the group added)

## Step 3: select

Picks a small, balanced sample of skills (about 70) to actually test, a few from
each group. This is a small web page you open in the browser.

Start a local web server from the project root folder, then open the page:

```bash
python -m http.server 8000
```

Open `http://localhost:8000/select/skill_selector.html` in the browser. It loads
the categorized skills from Step 2 automatically.

In the page:

1. `Assign category` fills in the group for each skill. Any skill left unassigned
   you can set by hand.
2. `Stratify Select` asks for a number and then picks that many random skills from
   each group.
3. `Export selected rows` downloads a CSV with only the selected skills.

That exported CSV is the input for Step 4.
