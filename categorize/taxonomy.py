"""
Single source of truth for Phase C taxonomy, schema, and prompts.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PrimaryCategory(str, Enum):
    G1 = "G1"
    G2 = "G2"
    G3 = "G3"
    G4 = "G4"
    G5 = "G5"
    G6 = "G6"
    G7 = "G7"
    G8 = "G8"


SUBSTANTIVE = {
    PrimaryCategory.G1.value,
    PrimaryCategory.G2.value,
    PrimaryCategory.G3.value,
    PrimaryCategory.G4.value,
    PrimaryCategory.G5.value,
    PrimaryCategory.G6.value,
}


class SkillLabel(BaseModel):
    # Field order intentionally follows spec.
    rationale: str = Field(description="One short sentence")
    primary_category: PrimaryCategory
    education_overlay: bool
    secondary_category: Optional[PrimaryCategory] = None
    confidence: float = Field(ge=0.0, le=1.0)


CLASSIFICATION_CARD = """G1 Renewable Energy & Energy Systems — generating renewable energy + storage/grid (solar, wind, biomass, geothermal, hydro, hydrogen, batteries, smart grid).
G2 Energy Efficiency & Built Environment — reducing energy demand + green building/construction/retrofit (audits, heat pumps, insulation, HVAC, installation).
G3 Circular Economy, Waste & Resource — reduce/reuse/recycle/recover; waste streams; resource & water efficiency; sustainable materials & textiles.
G4 Environmental Protection & Natural Capital — protect air/water/soil/ecosystems/wildlife; pollution control, remediation, biodiversity, climate impact.
G5 Sustainable Agriculture, Forestry & Food — sustainable crops/livestock/forestry/fisheries; soil & land management; food systems & food-waste.
G6 Sustainable Mobility & Green Manufacturing — low-impact transport/mobility + clean/sustainable industrial production.
G7 Green Management, Policy, Finance & Governance — environmental law/compliance/permits, policy, economics, ESG reporting, green finance, strategy, advisory.
G8 Green Technology, Data & Digital Innovation — computing/data/sensing/AI applied to sustainability (green IT, environmental data, IoT, monitoring tech)."""


SYSTEM_PROMPT = """You are an expert classifier of green skills for environmental-education research.
Classify each skill into exactly ONE primary category from the taxonomy provided.
Use ONLY the given taxonomy and follow the tie-break rules.
Tie-break (when several fit): a substantive domain (G1-G6) beats governance (G7) and
technology (G8); a skill defined by physical construction/installation is G2 (or G1 if
it generates energy). Judge by the skill OUTCOME, not surface wording.
Separately set education_overlay=true only when the skill's core act is teaching,
training, or awareness-raising. Give a one-sentence rationale and a confidence in [0,1].
Return ONLY the structured object; no extra text."""


JSON_FALLBACK_INSTRUCTION = (
    "\n\nReturn ONLY a JSON object with keys exactly: "
    '"rationale" (string), '
    '"primary_category" (one of G1..G8), '
    '"education_overlay" (true/false), '
    '"secondary_category" (one of G1..G8 or null), '
    '"confidence" (0..1). '
    "No markdown, no text before or after the JSON."
)
 

def build_user_prompt(preferred_label: str, description: str, alt_labels: str) -> str:
    """Build the task-specific user prompt."""
    return (
        "Taxonomy:\n"
        f"{CLASSIFICATION_CARD}\n\n"
        "Skill to classify:\n"
        f"- label: {preferred_label or '(none)'}\n"
        f"- description: {description or '(none)'}\n"
        f"- alternative labels: {alt_labels or '(none)'}\n\n"
        "Classify it now."
    )
