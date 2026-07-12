"""
Enhance ESCO's Green Skills Collection with pillar/category information
by traversing the ESCO skills-hierarchy graph.

Inputs (downloaded from the ESCO portal, https://esco.ec.europa.eu):
  - greenSkillsCollection_en.csv       : the ~630 skills flagged isGreenSkill
  - broaderRelationsSkillPillar_en.csv : edge list (conceptUri -> broaderUri)
                                          for the whole ESCO skills pillar
  - skillsHierarchy_en.csv             : Level 0-3 SkillGroup nodes with
                                          their official codes and labels

Output:
  - greenSkillsCollection_enhanced.csv : original columns + 6 new columns
                                          describing each skill's ESCO
                                          pillar/category at up to 4 levels
"""

import pandas as pd

UPLOAD_DIR = "../data/esco/"   # raw ESCO input CSVs
OUT_DIR = "../data/"           # enhanced output CSV

gs = pd.read_csv(f"{UPLOAD_DIR}/greenSkillsCollection_en.csv")
br = pd.read_csv(f"{UPLOAD_DIR}/broaderRelationsSkillPillar_en.csv")
sh = pd.read_csv(f"{UPLOAD_DIR}/skillsHierarchy_en.csv")

# ---------------------------------------------------------------------------
# 1. Build a URI -> (level, code, label) lookup from the skills hierarchy.
#    skillsHierarchy_en.csv stores Level 0-3 columns side by side per row;
#    we flatten that into a single dict keyed by URI.
# ---------------------------------------------------------------------------
uri_info = {}
for _, row in sh.iterrows():
    for lvl in range(4):
        uri = row.get(f"Level {lvl} URI")
        code = row.get(f"Level {lvl} code")
        label = row.get(f"Level {lvl} preferred term")
        if pd.notna(uri) and uri not in uri_info:
            uri_info[uri] = (lvl, code, label)

# ---------------------------------------------------------------------------
# 2. Build the broader-relation adjacency list: conceptUri -> [broaderUri, ...]
#    A concept can have more than one broader parent (branching hierarchy).
# ---------------------------------------------------------------------------
br_map = {}
for _, row in br.iterrows():
    br_map.setdefault(row["conceptUri"], []).append(row["broaderUri"])

# ---------------------------------------------------------------------------
# 3. Recursively climb from a skill's URI up through every broader-parent
#    chain, collecting every ancestor node that resolves to a Level 0-3
#    hierarchy entry. `seen` prevents infinite loops if the graph has cycles
#    or multiple branches re-converge on the same ancestor.
# ---------------------------------------------------------------------------
def climb(uri, seen=None, out=None):
    if seen is None:
        seen = set()
    if out is None:
        out = []
    if uri in seen:
        return out
    seen.add(uri)
    if uri in uri_info:
        out.append(uri_info[uri])
    for parent_uri in br_map.get(uri, []):
        climb(parent_uri, seen, out)
    return out


def format_level(entries, lvl):
    """Deduplicate and format all ancestor nodes found at a given level."""
    items = sorted({(code, label) for (l, code, label) in entries if l == lvl})
    return " | ".join(f"{code} - {label}" for code, label in items)


# ---------------------------------------------------------------------------
# 4. Run the traversal for every green skill and assemble the new columns.
# ---------------------------------------------------------------------------
rows_l0, rows_l1, rows_l2, rows_l3 = [], [], [], []
rows_root_pillar, rows_npillars = [], []

for _, row in gs.iterrows():
    entries = climb(row["conceptUri"])
    rows_l0.append(format_level(entries, 0))
    rows_l1.append(format_level(entries, 1))
    rows_l2.append(format_level(entries, 2))
    rows_l3.append(format_level(entries, 3))

    roots = sorted({label for (l, code, label) in entries if l == 0})
    rows_root_pillar.append(" | ".join(roots))
    rows_npillars.append(len({code for (l, code, label) in entries if l == 0}))

gs_out = gs.copy()
gs_out["escoPillar"] = rows_root_pillar            # e.g. "skills"
gs_out["escoLevel0Pillar"] = rows_l0               # e.g. "S - skills"
gs_out["escoLevel1Category"] = rows_l1             # e.g. "S1 - communication, collaboration..."
gs_out["escoLevel2Category"] = rows_l2             # e.g. "S1.3 - teaching and training"
gs_out["escoLevel3Category"] = rows_l3             # e.g. "S1.3.3 - training on operational procedures"
gs_out["escoNumPillars"] = rows_npillars            # how many root pillars this skill spans

out_path = f"{OUT_DIR}/greenSkillsCollection_enhanced.csv"
gs_out.to_csv(out_path, index=False)
print(f"Saved {len(gs_out)} rows to {out_path}")
