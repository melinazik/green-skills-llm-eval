#!/usr/bin/env python3
"""
call each enabled model on each skill and persist structured outputs.

Implements spec:
- Schema-first LiteLLM calls with JSON fallback for non-schema models
- Retry with exponential backoff on API/parse/validation errors
- Resumable checkpoint JSONL
- Structured verbose JSONL logging for every call attempt
- Long-format predictions_raw.csv output
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

# Load environment variables from a local .env file if one exists.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from taxonomy import (
    JSON_FALLBACK_INSTRUCTION,
    SYSTEM_PROMPT,
    SkillLabel,
    build_user_prompt,
)
 

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_skills(run_cfg: Dict[str, Any], limit: Optional[int]) -> List[Dict[str, str]]:
    df = pd.read_csv(run_cfg["input_csv"], dtype=str).fillna("")
    rows: List[Dict[str, str]] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "skill_id": str(row[run_cfg["id_col"]]),
                "label_text": str(row[run_cfg["label_col"]]),
                "description": str(row[run_cfg["desc_col"]]),
                "alt_labels": str(row[run_cfg["alt_col"]]),
            }
        )
    return rows[:limit] if limit else rows


def checkpoint_done_pairs(checkpoint_path: Path) -> set[Tuple[str, str]]:
    done: set[Tuple[str, str]] = set()
    if not checkpoint_path.exists():
        return done

    with checkpoint_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue

            skill_id = rec.get("skill_id")
            model_name = rec.get("model")
            if skill_id and model_name:
                done.add((str(skill_id), str(model_name)))
    return done


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def strip_fences(text: str) -> str:
    txt = text.strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\s*```$", "", txt)
    return txt.strip()


def first_json_object(text: str) -> str:
    """Extract first balanced JSON object from arbitrary text."""
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model response")

    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    raise ValueError("Unbalanced JSON object in model response")


def parse_skill_label(raw_text: str) -> SkillLabel:
    cleaned = strip_fences(raw_text)
    json_text = first_json_object(cleaned)
    return SkillLabel.model_validate_json(json_text)


def extract_text_content(resp: Any) -> str:
    """Best-effort extraction of assistant message content from LiteLLM response."""
    msg = resp.choices[0].message
    content = getattr(msg, "content", None)

    if content is None:
        # Some providers may return parsed content elsewhere; keep deterministic fallback.
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)

    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if "text" in item and isinstance(item["text"], str):
                    parts.append(item["text"])
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)

    return str(content)


def extract_usage(resp: Any) -> Dict[str, Optional[int]]:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }

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


def mock_label(skill: Dict[str, str], model_name: str, seed: int) -> SkillLabel:
    """Deterministic synthetic labels for offline smoke testing."""
    from taxonomy import PrimaryCategory

    text = f"{skill['label_text']} {skill['description']} {skill['alt_labels']}".lower()
    categories = [
        PrimaryCategory.G1,
        PrimaryCategory.G2,
        PrimaryCategory.G3,
        PrimaryCategory.G4,
        PrimaryCategory.G5,
        PrimaryCategory.G6,
        PrimaryCategory.G7,
        PrimaryCategory.G8,
    ]

    # Deterministic pseudo-random by skill+model+seed (stable across processes).
    digest = hashlib.md5(
        f"{skill['skill_id']}|{model_name}|{seed}".encode("utf-8")
    ).hexdigest()
    stable = int(digest, 16)
    cat = categories[stable % len(categories)]

    if re.search(r"solar|wind|battery|hydrogen|grid", text):
        cat = PrimaryCategory.G1
    elif re.search(r"heat pump|insulation|hvac|building|retrofit|energy audit", text):
        cat = PrimaryCategory.G2
    elif re.search(r"recycl|waste|circular|reuse|water", text):
        cat = PrimaryCategory.G3
    elif re.search(r"pollution|biodiversity|ecosystem|remediation", text):
        cat = PrimaryCategory.G4
    elif re.search(r"agric|forest|fisher|food|soil", text):
        cat = PrimaryCategory.G5
    elif re.search(r"transport|mobility|manufactur", text):
        cat = PrimaryCategory.G6
    elif re.search(r"policy|compliance|finance|esg|govern", text):
        cat = PrimaryCategory.G7
    elif re.search(r"data|digital|sensor|iot|ai|software", text):
        cat = PrimaryCategory.G8

    overlay = bool(re.search(r"teach|training|awareness|educat", text))
    confidence = round(0.6 + (stable % 40) / 100.0, 2)

    return SkillLabel(
        rationale="Mock classification for offline test run.",
        primary_category=cat,
        education_overlay=overlay,
        secondary_category=None,
        confidence=confidence,
    )


def is_gemini_model(model_cfg: Dict[str, Any]) -> bool:
    model_id = str(model_cfg.get("litellm_model", "")).lower()
    model_name = str(model_cfg.get("name", "")).lower()
    return "gemini" in model_id or "gemini" in model_name


def call_model_once(
    *,
    litellm: Any,
    model_cfg: Dict[str, Any],
    run_cfg: Dict[str, Any],
    system_prompt: str,
    user_prompt: str,
) -> Tuple[SkillLabel, str, Dict[str, Optional[int]], Optional[float]]:
    supports_schema = bool(model_cfg.get("supports_schema", False))

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": user_prompt + ("" if supports_schema else JSON_FALLBACK_INSTRUCTION),
        },
    ]

    kwargs: Dict[str, Any] = {
        "model": model_cfg["litellm_model"],
        "messages": messages,
        "temperature": run_cfg.get("temperature", 0.0),
        "seed": run_cfg.get("seed", 42),
    }
    api_base = model_cfg.get("api_base")
    api_base_env = model_cfg.get("api_base_env")
    if api_base_env:
        api_base = os.getenv(str(api_base_env))
        if not api_base:
            raise RuntimeError(
                f"Missing environment variable '{api_base_env}' required for model '{model_cfg.get('name', model_cfg['litellm_model'])}'"
            )
    if api_base:
        kwargs["api_base"] = api_base

    api_key_env = model_cfg.get("api_key_env")
    if api_key_env:
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Missing environment variable '{api_key_env}' required for model '{model_cfg.get('name', model_cfg['litellm_model'])}'"
            )
        kwargs["api_key"] = api_key

    if supports_schema:
        kwargs["response_format"] = SkillLabel
    else:
        kwargs["response_format"] = {"type": "json_object"}

    use_drop_params = is_gemini_model(model_cfg)
    prev_drop_params = getattr(litellm, "drop_params", None)

    try:
        if use_drop_params:
            litellm.drop_params = True

        resp = litellm.completion(**kwargs)
    finally:
        if use_drop_params:
            litellm.drop_params = prev_drop_params

    raw_text = extract_text_content(resp)
    parsed = parse_skill_label(raw_text)
    usage = extract_usage(resp)

    try:
        cost = litellm.completion_cost(completion_response=resp)
    except Exception:
        cost = None

    return parsed, raw_text, usage, cost


def write_predictions_from_checkpoint(checkpoint_path: Path, out_csv: Path) -> int:
    rows: List[Dict[str, Any]] = []
    if checkpoint_path.exists():
        with checkpoint_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if "primary_category" in rec:
                    rows.append(rec)

    if not rows:
        pd.DataFrame().to_csv(out_csv, index=False)
        return 0

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["skill_id", "model"], keep="last")

    preferred_cols = [
        "skill_id",
        "label_text",
        "model",
        "litellm_model",
        "rationale",
        "primary_category",
        "education_overlay",
        "secondary_category",
        "confidence",
        "cost",
        "ts",
    ]
    existing_cols = [c for c in preferred_cols if c in df.columns]
    df[existing_cols].to_csv(out_csv, index=False)
    return len(df)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="categorize/models_config.yaml")
    parser.add_argument("--mock", action="store_true", help="offline synthetic labels")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    cfg = load_yaml(Path(args.config))
    run_cfg = cfg["run"]
    model_cfgs = [m for m in cfg["models"] if m.get("enabled", True)]

    checkpoint_path = Path(run_cfg["checkpoint"])
    verbose_path = Path(run_cfg.get("verbose_log", "verbose.log"))
    out_csv = Path(run_cfg["predictions_out"])

    skills = read_skills(run_cfg, args.limit)
    done = checkpoint_done_pairs(checkpoint_path)

    total_calls = len(skills) * len(model_cfgs)
    print(
        f"{len(skills)} skills x {len(model_cfgs)} models = {total_calls} calls"
        f" ({len(done)} already in checkpoint){' [MOCK]' if args.mock else ''}"
    )

    litellm = None
    litellm_version = None
    if not args.mock:
        import litellm as _litellm

        litellm = _litellm
        litellm_version = getattr(_litellm, "__version__", "unknown")
        print(f"LiteLLM version: {litellm_version}")

    cost_by_model: Dict[str, float] = {m["name"]: 0.0 for m in model_cfgs}
    tokens_by_model: Dict[str, Dict[str, int]] = {
        m["name"]: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for m in model_cfgs
    }

    for model in model_cfgs:
        model_name = model["name"]
        model_t0 = time.perf_counter()   # wall-clock start for this model
        model_done_count = 0             # skills actually processed (not skipped)

        for idx, skill in enumerate(skills, start=1):
            pair = (skill["skill_id"], model_name)
            if pair in done:
                continue

            max_retries = int(run_cfg.get("max_retries", 2))
            max_attempts = max_retries + 1
            final_checkpoint_record: Optional[Dict[str, Any]] = None

            for attempt in range(1, max_attempts + 1):
                ts_start = utc_now_iso()
                t0 = time.perf_counter()

                supports_schema = bool(model.get("supports_schema", False))
                user_prompt = build_user_prompt(
                    preferred_label=skill["label_text"],
                    description=skill["description"],
                    alt_labels=skill["alt_labels"],
                )
                request_payload = {
                    "system_prompt": SYSTEM_PROMPT,
                    "user_prompt": user_prompt,
                    "temperature": run_cfg.get("temperature", 0.0),
                    "seed": run_cfg.get("seed", 42),
                    "response_format": (
                        "SkillLabel" if supports_schema else {"type": "json_object"}
                    ),
                    "api_base": model.get("api_base"),
                    "api_key_env": model.get("api_key_env"),
                    "drop_params": bool(is_gemini_model(model)),
                }

                raw_response: Optional[str] = None
                parsed_obj: Optional[Dict[str, Any]] = None
                usage: Dict[str, Optional[int]] = {
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                }
                cost_usd: Optional[float] = None
                status = "success"
                error_info: Optional[Dict[str, str]] = None

                try:
                    if args.mock:
                        label = mock_label(skill, model_name, int(run_cfg.get("seed", 42)))
                        raw_response = json.dumps(label.model_dump(mode="json"), ensure_ascii=False)
                        parsed_obj = label.model_dump(mode="json")
                        cost_usd = 0.0
                        usage = {
                            "prompt_tokens": None,
                            "completion_tokens": None,
                            "total_tokens": None,
                        }
                    else:
                        assert litellm is not None
                        label, raw_response, usage, cost_usd = call_model_once(
                            litellm=litellm,
                            model_cfg=model,
                            run_cfg=run_cfg,
                            system_prompt=SYSTEM_PROMPT,
                            user_prompt=user_prompt,
                        )
                        parsed_obj = label.model_dump(mode="json")

                    final_checkpoint_record = {
                        "skill_id": skill["skill_id"],
                        "label_text": skill["label_text"],
                        "model": model_name,
                        "litellm_model": model["litellm_model"],
                        **label.model_dump(mode="json"),
                        "cost": cost_usd,
                        "ts": utc_now_iso(),
                        "litellm_version": litellm_version,
                    }

                    if cost_usd is not None and not math.isnan(float(cost_usd)):
                        cost_by_model[model_name] += float(cost_usd)
                    if usage.get("prompt_tokens"):
                        tokens_by_model[model_name]["prompt_tokens"] += int(usage["prompt_tokens"])
                    if usage.get("completion_tokens"):
                        tokens_by_model[model_name]["completion_tokens"] += int(
                            usage["completion_tokens"]
                        )
                    if usage.get("total_tokens"):
                        tokens_by_model[model_name]["total_tokens"] += int(usage["total_tokens"])

                except Exception as exc:
                    status = "error"
                    error_info = {"type": type(exc).__name__, "message": str(exc)}
                    if attempt >= max_attempts:
                        final_checkpoint_record = {
                            "skill_id": skill["skill_id"],
                            "model": model_name,
                            "litellm_model": model["litellm_model"],
                            "error": str(exc)[:1000],
                            "ts": utc_now_iso(),
                            "litellm_version": litellm_version,
                        }

                duration_ms = int((time.perf_counter() - t0) * 1000)
                ts_end = utc_now_iso()

                verbose_record = {
                    "ts_start": ts_start,
                    "ts_end": ts_end,
                    "duration_ms": duration_ms,
                    "skill_id": skill["skill_id"],
                    "model": model_name,
                    "litellm_model": model["litellm_model"],
                    "attempt": attempt,
                    "request": request_payload,
                    "response_raw": raw_response,
                    "response_parsed": parsed_obj,
                    "usage": usage,
                    "cost_usd": cost_usd,
                    "status": status,
                    "error": error_info,
                    "litellm_version": litellm_version,
                    "mock": bool(args.mock),
                }
                append_jsonl(verbose_path, verbose_record)

                if status == "success":
                    break

                # Exponential backoff before next retry.
                sleep_seconds = min(20, 2 ** (attempt - 1))
                time.sleep(sleep_seconds)

            if final_checkpoint_record is not None:
                append_jsonl(checkpoint_path, final_checkpoint_record)
                done.add(pair)
                model_done_count += 1

            if idx % 10 == 0:
                elapsed = time.perf_counter() - model_t0
                rate = elapsed / model_done_count if model_done_count else 0.0
                remaining_s = (len(skills) - idx) * rate
                left = f"~{remaining_s:.0f}s left" if remaining_s < 60 else f"~{remaining_s / 60:.1f} min left"
                print(
                    f"  {model_name}: {idx}/{len(skills)}  |  "
                    f"{elapsed:.0f}s elapsed, ~{rate:.1f}s/skill, {left}"
                )

    n_rows = write_predictions_from_checkpoint(checkpoint_path, out_csv)
    print(f"\nWrote {out_csv} ({n_rows} rows)")

    print("Cost by model (USD):")
    for model_name, cost in cost_by_model.items():
        print(f"  - {model_name}: {cost:.6f}")

    print("Token usage by model:")
    for model_name, tok in tokens_by_model.items():
        print(
            f"  - {model_name}: prompt={tok['prompt_tokens']}, "
            f"completion={tok['completion_tokens']}, total={tok['total_tokens']}"
        )

    print(f"Verbose log: {verbose_path}")


if __name__ == "__main__":
    main()
