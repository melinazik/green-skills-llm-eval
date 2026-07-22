"""LLM prompting pipeline for ESCO green skills - v04.

Improvements over N_prompt_v_02/03:
- long format output: one row per (skill x prompt x model), so multiple
  models never overwrite each other and the data is analysis-ready
- API keys come from environment variables only (never hardcoded)
- saves response metadata required by the plan (tokens, response time)
- appends each result to the CSV immediately, so a crash loses nothing
- resumable: already-collected (skill, prompt, model) combos are skipped
- retries with backoff on transient API errors

Usage:
    export GEMINI_API_KEY=...        # and/or OPENAI_API_KEY, DEEPSEEK_API_KEY
    python prompt_pipeline.py --pilot          # first 10 skills only
    python prompt_pipeline.py                  # full run
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from litellm import completion

# ---------------------------------------------------------------- config

DATA_FILE = Path("select/output_select/selected_skills.csv")
OUTPUT_FILE = Path("prompt/output_prompt/responses.csv")

# the 5 prompt templates from the thesis instructions (LLM_Zikou.docx).
# do NOT change wording between skills - only [GREEN_SKILL] is replaced.
PROMPTS = [
    "Explain the concept of [GREEN_SKILL] to a beginner with no prior background in sustainability.",
    "Provide a practical example of how [GREEN_SKILL] can be applied in a real-world organizational or industrial context.",
    "How does [GREEN_SKILL] contribute to environmental sustainability and long-term resource efficiency?",
    "Suggest a step-by-step learning path for someone who wants to acquire skills related to [GREEN_SKILL].",
    "Explain [GREEN_SKILL] for a university student in a technical field (e.g., engineering or computer science).",
]

# litellm model identifiers. these run locally through Ollama (no API key).
# to use a hosted model instead, add its id here and set its API key env var,
# for example "gemini/gemini-2.5-flash" with GEMINI_API_KEY.
MODELS = [
    "ollama_chat/llama3.2:1b",
    "ollama_chat/gemma3:1b",
    "ollama_chat/qwen2.5:1.5b",
]

FIELDNAMES = [
    "skill_name", "prompt_number", "model", "prompt_text",
    "response_text", "timestamp", "response_time_s",
    "prompt_tokens", "completion_tokens", "total_tokens", "error",
]

MAX_RETRIES = 3

# ---------------------------------------------------------------- helpers


def ask_llm(model, skill_name, prompt_number, prompt_template):
    """Send one prompt to one model. Returns a long-format row dict."""
    prompt_text = prompt_template.replace("[GREEN_SKILL]", skill_name)
    row = {
        "skill_name": skill_name,
        "prompt_number": prompt_number,
        "model": model,
        "prompt_text": prompt_text,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "error": "",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        start = time.time()
        try:
            response = completion(
                model=model,
                messages=[{"role": "user", "content": prompt_text}],
            )
            usage = response.usage
            row.update({
                "response_text": response.choices[0].message.content,
                "response_time_s": round(time.time() - start, 2),
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            })
            return row
        except Exception as e:
            print(f"  attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(5 * attempt)  # backoff: 5s, 10s
            else:
                row.update({
                    "response_text": "",
                    "response_time_s": round(time.time() - start, 2),
                    "prompt_tokens": "", "completion_tokens": "", "total_tokens": "",
                    "error": str(e),
                })
                return row


def load_skills(pilot):
    """Read skill labels from the ESCO green skills CSV."""
    df = pd.read_csv(DATA_FILE)
    skills = df["preferredLabel"].dropna().tolist()
    return skills[:10] if pilot else skills


def load_done(path):
    """Return the set of (skill, prompt_number, model) already collected."""
    if not path.exists():
        return set()
    df = pd.read_csv(path)
    # rows that errored are retried on the next run
    df = df[df["error"].fillna("") == ""]
    return set(zip(df["skill_name"], df["prompt_number"].astype(int), df["model"]))


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", action="store_true", help="run only the first 10 skills")
    args = parser.parse_args()

    if not DATA_FILE.exists():
        sys.exit(f"missing {DATA_FILE} - put the ESCO green skills CSV there first")

    skills = load_skills(args.pilot)
    done = load_done(OUTPUT_FILE)
    total = len(skills) * len(PROMPTS) * len(MODELS)
    print(f"{len(skills)} skills x {len(PROMPTS)} prompts x {len(MODELS)} models "
          f"= {total} calls ({len(done)} already done)")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_header = not OUTPUT_FILE.exists()

    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()

        for model in MODELS:
            for skill in skills:
                for prompt_number, template in enumerate(PROMPTS, start=1):
                    if (skill, prompt_number, model) in done:
                        continue
                    print(f"[{model}] prompt {prompt_number} | {skill}")
                    row = ask_llm(model, skill, prompt_number, template)
                    writer.writerow(row)
                    f.flush()  # persist immediately - a crash loses nothing

    print(f"done. results in {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
