"""The Step 5 rubric, in one place. DRAFT, the wording is provisional.

Both the human raters (evaluate/rate.py, the Rate answers tab) and the LLM judge
(evaluate/judge.py) score the same four criteria on the same 1 to 5 scale, so the
two kinds of rating can be compared directly.

Change the criteria or the anchors here only.
"""

from typing import Optional

from pydantic import BaseModel, Field

CRITERIA = ["clarity", "depth", "relevance", "pedagogical"]

CRITERION_TEXT = {
    "clarity": "understandable, well structured, accessible to the intended reader",
    "depth": "connects the skill to real context and to sustainability reasoning",
    "relevance": "actually answers about the intended ESCO skill, without drifting",
    "pedagogical": "guides the learner with steps, examples and reasoning",
}

# The anchors are what keep a judge model from drifting to a flat 4 for everything.
SCALE = """1 = unusable: wrong, empty, or off-topic
2 = weak: some correct content but major gaps or errors
3 = acceptable: correct and usable, nothing more
4 = good: correct, well organised, some real insight
5 = excellent: accurate, well structured, genuinely instructive"""

JUDGE_SYSTEM_PROMPT = """You are grading teaching material about a "green skill" from the EU ESCO database.

You will be given the skill, the question that was asked, and one answer written by a language model. Score the answer on four criteria, each from 1 to 5.

Criteria:
- clarity: {clarity}
- depth: {depth}
- relevance: {relevance}
- pedagogical: {pedagogical}

Scale:
{scale}

Grade only what is in front of you. Do not reward length, confident tone, or formatting. An answer that is fluent but says nothing concrete about the skill scores low on depth and relevance. You do not know which model wrote the answer, and it does not matter.

Each of the four criteria gets one integer from 1 to 5. Never put words where a number belongs.

Reply with a JSON object and nothing else.""".format(scale=SCALE, **CRITERION_TEXT)

# No filled-in example here, on purpose. An earlier version showed a valid reply with
# concrete numbers, and the small models copied those numbers instead of grading:
# one judge returned the example's clarity, relevance and pedagogical values for all
# 48 answers. The shape is therefore described with placeholders only.
JSON_FALLBACK_INSTRUCTION = """

Reply with only a JSON object, no prose and no code fences. It must contain exactly
these five keys:

  "clarity"      an integer from 1 to 5
  "depth"        an integer from 1 to 5
  "relevance"    an integer from 1 to 5
  "pedagogical"  an integer from 1 to 5
  "rationale"    one short sentence

All four scores must be present, and each must be a bare integer, never a word and
never a sentence. Put all your wording in "rationale" and nowhere else. Choose each
score by reading this specific answer against the scale above; do not reuse a
default value."""


def build_user_prompt(*, skill: str, description: str, question: str, answer: str) -> str:
    parts = [f"SKILL: {skill}"]
    if description:
        parts.append(f"SKILL DESCRIPTION: {description}")
    parts.append(f"QUESTION ASKED: {question}")
    parts.append(f"ANSWER TO GRADE:\n{answer}")
    return "\n\n".join(parts)


class AnswerRating(BaseModel):
    """One judge's scores for one answer."""

    rationale: str = Field(description="One short sentence explaining the scores")
    clarity: int = Field(ge=1, le=5)
    depth: int = Field(ge=1, le=5)
    relevance: int = Field(ge=1, le=5)
    pedagogical: int = Field(ge=1, le=5)

    def scores(self) -> dict:
        return {c: getattr(self, c) for c in CRITERIA}


class RatingParseError(ValueError):
    pass


def parse_rating(raw_text: str) -> AnswerRating:
    """Read an AnswerRating out of whatever the model replied with."""
    import json
    import re

    txt = raw_text.strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\s*```$", "", txt).strip()

    start = txt.find("{")
    if start == -1:
        raise RatingParseError("no JSON object in the reply")

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(txt)):
        ch = txt[i]
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
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                obj = txt[start:i + 1]
                break
    else:
        raise RatingParseError("unbalanced JSON object in the reply")

    try:
        data = json.loads(obj)
    except Exception as exc:
        raise RatingParseError(f"invalid JSON: {exc}") from exc

    # small models often answer "4/5" or "four", so accept a leading integer
    for c in CRITERIA:
        v = data.get(c)
        if isinstance(v, str):
            m = re.search(r"\d+", v)
            if m:
                data[c] = int(m.group())
        elif isinstance(v, float):
            data[c] = round(v)

    data.setdefault("rationale", "")
    try:
        return AnswerRating.model_validate(data)
    except Exception as exc:
        raise RatingParseError(str(exc)) from exc
