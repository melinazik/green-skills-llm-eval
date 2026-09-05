"""The Step 5 rubric, in one place. DRAFT, the wording is provisional.

Both the human raters (evaluate/rate.py, the Rate answers tab) and the LLM judge
(evaluate/judge.py) score the same four quality dimensions.
"""

from typing import Any

from pydantic import BaseModel, Field

CRITERIA = ["coherence_clarity", "consistency_accuracy", "relevance_esco_alignment", "educational_value"]

JUDGE_SYSTEM_PROMPT = """You are an expert evaluator of AI-generated educational content about green (environmental sustainability) skills. Your task is to score a single response on four rubric dimensions: Coherence/Clarity, Consistency/Accuracy, Relevance/Alignment with ESCO, and Educational Value.
Work through the dimensions in the order listed below. For each dimension: first briefly reason about the response against that dimension's criteria only (2–3 sentences), then assign a single integer score from 1 to 5 using ONLY that dimension's rubric. Do not use half-points or scores outside this range.
Score each dimension independently, using only its own listed criteria. Do not let your assessment of one dimension influence another, and do not form or report an overall/aggregate impression - only the four separate dimension scores are needed. Do not let response length by itself drive any score, except where a dimension's own criteria explicitly mention length or proportionality. You do not know which model produced this response and must not attempt to infer or guess its source.

Rubric - Dimension 1: Coherence/Clarity
Score 5: Clear and logically ordered; terminology defined at first use; answer matches the audience named in the prompt; length proportionate to the question; no unexplained jargon.
Score 4: Clear and well organised, with one or two undefined terms, a minor slip, or mild redundancy.
Score 3: Understandable with effort: loose organisation, several unexplained technical terms, or answer mismatched to the named audience (e.g. use of complex terms for a stated beginner).
Score 2: Hard to follow throughout: disorganised, padded, repetitive, or consistently pitched at the wrong level.
Score 1: Incoherent or unusable as an explanation.

Rubric - Dimension 2: Consistency/Accuracy
Score 5: All major claims are correct; technical terms, standards, regulations, units and figures are used correctly; simplifications are legitimate, not misleading.
Score 4: Substantively correct; at most one minor imprecision, e.g. loose terminology, slightly dated figure, that would not mislead a learner.
Score 3: Core is overall correct but contains one clear factual error, or several imprecisions. A learner would acquire a partly wrong picture.
Score 2: Multiple factual errors, or one error central to the skill itself; the core message is compromised.
Score 1: Predominantly incorrect, or contains fabricated specifics (invented standards, regulations, figures, organisations, or citations).

Rubric - Dimension 3: Relevance/Alignment with ESCO
Score 5: Addresses precisely the ESCO skill as defined, at the correct scope; where confusion with an adjacent skill is likely, the boundary is made explicit.
Score 4: Addresses the intended skill; scope drifts modestly wider or narrower (e.g. treats the parent domain rather than the specific skill) without distorting it.
Score 3: Recognisably about the intended skill, but substantially mixed with a broader domain or an adjacent skill; a significant part of the content is off-target.
Score 2: Mostly about something else; the intended skill appears only incidentally.
Score 1: Off-topic, or the label is misread (wrong sense of a term with multiple meanings, wrong domain entirely).

Rubric - Dimension 4: Educational Value
Score 5: Actively teaches: builds from prior/everyday knowledge, gives at least one concrete worked example, sequences the content, anticipates a likely misconception, and (where the prompt allows) gives the learner something to do or a way to check understanding.
Score 4: Teaches well: at least one concrete example plus clear sequencing, but no misconception handling and no check of understanding.
Score 3: Informs rather than teaches: content is correct but examples are generic or absent and there is little structure a learner could follow.
Score 2: Definition or list dump: no example, no sequencing, no learner support.
Score 1: No instructional intent at all.
Return ONLY the structured object; no extra text."""


def build_user_prompt(*, skill: str, description: str, bloom_level: str,
                      question: str, answer: str) -> str:
    desc = description.strip() if description else ""
    desc = desc if desc else "(none)"

    return (
        f"Skill being explained: {skill}\n\n"
        f"Skill description (ESCO): {desc}\n\n"
        f"Prompt category (Bloom level): {bloom_level}\n\n"
        f"Original question given to the model: {question}\n\n"
        "Response to evaluate:\n"
        f"{answer}\n"
        "Evaluate the response now, dimension by dimension, in the order given.\n"
        "output_schema:\n"
        "coherence_clarity: {reasoning: str, score: int 1-5}\n"
        "consistency_accuracy: {reasoning: str, score: int 1-5}\n"
        "relevance_esco_alignment: {reasoning: str, score: int 1-5}\n"
        "educational_value: {reasoning: str, score: int 1-5}\n"
        "overall_confidence: float in [0,1]\n\n"
        "JSON fallback: Return ONLY a JSON object with keys exactly: "
        '"coherence_clarity", "consistency_accuracy", "relevance_esco_alignment", '
        '"educational_value" (each an object with "reasoning" and "score"), '
        'and "overall_confidence" (0..1). No markdown, no text before or after the JSON.'
    )


class DimensionRating(BaseModel):
    reasoning: str = Field(default="")
    score: int = Field(ge=1, le=5)


class AnswerRating(BaseModel):
    """One judge's structured scores for one answer."""

    coherence_clarity: DimensionRating
    consistency_accuracy: DimensionRating
    relevance_esco_alignment: DimensionRating
    educational_value: DimensionRating
    overall_confidence: float = Field(ge=0.0, le=1.0)

    @property
    def rationale(self) -> str:
        parts = [
            self.coherence_clarity.reasoning,
            self.consistency_accuracy.reasoning,
            self.relevance_esco_alignment.reasoning,
            self.educational_value.reasoning,
        ]
        parts = [" ".join(str(p).split()) for p in parts if str(p).strip()]
        return " | ".join(parts)

    def scores(self) -> dict:
        return {
            "coherence_clarity": self.coherence_clarity.score,
            "consistency_accuracy": self.consistency_accuracy.score,
            "relevance_esco_alignment": self.relevance_esco_alignment.score,
            "educational_value": self.educational_value.score,
        }


class RatingParseError(ValueError):
    pass


def _extract_json_object(txt: str) -> str:
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
                return txt[start:i + 1]

    raise RatingParseError("unbalanced JSON object in the reply")


def _as_int_score(value: Any) -> Any:
    import re

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        if m:
            return int(m.group())
    return value


def _normalize_dimension(value: Any) -> Any:
    if not isinstance(value, dict):
        return value

    score = _as_int_score(value.get("score"))
    reasoning = value.get("reasoning", "")
    return {"reasoning": "" if reasoning is None else str(reasoning), "score": score}


def _normalize_confidence(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return float(value)
    return value


def parse_rating(raw_text: str) -> AnswerRating:
    """Read an AnswerRating out of whatever the model replied with."""
    import json
    import re

    txt = raw_text.strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\s*```$", "", txt).strip()

    obj = _extract_json_object(txt)

    try:
        data = json.loads(obj)
    except Exception as exc:
        raise RatingParseError(f"invalid JSON: {exc}") from exc

    data["coherence_clarity"] = _normalize_dimension(data.get("coherence_clarity"))
    data["consistency_accuracy"] = _normalize_dimension(data.get("consistency_accuracy"))
    data["relevance_esco_alignment"] = _normalize_dimension(data.get("relevance_esco_alignment"))
    data["educational_value"] = _normalize_dimension(data.get("educational_value"))
    data["overall_confidence"] = _normalize_confidence(data.get("overall_confidence"))

    try:
        return AnswerRating.model_validate(data)
    except Exception as exc:
        raise RatingParseError(str(exc)) from exc
