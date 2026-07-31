"""LLM-as-a-judge for Step 5. DRAFT, the rubric is provisional.

Every judge model scores every answer, including the answers it wrote itself. That
is deliberate: a model asked to grade only its own work tends to score it high
(self-preference bias), and with cross-evaluation the bias becomes measurable
instead of hidden. evaluate/stats.py reports it.

Judges are blind to the author: the prompt never says which model wrote the answer.

    python evaluate/judge.py                    # every answer, every judge
    python evaluate/judge.py --limit 20         # first 20 answers, to test
    python evaluate/judge.py --mock             # no calls, synthetic scores
    python evaluate/judge.py --judge gemma      # only this judge

Output: evaluate/output_evaluate/ratings_llm_<judge>.csv, the same columns as the
human ratings plus the judge's one-line rationale, so stats.py reads both together.
Resumable through evaluate/output_evaluate/judge_checkpoint.jsonl.
"""

import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

import pandas as pd
import yaml

from rubric import (
    CRITERIA,
    JSON_FALLBACK_INSTRUCTION,
    JUDGE_SYSTEM_PROMPT,
    AnswerRating,
    RatingParseError,
    build_user_prompt,
    parse_rating,
)

RESPONSES = Path("prompt/output_prompt/responses.csv")
OUT_DIR = Path("evaluate/output_evaluate")
CHECKPOINT = OUT_DIR / "judge_checkpoint.jsonl"
# combined view of every judge, for the browser tab; not read by stats.py
COMBINED_CSV = "llm_judgements.csv"
VERBOSE_LOG = OUT_DIR / "judge_verbose.log"

# a judged cell is one answer seen by one judge
KEY_COLS = ["conceptUri", "prompt_number", "llm"]
OUTPUT_COLS = [
    "conceptUri",
    "preferredLabel",
    "prompt_number",
    "prompt_category",
    "llm",
    "rater",
    "self_judged",
] + CRITERIA + ["rationale"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def extract_text_content(resp: Any) -> str:
    msg = resp.choices[0].message
    content = getattr(msg, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def done_keys(path: Path) -> Set[Tuple[str, str, str, str]]:
    """(skill, prompt, author model, judge) cells already scored."""
    keys: Set[Tuple[str, str, str, str]] = set()
    if not path.exists():
        return keys

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("status") != "success":
                continue
            key = (str(rec.get("conceptUri", "")), str(rec.get("prompt_number", "")),
                   str(rec.get("llm", "")), str(rec.get("judge", "")))
            if all(key):
                keys.add(key)
    return keys


def mock_rating(*, judge: str, author: str, skill_id: str) -> AnswerRating:
    """Deterministic fake scores, with a small self-preference so --mock exercises
    the bias reporting as well as the plumbing."""
    base = 3 + (len(skill_id) % 2)
    if judge == author:
        base = min(5, base + 1)
    return AnswerRating(
        rationale=f"mock rating by {judge}",
        clarity=base, depth=base, relevance=base, pedagogical=base,
    )


def call_judge(*, litellm, model_cfg: Dict[str, Any], run_cfg: Dict[str, Any],
               user_prompt: str, attempt: int = 1,
               last_error: str = "") -> Tuple[AnswerRating, str]:
    supports_schema = bool(model_cfg.get("supports_schema", False))

    content = user_prompt + ("" if supports_schema else JSON_FALLBACK_INSTRUCTION)
    if attempt > 1:
        content += (
            f"\n\nYour previous reply could not be read: {last_error}\n"
            "Reply again with the JSON object only. All four scores must be present "
            "and each must be an integer from 1 to 5."
        )

    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    # The first attempt is deterministic, as everywhere else in the pipeline. A retry
    # at temperature 0 with the same seed would reproduce the same broken reply, so
    # later attempts loosen both. Only malformed replies are ever re-asked.
    temperature = run_cfg.get("temperature", 0.0) if attempt == 1 else 0.3
    seed = int(run_cfg.get("seed", 42)) + (attempt - 1)

    kwargs: Dict[str, Any] = {
        "model": model_cfg["litellm_model"],
        "messages": messages,
        "temperature": temperature,
        "seed": seed,
    }
    if model_cfg.get("api_base"):
        kwargs["api_base"] = model_cfg["api_base"]

    api_key_env = model_cfg.get("api_key_env")
    if api_key_env:
        api_key = os.getenv(str(api_key_env))
        if not api_key:
            raise RuntimeError(
                f"Missing environment variable '{api_key_env}' for judge "
                f"'{model_cfg.get('name', model_cfg['litellm_model'])}'")
        kwargs["api_key"] = api_key

    kwargs["response_format"] = AnswerRating if supports_schema else {"type": "json_object"}

    use_drop_params = "gemini" in str(model_cfg.get("litellm_model", "")).lower()
    prev_drop = getattr(litellm, "drop_params", None)
    try:
        if use_drop_params:
            litellm.drop_params = True
        resp = litellm.completion(**kwargs)
    finally:
        if use_drop_params:
            litellm.drop_params = prev_drop

    raw = extract_text_content(resp)
    return parse_rating(raw), raw


def write_csvs(checkpoint: Path, out_dir: Path) -> Dict[str, int]:
    """Rebuild one CSV per judge from the checkpoint. Removes duplicates."""
    by_judge: Dict[str, Dict[Tuple[str, str, str], Dict[str, Any]]] = {}

    if checkpoint.exists():
        with checkpoint.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("status") != "success":
                    continue
                judge = str(rec.get("judge", ""))
                key = (str(rec.get("conceptUri", "")), str(rec.get("prompt_number", "")),
                       str(rec.get("llm", "")))
                by_judge.setdefault(judge, {})[key] = rec

    counts: Dict[str, int] = {}
    all_rows = []
    for judge, records in by_judge.items():
        rows = []
        for rec in records.values():
            row = {
                "conceptUri": rec.get("conceptUri", ""),
                "preferredLabel": rec.get("preferredLabel", ""),
                "prompt_number": rec.get("prompt_number", ""),
                "prompt_category": rec.get("prompt_category", ""),
                "llm": rec.get("llm", ""),
                "rater": f"llm:{judge}",
                "self_judged": "yes" if rec.get("llm") == judge else "no",
                "rationale": rec.get("rationale", ""),
            }
            for c in CRITERIA:
                row[c] = rec.get(c)
            rows.append(row)

        out_path = out_dir / f"ratings_llm_{judge}.csv"
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS)
            writer.writeheader()
            writer.writerows(rows)
        counts[judge] = len(rows)
        all_rows.extend(rows)

    # One combined file as well, so the browser tab can read every judge from a
    # single fetch. stats.py ignores it: it only globs ratings_*.csv per judge.
    if all_rows:
        with (out_dir / COMBINED_CSV).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS)
            writer.writeheader()
            writer.writerows(all_rows)

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the Step 4 answers with LLM judges")
    parser.add_argument("--config", default="prompt/prompt_config.yaml",
                        help="where the judge models are read from")
    parser.add_argument("--input", default=str(RESPONSES))
    parser.add_argument("--limit", type=int, default=None, help="first N answers only")
    parser.add_argument("--judge", action="append", default=None,
                        help="only this judge (repeatable)")
    parser.add_argument("--mock", action="store_true", help="no calls, synthetic scores")
    parser.add_argument("--rebuild-csv", action="store_true",
                        help="rewrite the CSVs from the checkpoint and exit")
    parser.add_argument("--max-retries", type=int, default=2)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.rebuild_csv:
        counts = write_csvs(CHECKPOINT, OUT_DIR)
        for judge, n in sorted(counts.items()):
            print(f"  ratings_llm_{judge}.csv: {n} rows")
        return

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"missing {input_path} - run Step 4 first")

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    run_cfg = cfg.get("run", {}) or {}
    # temperature and seed sit under default_params in the Step 4 config
    judge_run_cfg = {**(run_cfg.get("default_params") or {}), **run_cfg}

    judges = [m for m in cfg.get("models", []) if m.get("enabled", True)]
    if args.judge:
        wanted = set(args.judge)
        judges = [m for m in judges if str(m.get("name")) in wanted]
        missing = wanted - {str(m.get("name")) for m in judges}
        if missing:
            raise SystemExit(f"unknown judge(s): {sorted(missing)}")
    if not judges:
        raise SystemExit("no judge models enabled in the config")

    df = pd.read_csv(input_path, dtype=str).fillna("")
    missing = [c for c in KEY_COLS + ["response_text", "prompt_text"] if c not in df.columns]
    if missing:
        raise SystemExit(f"{input_path} is missing the columns {missing}")

    if "error" in df.columns:
        df = df[df["error"] == ""]
    df = df[df["response_text"] != ""]
    if args.limit is not None:
        df = df.head(args.limit)

    answers = list(df.to_dict(orient="records"))
    done = done_keys(CHECKPOINT)

    total = len(answers) * len(judges)
    already = sum(
        1 for a in answers for j in judges
        if (a["conceptUri"], a["prompt_number"], a["llm"], str(j["name"])) in done
    )
    print(f"{len(answers)} answers x {len(judges)} judges = {total} cells "
          f"({already} already done)")

    if args.mock:
        print("MOCK mode: no judge is called")
    else:
        import litellm  # noqa: F401  (imported late so --mock needs no install)

    stats = {str(j["name"]): {"ok": 0, "failed": 0, "skipped": 0} for j in judges}
    started = time.perf_counter()
    processed = 0

    for judge_cfg in judges:
        judge = str(judge_cfg["name"])

        for answer in answers:
            key = (answer["conceptUri"], answer["prompt_number"], answer["llm"], judge)
            if key in done:
                stats[judge]["skipped"] += 1
                continue

            user_prompt = build_user_prompt(
                skill=answer.get("preferredLabel", ""),
                description=answer.get("description", ""),
                question=answer.get("prompt_text", ""),
                answer=answer.get("response_text", ""),
            )

            record: Optional[Dict[str, Any]] = None
            last_error = ""

            for attempt in range(1, args.max_retries + 2):
                try:
                    if args.mock:
                        rating = mock_rating(judge=judge, author=answer["llm"],
                                             skill_id=answer["conceptUri"])
                        raw = "(mock)"
                    else:
                        import litellm as _litellm
                        rating, raw = call_judge(litellm=_litellm, model_cfg=judge_cfg,
                                                 run_cfg=judge_run_cfg, user_prompt=user_prompt,
                                                 attempt=attempt, last_error=last_error)

                    record = {
                        "status": "success",
                        "judge": judge,
                        "conceptUri": answer["conceptUri"],
                        "preferredLabel": answer.get("preferredLabel", ""),
                        "prompt_number": answer["prompt_number"],
                        "prompt_category": answer.get("prompt_category", ""),
                        "llm": answer["llm"],
                        "rationale": rating.rationale,
                        "datetime": utc_now_iso(),
                        "mock": bool(args.mock),
                        **rating.scores(),
                    }
                    stats[judge]["ok"] += 1
                    done.add(key)
                    break

                except (RatingParseError, Exception) as exc:  # noqa: B014
                    last_error = f"{type(exc).__name__}: {exc}"
                    append_jsonl(VERBOSE_LOG, {
                        "ts": utc_now_iso(), "judge": judge, "attempt": attempt,
                        "conceptUri": answer["conceptUri"],
                        "prompt_number": answer["prompt_number"],
                        "llm": answer["llm"], "error": last_error,
                    })
                    if attempt <= args.max_retries:
                        time.sleep(min(20, 2 ** (attempt - 1)))

            if record is None:
                stats[judge]["failed"] += 1
                record = {
                    "status": "error", "judge": judge,
                    "conceptUri": answer["conceptUri"],
                    "prompt_number": answer["prompt_number"],
                    "llm": answer["llm"], "error": last_error[:2000],
                    "datetime": utc_now_iso(),
                }

            append_jsonl(CHECKPOINT, record)
            processed += 1

            if processed % 25 == 0:
                elapsed = time.perf_counter() - started
                rate = processed / elapsed if elapsed else 0
                left = total - already - processed
                eta = int(left / rate) if rate else 0
                print(f"  {processed} judged this run, {left} left, eta ~{eta // 60}m {eta % 60}s")

    counts = write_csvs(CHECKPOINT, OUT_DIR)

    print("\nPer judge:")
    for judge, s in stats.items():
        print(f"  {judge}: ok={s['ok']} failed={s['failed']} skipped={s['skipped']}")
    print("\nWrote:")
    for judge, n in sorted(counts.items()):
        print(f"  {OUT_DIR / f'ratings_llm_{judge}.csv'} ({n} rows)")
    print(f"\nCheckpoint: {CHECKPOINT}")
    if any(s["failed"] for s in stats.values()):
        print(f"Failures are logged in {VERBOSE_LOG}")
    print("\nNow run: python evaluate/stats.py")


if __name__ == "__main__":
    main()
