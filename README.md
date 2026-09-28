# Evaluating LLMs as Teachers of Green Skills

This project measures how well Large Language Models
(LLMs) can teach "green skills",
the sustainability skills from the EU ESCO database.

Each green skill is enriched with its ESCO category, classified into a thematic
group, a representative sample is selected, and every LLM is then prompted to
teach it. The responses are collected for evaluation.

## Pipeline overview

| Step | Folder        | What it does                                                   |
| ---- | ------------- | -------------------------------------------------------------- |
| 1    | `enhance/`    | Add the ESCO category of each green skill                      |
| 2    | `categorize/` | Put each skill in a group (G1 to G8) by multi-LLM voting       |
| 3    | `select/`     | Pick a balanced sample of skills (browser tool)                |
| 4    | `prompt/`     | Ask each LLM the 8 fixed questions per skill, save the answers |

The two browser tools live together in `viewer/`, as one page with a tab per step:
**Select skills** (Step 3) and **Review answers** (Step 4). Serve the project root
and open `http://localhost:8010/viewer/`.

Notes :

- The green skills come from the ESCO dataset (629 skills flagged as green in this
  version).
- Runs are reproducible: temperature is 0 and the seed is fixed in Steps 2 and 4.
- Every data-collecting step is resumable and appends as it goes, so a crash never
  loses collected work.

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
each group.

Start a local web server from the project root folder, then open the page:

```bash
python -m http.server 8010
```

Open `http://localhost:8010/viewer/` in the browser and stay on the
**Select skills** tab. It loads the categorized skills from Step 2 automatically.

The server has to start from the project root, not from `viewer/`, because the page
reads the CSVs of the other steps through relative paths such as
`../categorize/output_categorize/`.

In the page:

1. `Assign category` fills in the group for each skill. Any skill left unassigned
   you can set by hand.
2. `Stratify Select` asks for a number and then picks that many random skills from
   each group.
3. `Export selected rows` downloads a CSV with only the selected skills.

Save that downloaded CSV as `select/output_select/selected_skills.csv`. That is the
input for Step 4.

## Step 4: prompt

Asks each LLM the same 12 fixed questions about each skill and saves the answers.
The questions never change. Only the skill name changes.

The 12 prompts are 6 pedagogical categories with 2 prompts each, so that answers can
also be compared per category and not only per single question:

| Code | Category   | Bloom level | Prompts                    |
| ---- | ---------- | ----------- | -------------------------- |
| C1   | Remember   | Remember    | remember_1, remember_2     |
| C2   | Understand | Understand  | understand_1, understand_2 |
| C3   | Apply      | Apply       | apply_1, apply_2           |
| C4   | Analyze    | Analyze     | analyze_1, analyze_2       |
| C5   | Evaluate   | Evaluate    | evaluate_1, evaluate_2     |
| C6   | Create     | Create      | create_1, create_2         |

The levels follow the revised Bloom taxonomy (Anderson & Krathwohl, 2001). The system
prompt is empty and the same for all 12, so the user prompt is the only thing that
changes between conditions.

The prompts are given ready in `prompt/prompts.csv`, which is the authoritative
record of what was asked. To change them, edit that file directly.

Input: `select/output_select/selected_skills.csv` (from Step 3). If that file is
missing, Step 4 stops with a message telling you to run Step 3 first.
Output: `prompt/output_prompt/responses.csv`, one row per skill, prompt and model:
all the skill columns, the prompt metadata (category, Bloom level), the answer text
and the call metadata (time, tokens, cost, `finish_reason`).

Settings live in `prompt/prompt_config.yaml`: the paths, the models, temperature and
seed. By default it uses the same local Ollama models as Step 2, so no API key is
needed. To use a hosted model, add its id there and set its API key (for example
`GEMINI_API_KEY`).

Run (from the project root folder):

```bash
python prompt/generate_responses.py --dry-run   # show rendered prompts, no calls
python prompt/generate_responses.py --mock      # fake answers, to test the plumbing
python prompt/generate_responses.py --limit 5   # first 5 skills only, to test
python prompt/generate_responses.py             # full run
```

It appends each answer to the CSV immediately and is resumable: if it stops, run it
again and it skips the answers already collected (tracked in
`prompt/output_prompt/checkpoint.jsonl`). At the end it rewrites the CSV from the
checkpoint, which also removes duplicates. `--rebuild-csv` does only that rewrite,
without calling any model. A `verbose.log` next to the output records every call
for debugging.

There is no `max_tokens` limit, because the C4 prompts ask for long answers. If a
model stops early anyway, the `finish_reason` column says `length` and the run
prints a warning with how many answers were cut off.

To read the answers, open `http://localhost:8010/viewer/#responses` (the same server
as Step 3, started from the project root) and go to the **Review answers** tab. It
groups the answers by skill and lets you compare prompts and models side by side.

## Helper scripts

```bash
python run_all.py         # run steps 1, 2 and 4 in order, then the checks (step 3 is manual)
python check_outputs.py   # read-only sanity checks on each step's output
```

`run_all.py` runs the whole pipeline.
It runs Step 1 (enhance), Step 2 (categorize, aggregate, merge) and Step 4 (prompt),
one after another, and stops if any step fails. When they finish it runs
`check_outputs.py`.

Preparation:

- Ollama must be running with the models from `categorize/models_config.yaml` and
  `prompt/prompt_config.yaml`.
- Step 3 is a manual choice in the browser, so it is not included. Once step 3 is
  completed, save `select/output_select/selected_skills.csv`, otherwise Step 4 stops.

It is safe to stop and re-run: every step is resumable and continues where it left
off. On a normal computer the full run is slow (the models run on the CPU), so expect
it to take a while.

### Test runs on a small volume

A full run takes hours, so try the pipeline on a few skills first. Two flags do that,
and both are passed on to the two steps that call the models (Step 2a and Step 4b);
the other steps are fast and ignore them.

```bash
python run_all.py --limit 2 --mock   # no model is called at all, seconds
python run_all.py --limit 2          # real models, 2 skills
python run_all.py --limit 5          # real models, 5 skills
```

- `--mock` replaces every answer with a synthetic one. Nothing is sent to a model, so
  it checks the plumbing only: paths, prompt rendering, resuming, the output columns.
- `--limit N` caps the number of skills. Rough cost: about 20 seconds per call on the
  CPU, and Step 4 makes 12 prompts x 3 models = 36 calls per skill, so 2 skills take
  roughly 25 minutes and 5 skills roughly 60.

The same flags exist on the individual scripts, which is handier when only one step
needs testing:

```bash
python categorize/categorize.py --limit 5 --mock
python prompt/generate_responses.py --limit 5 --mock
python prompt/generate_responses.py --dry-run   # print the rendered prompts, call nothing
```

Two things to keep in mind:

- Mock answers are written to the same checkpoint as real ones, so delete
  `prompt/output_prompt/` and `categorize/output_categorize/checkpoint.jsonl` before
  the real run, otherwise those cells count as already done and are skipped.
- Because every step is resumable, `--limit N` caps the skills of *this* run, not the
  total in the output file. Running `--limit 2` on top of 18 skills already collected
  leaves you with 20, not 2.
