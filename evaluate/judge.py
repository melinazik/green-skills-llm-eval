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

Output: evaluate/output_evaluate/ratings_llm_<judge>.csv with the rating columns
plus judge telemetry (rendered prompt, raw reply, tokens, latency, cost), so
stats.py keeps working while the browser can inspect more detail.
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
MISSING_CSV = OUT_DIR / "missing.csv"
VERBOSE_LOG = OUT_DIR / "judge_verbose.log"

# a judged cell is one answer seen by one judge
KEY_COLS = ["conceptUri", "prompt_number", "llm"]
JUDGE_TELEMETRY_COLS = [
    "litellm_model",
    "system_prompt_rendered",
    "prompt_text_rendered",
    "response_text",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "time_taken_s",
    "cost",
    "finish_reason",
    "params_json",
]
OUTPUT_COLS = [
    "conceptUri",
    "preferredLabel",
    "prompt_number",
    "prompt_category",
    "llm",
    "rater",
    "self_judged",
] + CRITERIA + ["rationale"] + JUDGE_TELEMETRY_COLS


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


def extract_usage(resp: Any) -> Dict[str, Optional[int]]:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}

    if isinstance(usage, dict):
        p = usage.get("prompt_tokens")
        c = usage.get("completion_tokens")
        t = usage.get("total_tokens")
    else:
        p = getattr(usage, "prompt_tokens", None)
        c = getattr(usage, "completion_tokens", None)
        t = getattr(usage, "total_tokens", None)

    return {
        "prompt_tokens": int(p) if p is not None else None,
        "completion_tokens": int(c) if c is not None else None,
        "total_tokens": int(t) if t is not None else None,
    }


def extract_finish_reason(resp: Any) -> str:
    try:
        choice = resp.choices[0]
    except Exception:
        return ""

    if isinstance(choice, dict):
        reason = choice.get("finish_reason")
    else:
        reason = getattr(choice, "finish_reason", None)

    return "" if reason is None else str(reason)


def compute_cost(litellm: Any, resp: Any) -> Optional[float]:
    for fn in (
        lambda: litellm.completion_cost(resp),
        lambda: litellm.completion_cost(completion_response=resp),
    ):
        try:
            raw = fn()
            return float(raw) if raw is not None else None
        except Exception:
            continue
    return None


def build_judge_messages(*, user_prompt: str,
                         attempt: int, last_error: str) -> Tuple[str, list[Dict[str, str]]]:
    content = user_prompt
    if attempt > 1:
        content += (
            f"\n\nYour previous reply could not be read: {last_error}\n"
            "Reply again with the JSON object only, using exactly these keys: "
            '"coherence_clarity", "consistency_accuracy", '
            '"relevance_esco_alignment", "educational_value", and "overall_confidence". '
            "Each dimension must include 'reasoning' and integer 'score' from 1 to 5."
        )

    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
    return content, messages


def done_keys(path: Path, allowed_judges: Optional[Set[str]] = None) -> Set[Tuple[str, str, str, str]]:
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
            judge = str(rec.get("judge", ""))
            if allowed_judges is not None and judge not in allowed_judges:
                continue
            key = (str(rec.get("conceptUri", "")), str(rec.get("prompt_number", "")),
                   str(rec.get("llm", "")), judge)
            if all(key):
                keys.add(key)
    return keys


def mock_rating(*, judge: str, author: str, skill_id: str) -> AnswerRating:
    """Deterministic fake scores, with a small self-preference so --mock exercises
    the bias reporting as well as the plumbing."""
    base = 3 + (len(skill_id) % 2)
    if judge == author:
        base = min(5, base + 1)

    payload = {
        "coherence_clarity": {
            "reasoning": f"mock coherence/clarity by {judge}",
            "score": base,
        },
        "consistency_accuracy": {
            "reasoning": f"mock consistency/accuracy by {judge}",
            "score": base,
        },
        "relevance_esco_alignment": {
            "reasoning": f"mock relevance/alignment by {judge}",
            "score": base,
        },
        "educational_value": {
            "reasoning": f"mock educational value by {judge}",
            "score": base,
        },
        "overall_confidence": 0.5,
    }
    return AnswerRating.model_validate(payload)


def call_judge(*, litellm, model_cfg: Dict[str, Any], run_cfg: Dict[str, Any],
               user_prompt: str, attempt: int = 1,
               last_error: str = "") -> Tuple[str, Dict[str, Any]]:
    supports_schema = bool(model_cfg.get("supports_schema", False))
    content, messages = build_judge_messages(
        user_prompt=user_prompt,
        attempt=attempt,
        last_error=last_error,
    )

    # The first attempt is deterministic, as everywhere else in the pipeline. A retry
    # at temperature 0 with the same seed would reproduce the same broken reply, so
    # later attempts loosen both. Only malformed replies are ever re-asked.
    temperature = run_cfg.get("temperature", 0.0) if attempt == 1 else 0.3
    seed = int(run_cfg.get("seed", 42)) + (attempt - 1)
    request_params = {"temperature": temperature, "seed": seed}

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
    t0 = time.perf_counter()
    try:
        if use_drop_params:
            litellm.drop_params = True
        resp = litellm.completion(**kwargs)
    finally:
        if use_drop_params:
            litellm.drop_params = prev_drop

    dt = time.perf_counter() - t0
    raw = extract_text_content(resp)
    usage = extract_usage(resp)
    telemetry = {
        "litellm_model": model_cfg["litellm_model"],
        "system_prompt_rendered": JUDGE_SYSTEM_PROMPT,
        "prompt_text_rendered": content,
        "response_text": raw,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "time_taken_s": round(dt, 6),
        "cost": compute_cost(litellm, resp),
        "finish_reason": extract_finish_reason(resp),
        "params_json": json.dumps(request_params, ensure_ascii=False, sort_keys=True),
        "usage": usage,
        "request": {
            "messages": messages,
            "params": request_params,
            "api_base": model_cfg.get("api_base"),
            "mock": False,
        },
    }
    return raw, telemetry


def write_missing_csv(path: Path, missing_keys: Set[Tuple[str, str, str]]) -> None:
    rows = [
        {
            "conceptUri": concept_uri,
            "prompt_number": prompt_number,
            "llm": llm,
        }
        for concept_uri, prompt_number, llm in sorted(missing_keys)
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=KEY_COLS)
        writer.writeheader()
        writer.writerows(rows)


def write_csvs(checkpoint: Path, out_dir: Path,
               allowed_judges: Optional[Set[str]] = None) -> Dict[str, int]:
    """Rebuild one CSV per judge from the checkpoint, optionally filtered by judge."""
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
                if allowed_judges is not None and judge not in allowed_judges:
                    continue
                key = (str(rec.get("conceptUri", "")), str(rec.get("prompt_number", "")),
                       str(rec.get("llm", "")))
                by_judge.setdefault(judge, {})[key] = rec

    if allowed_judges is not None:
        for path in out_dir.glob("ratings_llm_*.csv"):
            judge_name = path.stem.replace("ratings_llm_", "", 1)
            if judge_name not in allowed_judges:
                path.unlink(missing_ok=True)

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
            for c in JUDGE_TELEMETRY_COLS:
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
    combined_path = out_dir / COMBINED_CSV
    if all_rows:
        with combined_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS)
            writer.writeheader()
            writer.writerows(all_rows)
    else:
        combined_path.unlink(missing_ok=True)

    return counts


def crosscheck_missing(*, answers: list[Dict[str, Any]]) -> Set[Tuple[str, str, str]]:
    """Find per-LLM missing keys against the union seen across all LLMs.

    Report (conceptUri, prompt_number, llm) for the LLM that is missing that key.
    """
    by_llm: Dict[str, Set[Tuple[str, str]]] = {}
    for answer in answers:
        llm = answer["llm"]
        key2 = (answer["conceptUri"], answer["prompt_number"])
        by_llm.setdefault(llm, set()).add(key2)

    universe: Set[Tuple[str, str]] = set().union(*by_llm.values()) if by_llm else set()

    missing: Set[Tuple[str, str, str]] = set()
    for llm, llm_keys in by_llm.items():
        for concept_uri, prompt_number in universe:
            if (concept_uri, prompt_number) not in llm_keys:
                missing.add((concept_uri, prompt_number, llm))

    return missing


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the Step 4 answers with LLM judges")
    parser.add_argument("--config", default="evaluate/prompt_config.yaml",
                        help="where the judge models are read from")
    parser.add_argument("--input", default=str(RESPONSES))
    parser.add_argument("--limit", type=int, default=None, help="first N answers only")
    parser.add_argument("--judge", action="append", default=None,
                        help="only this judge (repeatable)")
    parser.add_argument("--mock", action="store_true", help="no calls, synthetic scores")
    parser.add_argument("--rebuild-csv", action="store_true",
                        help="rewrite the CSVs from the checkpoint and exit")
    parser.add_argument("--crosscheck", action="store_true",
                        help="write missing.csv with (conceptUri, prompt_number, llm) missing any judge")
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--verbose", action="store_true",
                        help="log each judged cell as it is processed")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

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

    allowed_judges = {str(j["name"]) for j in judges}

    if args.rebuild_csv:
        counts = write_csvs(CHECKPOINT, OUT_DIR, allowed_judges=allowed_judges)
        for judge, n in sorted(counts.items()):
            print(f"  ratings_llm_{judge}.csv: {n} rows")
        return

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"missing {input_path} - run Step 4 first")

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
    done = done_keys(CHECKPOINT, allowed_judges=allowed_judges)

    if args.crosscheck:
        missing_keys = crosscheck_missing(answers=answers)
        write_missing_csv(MISSING_CSV, missing_keys)
        print(f"Cross-check wrote {MISSING_CSV} ({len(missing_keys)} rows)")
        return

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

            if args.verbose:
                print(
                    "Processing cell: "
                    f"judge={judge}, "
                    f"prompt_number={answer['prompt_number']}, "
                    f"skill_id={answer['conceptUri']}"
                )

            if key in done:
                stats[judge]["skipped"] += 1
                if args.verbose:
                    print("  -> skipped (already successful in checkpoint)")
                continue

            user_prompt = build_user_prompt(
                skill=answer.get("preferredLabel", ""),
                description=answer.get("description", ""),
                bloom_level=answer.get("bloom_level", ""),
                question=answer.get("prompt_text", ""),
                answer=answer.get("response_text", ""),
            )

            record: Optional[Dict[str, Any]] = None
            last_error = ""

            litellm_model = str(judge_cfg.get("litellm_model", ""))
            system_prompt_rendered = JUDGE_SYSTEM_PROMPT
            prompt_text_rendered = ""
            judge_response_text = ""
            usage = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
            cost = None
            finish_reason = ""
            time_taken_s = None
            params_json = ""

            for attempt in range(1, args.max_retries + 2):
                ts_start = utc_now_iso()
                t0 = time.perf_counter()
                status = "success"
                error_info: Optional[Dict[str, Any]] = None

                usage = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
                cost = None
                finish_reason = ""
                judge_response_text = ""
                time_taken_s = None

                prompt_text_rendered, messages = build_judge_messages(
                    user_prompt=user_prompt,
                    attempt=attempt,
                    last_error=last_error,
                )
                temperature = judge_run_cfg.get("temperature", 0.0) if attempt == 1 else 0.3
                seed = int(judge_run_cfg.get("seed", 42)) + (attempt - 1)
                request_params = {"temperature": temperature, "seed": seed}
                params_json = json.dumps(request_params, ensure_ascii=False, sort_keys=True)
                request_payload = {
                    "messages": messages,
                    "params": request_params,
                    "api_base": judge_cfg.get("api_base"),
                    "mock": bool(args.mock),
                }

                try:
                    if args.mock:
                        rating = mock_rating(judge=judge, author=answer["llm"],
                                             skill_id=answer["conceptUri"])
                        judge_response_text = "(mock)"
                        cost = 0.0
                        time_taken_s = round(time.perf_counter() - t0, 6)
                    else:
                        import litellm as _litellm
                        raw, telemetry = call_judge(
                            litellm=_litellm,
                            model_cfg=judge_cfg,
                            run_cfg=judge_run_cfg,
                            user_prompt=user_prompt,
                            attempt=attempt,
                            last_error=last_error,
                        )
                        judge_response_text = raw
                        litellm_model = str(telemetry.get("litellm_model", litellm_model))
                        system_prompt_rendered = str(
                            telemetry.get("system_prompt_rendered", system_prompt_rendered)
                        )
                        prompt_text_rendered = str(
                            telemetry.get("prompt_text_rendered", prompt_text_rendered)
                        )
                        usage = telemetry.get("usage", usage)
                        cost = telemetry.get("cost")
                        finish_reason = str(telemetry.get("finish_reason", ""))
                        time_taken_s = telemetry.get("time_taken_s")
                        params_json = str(telemetry.get("params_json", params_json))
                        request_payload = telemetry.get("request", request_payload)
                        rating = parse_rating(raw)

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
                        "litellm_model": litellm_model,
                        "system_prompt_rendered": system_prompt_rendered,
                        "prompt_text_rendered": prompt_text_rendered,
                        "response_text": judge_response_text,
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                        "time_taken_s": time_taken_s,
                        "cost": cost,
                        "finish_reason": finish_reason,
                        "params_json": params_json,
                        **rating.scores(),
                    }
                    stats[judge]["ok"] += 1
                    done.add(key)
                    break

                except (RatingParseError, Exception) as exc:  # noqa: B014
                    status = "error"
                    last_error = f"{type(exc).__name__}: {exc}"
                    error_info = {"type": type(exc).__name__, "message": str(exc)}
                    if attempt <= args.max_retries:
                        time.sleep(min(20, 2 ** (attempt - 1)))

                finally:
                    duration_ms = int((time.perf_counter() - t0) * 1000)
                    append_jsonl(VERBOSE_LOG, {
                        "ts_start": ts_start,
                        "ts_end": utc_now_iso(),
                        "duration_ms": duration_ms,
                        "judge": judge,
                        "conceptUri": answer["conceptUri"],
                        "prompt_number": answer["prompt_number"],
                        "llm": answer["llm"],
                        "litellm_model": litellm_model,
                        "attempt": attempt,
                        "request": request_payload,
                        "response_raw": judge_response_text,
                        "usage": usage,
                        "cost_usd": cost,
                        "finish_reason": finish_reason,
                        "status": status,
                        "error": error_info,
                        "mock": bool(args.mock),
                    })

            if record is None:
                stats[judge]["failed"] += 1
                record = {
                    "status": "error",
                    "judge": judge,
                    "conceptUri": answer["conceptUri"],
                    "prompt_number": answer["prompt_number"],
                    "llm": answer["llm"],
                    "error": last_error[:2000],
                    "datetime": utc_now_iso(),
                    "mock": bool(args.mock),
                    "litellm_model": litellm_model,
                    "system_prompt_rendered": system_prompt_rendered,
                    "prompt_text_rendered": prompt_text_rendered,
                    "response_text": judge_response_text,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "time_taken_s": time_taken_s,
                    "cost": cost,
                    "finish_reason": finish_reason,
                    "params_json": params_json,
                }

            append_jsonl(CHECKPOINT, record)
            processed += 1

            if processed % 25 == 0:
                elapsed = time.perf_counter() - started
                rate = processed / elapsed if elapsed else 0
                left = total - already - processed
                eta = int(left / rate) if rate else 0
                print(f"  {processed} judged this run, {left} left, eta ~{eta // 60}m {eta % 60}s")

    counts = write_csvs(CHECKPOINT, OUT_DIR, allowed_judges=allowed_judges)

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
