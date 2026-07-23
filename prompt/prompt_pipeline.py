"""LLM prompting pipeline for ESCO green skills.

For each selected skill, ask every enabled model the 5 fixed questions and save
the answers in long format (one row per skill x prompt x model).

Settings live in prompt_config.yaml (paths, prompts, models, temperature, seed),
so the code does not change when you change models or prompts.

Features:
- config file driven (no hardcoded models or prompts)
- appends each result to the CSV immediately, so a crash loses nothing
- resumable: already collected (skill, prompt, model) combos are skipped
- retries with backoff on transient API errors
- structured verbose log (one JSON line per call) for debugging
- progress with timing and a rough estimate of time left

Usage (from the project root folder):
    python prompt/prompt_pipeline.py                    # full run
    python prompt/prompt_pipeline.py --limit 10         # first 10 skills only
    python prompt/prompt_pipeline.py --config other.yaml
"""

import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml
from litellm import completion

FIELDNAMES = [
    "skill_name", "prompt_number", "model", "litellm_model", "prompt_text",
    "response_text", "timestamp", "response_time_s",
    "prompt_tokens", "completion_tokens", "total_tokens", "error",
]


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def append_jsonl(path, record):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def ask_llm(model_cfg, skill_name, prompt_number, prompt_template, run_cfg, verbose_path):
    """Send one prompt to one model. Returns a long-format row dict."""
    prompt_text = prompt_template.replace("[GREEN_SKILL]", skill_name)
    max_retries = int(run_cfg.get("max_retries", 3))
    row = {
        "skill_name": skill_name,
        "prompt_number": prompt_number,
        "model": model_cfg["name"],
        "litellm_model": model_cfg["litellm_model"],
        "prompt_text": prompt_text,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "error": "",
    }

    for attempt in range(1, max_retries + 1):
        start = time.time()
        try:
            response = completion(
                model=model_cfg["litellm_model"],
                messages=[{"role": "user", "content": prompt_text}],
                temperature=run_cfg.get("temperature", 0.0),
                seed=run_cfg.get("seed", 42),
                api_base=model_cfg.get("api_base"),
            )
            usage = response.usage
            duration = round(time.time() - start, 2)
            row.update({
                "response_text": response.choices[0].message.content,
                "response_time_s": duration,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            })
            append_jsonl(verbose_path, {
                "timestamp": row["timestamp"], "skill": skill_name,
                "prompt_number": prompt_number, "model": model_cfg["name"],
                "attempt": attempt, "duration_s": duration, "status": "success",
            })
            return row
        except Exception as e:
            print(f"  attempt {attempt}/{max_retries} failed: {e}")
            append_jsonl(verbose_path, {
                "timestamp": row["timestamp"], "skill": skill_name,
                "prompt_number": prompt_number, "model": model_cfg["name"],
                "attempt": attempt, "status": "error", "error": str(e),
            })
            if attempt < max_retries:
                time.sleep(5 * attempt)  # backoff: 5s, 10s
            else:
                row.update({
                    "response_text": "",
                    "response_time_s": round(time.time() - start, 2),
                    "prompt_tokens": "", "completion_tokens": "", "total_tokens": "",
                    "error": str(e),
                })
                return row


def load_skills(input_csv, label_col, limit):
    """Read skill names from the selected skills CSV."""
    df = pd.read_csv(input_csv)
    if label_col not in df.columns:
        raise SystemExit(f"column '{label_col}' not found in {input_csv}")
    skills = df[label_col].dropna().tolist()
    return skills[:limit] if limit else skills


def load_done(path):
    """Return the set of (skill, prompt_number, model) already collected."""
    if not path.exists():
        return set()
    df = pd.read_csv(path)
    df = df[df["error"].fillna("") == ""]   # errored rows are retried next run
    return set(zip(df["skill_name"], df["prompt_number"].astype(int), df["model"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="prompt/prompt_config.yaml")
    parser.add_argument("--limit", type=int, default=None, help="cap the number of skills")
    parser.add_argument("--input", default=None, help="override the input CSV path")
    parser.add_argument("--output", default=None, help="override the output CSV path")
    args = parser.parse_args()

    cfg = load_config(args.config)
    run_cfg = cfg["run"]
    prompts = cfg["prompts"]
    models = [m for m in cfg["models"] if m.get("enabled", True)]

    input_csv = Path(args.input or run_cfg["input_csv"])
    output_csv = Path(args.output or run_cfg["output_csv"])
    verbose_path = Path(run_cfg["verbose_log"])
    limit = args.limit if args.limit is not None else run_cfg.get("limit")

    if not input_csv.exists():
        raise SystemExit(f"missing {input_csv} - export the selected skills from Step 3 first")

    skills = load_skills(input_csv, run_cfg["label_col"], limit)
    done = load_done(output_csv)
    total = len(skills) * len(prompts) * len(models)
    print(f"{len(skills)} skills x {len(prompts)} prompts x {len(models)} models "
          f"= {total} calls ({len(done)} already done)")

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    verbose_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not output_csv.exists()

    with open(output_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()

        for model_cfg in models:
            model_name = model_cfg["name"]
            model_t0 = time.perf_counter()
            model_done_count = 0

            for idx, skill in enumerate(skills, start=1):
                for prompt_number, template in enumerate(prompts, start=1):
                    if (skill, prompt_number, model_name) in done:
                        continue
                    print(f"[{model_name}] skill {idx}/{len(skills)} prompt {prompt_number} | {skill}", end="", flush=True)
                    row = ask_llm(model_cfg, skill, prompt_number, template, run_cfg, verbose_path)
                    writer.writerow(row)
                    f.flush()   # persist immediately, a crash loses nothing
                    model_done_count += 1
                    tail = f" | {row['response_time_s']}s" if not row["error"] else " | ERROR"
                    print(tail, flush=True)

                if idx % 10 == 0:
                    elapsed = time.perf_counter() - model_t0
                    per_skill = elapsed / idx
                    remaining_s = (len(skills) - idx) * per_skill
                    left = f"~{remaining_s:.0f}s left" if remaining_s < 60 else f"~{remaining_s / 60:.1f} min left"
                    print(f"  {model_name}: {idx}/{len(skills)} skills  |  "
                          f"{elapsed:.0f}s elapsed, {left}")

    print(f"done. results in {output_csv}")


if __name__ == "__main__":
    main()
