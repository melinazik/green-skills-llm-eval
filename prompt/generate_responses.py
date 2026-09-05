#!/usr/bin/env python3
"""
Run each enabled model on each prompt for each selected skill and persist responses.

Features:
- Resume-safe checkpoint JSONL keyed by (skill_id, prompt_number, model)
- Appends each successful result to the CSV immediately, so a crash loses nothing
- Verbose per-attempt JSONL telemetry (request/response/timing/tokens/cost/errors)
- Startup placeholder validation for prompt templates
- Dry-run rendering validation mode
- Optional mock mode for offline plumbing tests
- Final CSV rebuilt from the successful checkpoint records at the end of the run
  (or on demand with --rebuild-csv, without calling any model)

Usage (from the project root folder):
    python prompt/generate_responses.py                 # full run
    python prompt/generate_responses.py --limit 10      # first 10 skills only
    python prompt/generate_responses.py --dry-run       # render samples, no calls
    python prompt/generate_responses.py --mock          # offline plumbing test
    python prompt/generate_responses.py --rebuild-csv   # CSV from checkpoint only
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import string
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd
import yaml


PROMPT_REQUIRED_COLS = ["prompt_number", "system_prompt", "prompt_text"]
PROMPT_OUTPUT_META_COLS = [
    "prompt_number",
    "prompt_name",
    "prompt_category",
    "category_name",
    "bloom_level",
]
GENERATED_OUTPUT_COLS = [
    "system_prompt_rendered",
    "prompt_text",
    "llm",
    "litellm_model",
    "response_text",
    "datetime",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "time_taken_s",
    "cost",
    "finish_reason",
    "params_json",
    "error",
]

DEFAULT_CONFIG = Path(__file__).resolve().parent / "prompt_config.yaml"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_csv_str(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str).fillna("")


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


class PlaceholderValidationError(ValueError):
    pass


def _extract_placeholders(template: str) -> List[str]:
    placeholders: List[str] = []
    formatter = string.Formatter()

    try:
        parsed = formatter.parse(template)
    except ValueError as exc:
        raise PlaceholderValidationError(f"Template brace parsing failed: {exc}") from exc

    for _, field_name, _, _ in parsed:
        if field_name is None:
            continue
        if field_name == "":
            raise PlaceholderValidationError(
                "Found positional placeholder '{}' but only named placeholders are supported"
            )

        # Support simple attribute/index syntax by validating the root token.
        root = field_name.split(".", 1)[0].split("[", 1)[0]
        placeholders.append(root)

    return placeholders


def validate_placeholders(prompts_df: pd.DataFrame, skills_columns: List[str]) -> None:
    available = set(skills_columns)

    for _, prompt_row in prompts_df.iterrows():
        prompt_number = str(prompt_row.get("prompt_number", ""))

        for col in ("system_prompt", "prompt_text"):
            template = str(prompt_row.get(col, ""))
            try:
                placeholders = _extract_placeholders(template)
            except PlaceholderValidationError as exc:
                raise PlaceholderValidationError(
                    f"Prompt {prompt_number!r} column {col!r} is invalid: {exc}"
                ) from exc

            missing = [p for p in placeholders if p not in available]
            if missing:
                available_str = ", ".join(skills_columns)
                raise PlaceholderValidationError(
                    "Unknown placeholder(s) "
                    f"{missing} in prompt_number={prompt_number!r}, column={col!r}. "
                    f"Available skill columns: [{available_str}]"
                )


def render_template(template: str, values: Dict[str, Any], *, prompt_number: str, col_name: str) -> str:
    try:
        return template.format(**values)
    except Exception as exc:
        raise ValueError(
            f"Failed rendering prompt_number={prompt_number!r}, column={col_name!r}: {exc}"
        ) from exc


def extract_text_content(resp: Any) -> str:
    message = resp.choices[0].message
    content = getattr(message, "content", None)

    if content is None:
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
                if isinstance(item.get("text"), str):
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
    """Why the model stopped. 'length' means the answer was truncated."""
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
    # Different LiteLLM versions expose slightly different call signatures.
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


def unique_name(name: str, used: Set[str]) -> str:
    if name not in used:
        return name

    candidate = f"{name}_gen"
    if candidate not in used:
        return candidate

    i = 2
    while True:
        candidate = f"{name}_gen{i}"
        if candidate not in used:
            return candidate
        i += 1


def resolve_column_mapping(skills_cols: List[str], candidate_cols: Iterable[str]) -> Tuple[Dict[str, str], List[str]]:
    used = set(skills_cols)
    mapping: Dict[str, str] = {}
    warnings: List[str] = []

    for col in candidate_cols:
        resolved = unique_name(col, used)
        mapping[col] = resolved
        if resolved != col:
            warnings.append(
                f"Column collision: source has '{col}', generated column renamed to '{resolved}'"
            )
        used.add(resolved)

    return mapping, warnings


def is_success_checkpoint_record(rec: Dict[str, Any]) -> bool:
    if rec.get("status") == "success":
        return True
    # Backward-compatible fallback if status is absent.
    return ("response_text" in rec) and not rec.get("error")


def checkpoint_success_keys(path: Path) -> Set[Tuple[str, str, str]]:
    keys: Set[Tuple[str, str, str]] = set()
    if not path.exists():
        return keys

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue

            if not is_success_checkpoint_record(rec):
                continue

            skill_id = str(rec.get("skill_id", ""))
            prompt_number = str(rec.get("prompt_number", ""))
            model_name = str(rec.get("model", ""))
            if skill_id and prompt_number and model_name:
                keys.add((skill_id, prompt_number, model_name))

    return keys


def load_success_records(path: Path) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    if not path.exists():
        return by_key

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue

            if not is_success_checkpoint_record(rec):
                continue

            key = (
                str(rec.get("skill_id", "")),
                str(rec.get("prompt_number", "")),
                str(rec.get("model", "")),
            )
            if all(key):
                by_key[key] = rec

    return by_key


def maybe_log_ollama_digests(model_cfgs: List[Dict[str, Any]]) -> None:
    tags: List[str] = []
    for m in model_cfgs:
        litellm_model = str(m.get("litellm_model", ""))
        if litellm_model.startswith("ollama_chat/"):
            tags.append(litellm_model.split("/", 1)[1])

    if not tags:
        return

    if shutil.which("ollama") is None:
        print("Ollama digest check: 'ollama' CLI not found; skipping digest logging")
        return

    print("Ollama model digests (best effort):")
    for tag in tags:
        digest = None
        try:
            proc = subprocess.run(
                ["ollama", "show", tag, "--modelfile"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode == 0:
                # The FROM line points at the weights blob, whose file name holds
                # the digest: FROM /.../blobs/sha256-<hex>
                for line in proc.stdout.splitlines():
                    line = line.strip()
                    if not line.startswith("FROM "):
                        continue
                    m = re.search(r"sha256[:-]([0-9a-f]{12,64})", line)
                    if m:
                        digest = f"sha256:{m.group(1)}"
                        break
            print(f"  - {tag}: {digest or 'unavailable'}")
        except Exception as exc:
            print(f"  - {tag}: unavailable ({type(exc).__name__}: {exc})")


def build_mock_response(*, seed: int, skill_id: str, prompt_number: str, model_name: str) -> str:
    h = hashlib.md5(f"{seed}|{skill_id}|{prompt_number}|{model_name}".encode("utf-8")).hexdigest()[:12]
    return f"[MOCK_RESPONSE model={model_name} skill_id={skill_id} prompt={prompt_number} hash={h}]"


def format_eta(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m {sec}s"
    return f"{m}m {sec}s"


class OutputLayout:
    """Column layout of the responses CSV, shared by the appender and the rebuild.

    The layout is fixed once at startup so that rows appended during the run and
    rows written by the final rebuild always line up.
    """

    def __init__(self, *, skills_df: pd.DataFrame, prompts_df: pd.DataFrame, id_col: str) -> None:
        self.id_col = id_col
        self.skills_cols = list(skills_df.columns)
        self.prompt_cols = [c for c in PROMPT_OUTPUT_META_COLS if c == "prompt_number" or c in prompts_df.columns]

        self.prompt_col_map, prompt_warnings = resolve_column_mapping(self.skills_cols, self.prompt_cols)
        self.gen_col_map, gen_warnings = resolve_column_mapping(
            self.skills_cols + [self.prompt_col_map[c] for c in self.prompt_cols],
            GENERATED_OUTPUT_COLS,
        )

        for w in prompt_warnings + gen_warnings:
            print(f"WARNING: {w}")

        self.output_cols = (
            self.skills_cols
            + [self.prompt_col_map[c] for c in self.prompt_cols]
            + [self.gen_col_map[c] for c in GENERATED_OUTPUT_COLS]
        )

        self.skills_lookup: Dict[str, Dict[str, Any]] = {}
        for _, row in skills_df.iterrows():
            row_dict = row.to_dict()
            sid = str(row_dict.get(id_col, ""))
            if sid and sid not in self.skills_lookup:
                self.skills_lookup[sid] = row_dict

        self.prompts_lookup: Dict[str, Dict[str, Any]] = {}
        for _, row in prompts_df.iterrows():
            row_dict = row.to_dict()
            pnum = str(row_dict.get("prompt_number", ""))
            if pnum and pnum not in self.prompts_lookup:
                self.prompts_lookup[pnum] = row_dict

    def row_from_record(self, rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Flatten one successful checkpoint record. None if the skill is unknown."""
        skill_id = str(rec.get("skill_id", ""))
        prompt_number = str(rec.get("prompt_number", ""))

        skill_src = self.skills_lookup.get(skill_id)
        if skill_src is None:
            return None

        prompt_src = self.prompts_lookup.get(prompt_number, {})
        row: Dict[str, Any] = {}

        for c in self.skills_cols:
            row[c] = skill_src.get(c, "")

        for base_col in self.prompt_cols:
            if base_col == "prompt_number":
                val = prompt_number
            else:
                val = rec.get(base_col, prompt_src.get(base_col, ""))
            row[self.prompt_col_map[base_col]] = val

        generated_values = {
            "system_prompt_rendered": rec.get("system_prompt_rendered", ""),
            "prompt_text": rec.get("prompt_text_rendered", ""),
            "llm": rec.get("model", ""),
            "litellm_model": rec.get("litellm_model", ""),
            "response_text": rec.get("response_text", ""),
            "datetime": rec.get("datetime", rec.get("ts", "")),
            "prompt_tokens": rec.get("prompt_tokens"),
            "completion_tokens": rec.get("completion_tokens"),
            "total_tokens": rec.get("total_tokens"),
            "time_taken_s": rec.get("time_taken_s"),
            "cost": rec.get("cost"),
            "finish_reason": rec.get("finish_reason", ""),
            "params_json": rec.get("params_json", ""),
            "error": rec.get("error", ""),
        }

        for base_col, val in generated_values.items():
            row[self.gen_col_map[base_col]] = val

        return row


def append_output_row(output_csv: Path, layout: OutputLayout, rec: Dict[str, Any]) -> None:
    """Append one successful result to the CSV right away, so a crash loses nothing."""
    row = layout.row_from_record(rec)
    if row is None:
        return

    write_header = not output_csv.exists() or output_csv.stat().st_size == 0
    with output_csv.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=layout.output_cols, quoting=csv.QUOTE_ALL)
        if write_header:
            writer.writeheader()
        writer.writerow({c: ("" if row.get(c) is None else row.get(c)) for c in layout.output_cols})


def regenerate_output_csv(
    *,
    checkpoint_path: Path,
    layout: OutputLayout,
    output_csv: Path,
) -> Tuple[int, int]:
    """Rewrite the CSV from the checkpoint. Removes any duplicates left by appending."""
    success_records = list(load_success_records(checkpoint_path).values())

    rows: List[Dict[str, Any]] = []
    dropped_missing_skill = 0

    for rec in success_records:
        row = layout.row_from_record(rec)
        if row is None:
            dropped_missing_skill += 1
            continue
        rows.append(row)

    out_df = pd.DataFrame(rows, columns=layout.output_cols)
    out_df.to_csv(output_csv, index=False, encoding="utf-8", quoting=csv.QUOTE_ALL)

    return len(rows), dropped_missing_skill


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate LLM responses for skills × prompts × models")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Validate + render sample prompts only")
    parser.add_argument("--mock", action="store_true", help="Offline synthetic responses, no API calls")
    parser.add_argument("--verbose", action="store_true", help="Log each prompt/skill cell as it is processed")
    parser.add_argument(
        "--rebuild-csv",
        action="store_true",
        help="Rebuild the output CSV from the checkpoint and exit, without calling any model",
    )
    args = parser.parse_args()

    cfg_path = Path(args.config)
    cfg = load_yaml(cfg_path)

    run_cfg = cfg.get("run", {})
    all_models = cfg.get("models", [])
    model_cfgs = [m for m in all_models if m.get("enabled", True)]
    if not model_cfgs:
        raise SystemExit("No enabled models found in config")

    skills_csv = Path(run_cfg["skills_csv"])
    prompts_csv = Path(run_cfg["prompts_csv"])
    output_csv = Path(run_cfg["output_csv"])
    checkpoint_path = Path(run_cfg["checkpoint"])
    verbose_log_path = Path(run_cfg.get("verbose_log", "verbose.log"))
    id_col = str(run_cfg.get("id_col", "conceptUri"))
    max_retries = int(run_cfg.get("max_retries", 2))

    if not skills_csv.exists():
        raise SystemExit(
            f"Skills CSV not found: {skills_csv}\n"
            "Step 4 runs on the skills chosen in Step 3. Open select/skill_selector.html,\n"
            "export the selected rows and save them at that path, then run this again."
        )
    if not prompts_csv.exists():
        raise SystemExit(f"Prompts CSV not found: {prompts_csv}\nRun: python prompt/build_prompts.py")

    full_skills_df = read_csv_str(skills_csv)
    prompts_df = read_csv_str(prompts_csv)

    if id_col not in full_skills_df.columns:
        raise SystemExit(f"Configured id_col {id_col!r} is not present in skills CSV")

    missing_prompt_cols = [c for c in PROMPT_REQUIRED_COLS if c not in prompts_df.columns]
    if missing_prompt_cols:
        raise SystemExit(f"Prompts CSV is missing required columns: {missing_prompt_cols}")

    validate_placeholders(prompts_df, list(full_skills_df.columns))

    for p in (output_csv, checkpoint_path, verbose_log_path):
        p.parent.mkdir(parents=True, exist_ok=True)

    layout = OutputLayout(skills_df=full_skills_df, prompts_df=prompts_df, id_col=id_col)

    if args.rebuild_csv:
        written_rows, dropped = regenerate_output_csv(
            checkpoint_path=checkpoint_path, layout=layout, output_csv=output_csv
        )
        print(f"Rebuilt {output_csv} from {checkpoint_path}: {written_rows} success rows")
        if dropped:
            print(f"WARNING: dropped {dropped} rows whose skill_id is not in the current skills CSV")
        return

    skills_df = full_skills_df.head(args.limit) if args.limit is not None else full_skills_df

    skills_records = skills_df.to_dict(orient="records")
    prompt_records = prompts_df.to_dict(orient="records")

    done_success = checkpoint_success_keys(checkpoint_path)

    scope_total = len(skills_records) * len(prompt_records) * len(model_cfgs)
    done_in_scope = 0
    for model in model_cfgs:
        model_name = str(model.get("name", ""))
        for prompt in prompt_records:
            pnum = str(prompt.get("prompt_number", ""))
            for skill in skills_records:
                sid = str(skill.get(id_col, ""))
                if (sid, pnum, model_name) in done_success:
                    done_in_scope += 1

    print(
        f"Skills={len(skills_records)} | Prompts={len(prompt_records)} | "
        f"Enabled models={len(model_cfgs)} => {scope_total} total cells"
    )
    print(f"Already successful in checkpoint (within this scope): {done_in_scope}")

    if args.dry_run:
        print("\nDRY RUN: validating and rendering sample prompts (no API calls)")
        shown = 0
        for model in model_cfgs:
            model_name = str(model.get("name", ""))
            for prompt in prompt_records:
                pnum = str(prompt.get("prompt_number", ""))
                for skill in skills_records:
                    values = {k: "" if v is None else str(v) for k, v in skill.items()}
                    sid = str(values.get(id_col, ""))

                    sys_rendered = render_template(
                        str(prompt.get("system_prompt", "")),
                        values,
                        prompt_number=pnum,
                        col_name="system_prompt",
                    )
                    usr_rendered = render_template(
                        str(prompt.get("prompt_text", "")),
                        values,
                        prompt_number=pnum,
                        col_name="prompt_text",
                    )

                    print("-" * 80)
                    print(f"model={model_name} | prompt_number={pnum} | skill_id={sid}")
                    print(f"system_prompt_rendered: {sys_rendered!r}")
                    print("prompt_text_rendered:")
                    print(usr_rendered)
                    shown += 1
                    if shown >= 5:
                        print("-" * 80)
                        print(f"Dry-run preview complete ({shown} sample cells shown).")
                        print("Note: Use '{{' and '}}' in prompt templates for literal braces.")
                        return

        print("Dry-run complete.")
        print("Note: Use '{{' and '}}' in prompt templates for literal braces.")
        return

    litellm = None
    litellm_version = None
    if not args.mock:
        import litellm as _litellm

        litellm = _litellm
        litellm_version = getattr(_litellm, "__version__", "unknown")
        maybe_log_ollama_digests(model_cfgs)
    else:
        print("MOCK mode enabled: no API calls will be made")

    default_params = run_cfg.get("default_params", {}) or {}

    model_stats: Dict[str, Dict[str, Any]] = {
        str(m.get("name", "")): {
            "success": 0,
            "failure": 0,
            "skipped": 0,
            "truncated": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost": 0.0,
        }
        for m in model_cfgs
    }

    started_cells = 0
    failures_total = 0
    run_start = time.perf_counter()

    # Start from a CSV that matches the current layout and already holds every
    # earlier success, then append new rows as they arrive.
    regenerate_output_csv(checkpoint_path=checkpoint_path, layout=layout, output_csv=output_csv)

    for model in model_cfgs:
        model_name = str(model["name"])
        litellm_model = str(model["litellm_model"])
        api_base = model.get("api_base")

        print(
            f"\nStarting model run: name={model_name}, litellm_model={litellm_model}"
            + (f", api_base={api_base}" if api_base else "")
        )

        model_params = model.get("params", {}) or {}
        effective_params = {**default_params, **model_params}
        effective_params = {k: v for k, v in effective_params.items() if v is not None}

        for prompt in prompt_records:
            prompt_number = str(prompt.get("prompt_number", ""))

            for skill in skills_records:
                skill_values = {k: "" if v is None else str(v) for k, v in skill.items()}
                skill_id = str(skill_values.get(id_col, ""))
                cell_key = (skill_id, prompt_number, model_name)

                if args.verbose:
                    print(
                        f"Processing cell: model={model_name}, prompt_number={prompt_number}, skill_id={skill_id}"
                    )

                if cell_key in done_success:
                    model_stats[model_name]["skipped"] += 1
                    if args.verbose:
                        print("  -> skipped (already successful in checkpoint)")
                    continue

                started_cells += 1
                sys_template = str(prompt.get("system_prompt", ""))
                usr_template = str(prompt.get("prompt_text", ""))

                system_prompt_rendered = render_template(
                    sys_template,
                    skill_values,
                    prompt_number=prompt_number,
                    col_name="system_prompt",
                )
                user_prompt_rendered = render_template(
                    usr_template,
                    skill_values,
                    prompt_number=prompt_number,
                    col_name="prompt_text",
                )

                messages: List[Dict[str, str]] = []
                if system_prompt_rendered.strip() != "":
                    messages.append({"role": "system", "content": system_prompt_rendered})
                messages.append({"role": "user", "content": user_prompt_rendered})

                checkpoint_record: Optional[Dict[str, Any]] = None

                for attempt in range(1, max_retries + 2):
                    ts_start = utc_now_iso()
                    t0 = time.perf_counter()

                    usage = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
                    cost = None
                    finish_reason = ""
                    response_text = ""
                    status = "success"
                    error_info: Optional[Dict[str, Any]] = None

                    request_payload = {
                        "messages": messages,
                        "params": effective_params,
                        "api_base": api_base,
                        "mock": bool(args.mock),
                    }

                    try:
                        if args.mock:
                            seed = int(effective_params.get("seed", 42))
                            response_text = build_mock_response(
                                seed=seed,
                                skill_id=skill_id,
                                prompt_number=prompt_number,
                                model_name=model_name,
                            )
                            cost = 0.0
                        else:
                            assert litellm is not None

                            kwargs: Dict[str, Any] = {
                                "model": litellm_model,
                                "messages": messages,
                            }
                            kwargs.update(effective_params)
                            if api_base:
                                kwargs["api_base"] = api_base

                            api_key_env = model.get("api_key_env")
                            if api_key_env:
                                api_key = os.getenv(str(api_key_env))
                                if not api_key:
                                    raise RuntimeError(
                                        f"Missing environment variable '{api_key_env}' for model '{model_name}'"
                                    )
                                kwargs["api_key"] = api_key

                            use_drop_params = "gemini" in litellm_model.lower() or "gemini" in model_name.lower()
                            prev_drop = getattr(litellm, "drop_params", None)
                            try:
                                if use_drop_params:
                                    litellm.drop_params = True
                                resp = litellm.completion(**kwargs)
                            finally:
                                if use_drop_params:
                                    litellm.drop_params = prev_drop

                            response_text = extract_text_content(resp)
                            usage = extract_usage(resp)
                            cost = compute_cost(litellm, resp)
                            finish_reason = extract_finish_reason(resp)

                        dt = time.perf_counter() - t0
                        checkpoint_record = {
                            "status": "success",
                            "skill_id": skill_id,
                            "prompt_number": prompt_number,
                            "prompt_name": str(prompt.get("prompt_name", "")),
                            "prompt_category": str(prompt.get("prompt_category", "")),
                            "category_name": str(prompt.get("category_name", "")),
                            "bloom_level": str(prompt.get("bloom_level", "")),
                            "variation": str(prompt.get("variation", "")),
                            "source": str(prompt.get("source", "")),
                            "model": model_name,
                            "litellm_model": litellm_model,
                            "system_prompt_rendered": system_prompt_rendered,
                            "prompt_text_rendered": user_prompt_rendered,
                            "response_text": response_text,
                            "datetime": utc_now_iso(),
                            "prompt_tokens": usage.get("prompt_tokens"),
                            "completion_tokens": usage.get("completion_tokens"),
                            "total_tokens": usage.get("total_tokens"),
                            "time_taken_s": round(dt, 6),
                            "cost": cost,
                            "finish_reason": finish_reason,
                            "params_json": json.dumps(effective_params, ensure_ascii=False, sort_keys=True),
                            "error": "",
                            "mock": bool(args.mock),
                            "litellm_version": litellm_version,
                        }

                        if usage.get("prompt_tokens") is not None:
                            model_stats[model_name]["prompt_tokens"] += int(usage["prompt_tokens"])
                        if usage.get("completion_tokens") is not None:
                            model_stats[model_name]["completion_tokens"] += int(usage["completion_tokens"])
                        if usage.get("total_tokens") is not None:
                            model_stats[model_name]["total_tokens"] += int(usage["total_tokens"])
                        if cost is not None:
                            model_stats[model_name]["cost"] += float(cost)

                        if finish_reason == "length":
                            model_stats[model_name]["truncated"] += 1

                        model_stats[model_name]["success"] += 1
                        done_success.add(cell_key)

                    except Exception as exc:
                        status = "error"
                        error_info = {
                            "type": type(exc).__name__,
                            "message": str(exc),
                        }

                        if attempt == (max_retries + 1):
                            failures_total += 1
                            model_stats[model_name]["failure"] += 1
                            checkpoint_record = {
                                "status": "error",
                                "skill_id": skill_id,
                                "prompt_number": prompt_number,
                                "prompt_name": str(prompt.get("prompt_name", "")),
                                "prompt_category": str(prompt.get("prompt_category", "")),
                                "category_name": str(prompt.get("category_name", "")),
                                "bloom_level": str(prompt.get("bloom_level", "")),
                                "variation": str(prompt.get("variation", "")),
                                "source": str(prompt.get("source", "")),
                                "model": model_name,
                                "litellm_model": litellm_model,
                                "error": str(exc)[:2000],
                                "datetime": utc_now_iso(),
                                "mock": bool(args.mock),
                                "litellm_version": litellm_version,
                            }

                    duration_ms = int((time.perf_counter() - t0) * 1000)
                    ts_end = utc_now_iso()

                    verbose_record = {
                        "ts_start": ts_start,
                        "ts_end": ts_end,
                        "duration_ms": duration_ms,
                        "skill_id": skill_id,
                        "prompt_number": prompt_number,
                        "model": model_name,
                        "litellm_model": litellm_model,
                        "attempt": attempt,
                        "request": request_payload,
                        "response_raw": response_text,
                        "usage": usage,
                        "cost_usd": cost,
                        "finish_reason": finish_reason,
                        "status": status,
                        "error": error_info,
                        "mock": bool(args.mock),
                        "litellm_version": litellm_version,
                    }
                    append_jsonl(verbose_log_path, verbose_record)

                    if status == "success":
                        break

                    if attempt < (max_retries + 1):
                        time.sleep(min(20, 2 ** (attempt - 1)))

                if checkpoint_record is not None:
                    append_jsonl(checkpoint_path, checkpoint_record)
                    if checkpoint_record.get("status") == "success":
                        append_output_row(output_csv, layout, checkpoint_record)

                # Progress update every 25 processed cells (or last cell).
                processed_or_done = done_in_scope + started_cells
                if started_cells % 25 == 0 or processed_or_done >= scope_total:
                    elapsed = time.perf_counter() - run_start
                    throughput = (started_cells / elapsed) if elapsed > 0 else 0.0
                    remaining = max(0, scope_total - processed_or_done)
                    eta = (remaining / throughput) if throughput > 0 else None
                    print(
                        f"Progress: success={len(done_success)}/{scope_total}, "
                        f"processed_this_run={started_cells}, failures={failures_total}, "
                        f"elapsed={format_eta(elapsed)}, eta={format_eta(eta)}"
                    )

    written_rows, dropped_missing_skill = regenerate_output_csv(
        checkpoint_path=checkpoint_path,
        layout=layout,
        output_csv=output_csv,
    )

    print("\nRun complete")
    print(f"Output CSV: {output_csv} ({written_rows} success rows)")
    if dropped_missing_skill:
        print(f"WARNING: dropped {dropped_missing_skill} success rows due to missing skill_id in current skills CSV")

    print(f"Checkpoint: {checkpoint_path}")
    print(f"Verbose log: {verbose_log_path}")
    print(f"Failures in this run: {failures_total}")

    print("\nPer-model summary (this run):")
    for model in model_cfgs:
        name = str(model["name"])
        st = model_stats[name]
        print(
            f"  - {name}: success={st['success']} failure={st['failure']} skipped={st['skipped']} "
            f"truncated={st['truncated']} "
            f"tokens(prompt/completion/total)={st['prompt_tokens']}/{st['completion_tokens']}/{st['total_tokens']} "
            f"cost_usd={st['cost']:.6f}"
        )

    truncated_total = sum(st["truncated"] for st in model_stats.values())
    if truncated_total:
        print(
            f"\nWARNING: {truncated_total} answers stopped with finish_reason='length' "
            f"(cut off). Check the finish_reason column in {output_csv}."
        )


if __name__ == "__main__":
    main()
