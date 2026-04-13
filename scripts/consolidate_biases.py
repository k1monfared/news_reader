"""One-off script to consolidate similar biases in source_biases.json.

Uses theme-based keyword grouping: biases matching the same theme keywords
get merged into a single entry. Biases that don't match any theme stay as-is.
All original pattern names and details are preserved in the merged entry.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

BIASES_PATH = Path(__file__).resolve().parent.parent / "docs" / "_data" / "source_biases.json"
BACKUP_PATH = BIASES_PATH.with_suffix(".json.bak")

# Theme definitions: each theme has keywords that match against pattern + detail text.
# A bias matches a theme if it contains enough keywords from that theme.
THEMES = {
    "attribution_framing_asymmetry": {
        "label": "Attribution and framing asymmetry",
        "keywords": ["attribution", "framing", "framing asymmetry", "selective", "asymmetric",
                      "asymmetry", "language", "nomenclature", "naming", "labels", "labeling",
                      "fusion", "attribution fusion"],
        "min_matches": 1,
    },
    "speculation_amplification": {
        "label": "Speculation and amplification",
        "keywords": ["speculation", "speculative", "amplification", "amplifying", "dramatization",
                      "dramatizing", "countdown", "countdown dramatization", "prediction",
                      "inevitability", "pre-positioning", "pre-accusation", "questioning"],
        "min_matches": 1,
    },
    "casualty_victimization": {
        "label": "Casualty and victimization emphasis",
        "keywords": ["casualty", "casualties", "victim", "victimization", "humanitarian",
                      "humanization", "orphan", "children", "child", "civilian", "death",
                      "disfigurement", "trauma", "rescue", "evacuation", "displacement"],
        "min_matches": 1,
    },
    "economic_crisis_escalation": {
        "label": "Economic crisis escalation framing",
        "keywords": ["economic", "oil", "crude", "energy", "market", "price", "inflation",
                      "crisis", "catastrophization", "recession", "stagflation", "fuel",
                      "commodity", "trade", "sanctions", "insurance", "currency", "rial",
                      "deposit", "deficit", "financial", "cost", "budget", "subsidy",
                      "remittance", "refinery", "lng", "opec", "hormuz", "petroleum",
                      "gasoline", "fertilizer", "earnings"],
        "min_matches": 2,
    },
    "conflict_expansion_contagion": {
        "label": "Conflict expansion and contagion framing",
        "keywords": ["contagion", "expansion", "threading", "widening", "escalation",
                      "domino", "spreading", "regional", "proxy", "cuba", "venezuela",
                      "pakistan", "indonesia", "australia", "india", "sri lanka",
                      "southeast asia", "korean", "african", "canadian"],
        "min_matches": 1,
    },
    "military_capability_strategy": {
        "label": "Military capability and strategy speculation",
        "keywords": ["military", "invasion", "ground", "invasion speculation", "missile",
                      "drone", "strike", "weapon", "munition", "arsenal", "depletion",
                      "deployment", "capability", "tomahawk", "patriot", "iron dome",
                      "friendly-fire", "b-2", "ammunition", "carrier", "navy"],
        "min_matches": 2,
    },
    "diplomatic_negotiation": {
        "label": "Diplomatic and negotiation framing",
        "keywords": ["diplomatic", "negotiation", "ceasefire", "talks", "mediation",
                      "mediator", "peace", "deal", "framework", "constraint", "rejection",
                      "denial", "abandonment", "exclusion"],
        "min_matches": 2,
    },
    "japan_specific_anchoring": {
        "label": "Japan-specific impact anchoring",
        "keywords": ["japan", "japanese", "ministerial", "yen"],
        "min_matches": 1,
    },
    "sports_culture_distraction": {
        "label": "Non-conflict topic politicization",
        "keywords": ["sports", "cricket", "athlete", "celebrity", "cultural", "easter",
                      "chocolate", "pope", "wildlife", "tourism", "art", "museum",
                      "bioluminescent", "travel", "vacation", "april fools", "egg",
                      "dui", "heroism", "equal pay", "luxury"],
        "min_matches": 1,
    },
    "trump_rhetoric": {
        "label": "Trump rhetoric and policy framing",
        "keywords": ["trump", "maga", "white house", "birthright", "treason",
                      "profanity", "hellfire", "silent treatment"],
        "min_matches": 1,
    },
    "nuclear_threat": {
        "label": "Nuclear threat framing",
        "keywords": ["nuclear", "iaea", "enrichment", "reactor", "facility",
                      "radiation", "waste", "atomic"],
        "min_matches": 1,
    },
    "war_crime_legal": {
        "label": "War crime and legal framing",
        "keywords": ["war crime", "international law", "legal", "crime",
                      "no quarter", "geneva", "tribunal", "prosecution"],
        "min_matches": 1,
    },
    "european_policy_focus": {
        "label": "European policy and institutional focus",
        "keywords": ["eu ", "european", "nato", "von der leyen", "lagarde",
                      "german", "spanish", "french", "orbán", "uk ", "british",
                      "ramstein", "swiss", "lignite"],
        "min_matches": 1,
    },
    "tech_cyber_infrastructure": {
        "label": "Technology and infrastructure impact",
        "keywords": ["cyber", "satellite", "data center", "aws", "hyperscaler",
                      "ai ", "semiconductor", "beidou", "blockchain", "cryptocurrency",
                      "tech", "infrastructure", "desalination", "pipeline", "airspace",
                      "navigation"],
        "min_matches": 1,
    },
    "historical_analogy": {
        "label": "Historical analogy invocation",
        "keywords": ["historical", "1973", "pearl harbor", "embargo",
                      "cold war", "playbook", "precedent", "history"],
        "min_matches": 1,
    },
    "regime_succession": {
        "label": "Regime change and succession narrative",
        "keywords": ["regime", "succession", "khamenei", "leadership",
                      "decapitation", "vacuum", "transition", "disappearance",
                      "new leader", "pahlavi", "exile"],
        "min_matches": 1,
    },
    "protest_dissent": {
        "label": "Protest and dissent coverage",
        "keywords": ["protest", "dissent", "conviction", "anti-war",
                      "confrontation", "diaspora", "demonstration",
                      "anti-base", "anti-muslim"],
        "min_matches": 1,
    },
    "media_information_warfare": {
        "label": "Media and information warfare framing",
        "keywords": ["disinformation", "propaganda", "meta-narrative",
                      "meta-framing", "conspiracy", "false flag",
                      "censorship", "suppression", "blackout"],
        "min_matches": 1,
    },
    "humanitarian_migration": {
        "label": "Humanitarian and migration framing",
        "keywords": ["migration", "refugee", "repatriation", "stateless",
                      "visa", "worker", "gig economy", "teacher salary"],
        "min_matches": 1,
    },
}


def text_matches_theme(text: str, theme: dict) -> bool:
    """Check if text matches a theme's keywords."""
    text_lower = text.lower()
    matches = sum(1 for kw in theme["keywords"] if kw in text_lower)
    return matches >= theme["min_matches"]


def assign_theme(bias: dict) -> str | None:
    """Assign a bias to a theme, or None if it doesn't match any."""
    combined = f"{bias['pattern']} {bias.get('detail', '')}"
    for theme_id, theme in THEMES.items():
        if text_matches_theme(combined, theme):
            return theme_id
    return None


def merge_group(biases: list[dict], theme_label: str | None = None) -> dict:
    """Merge a group of biases into one entry."""
    if len(biases) == 1:
        return biases[0]

    # Pick confirmed as primary, else shortest pattern name
    primary = None
    for b in biases:
        if b.get("status") == "confirmed":
            primary = b
            break
    if not primary:
        primary = min(biases, key=lambda x: len(x["pattern"]))

    others = [b for b in biases if b is not primary]

    # Use theme label as pattern if available and primary isn't confirmed
    pattern = primary["pattern"]
    if theme_label and primary.get("status") != "confirmed":
        pattern = theme_label

    # Build merged detail
    all_details = []
    for b in [primary] + others:
        all_details.append(f'[{b["pattern"]}] {b["detail"]}')
    merged_detail = " | ".join(all_details)

    # Combine unique debias instructions
    debias_parts = []
    seen = set()
    for b in [primary] + others:
        d = b.get("debias", "").strip()
        if d and d.lower() not in seen:
            seen.add(d.lower())
            debias_parts.append(d)
    merged_debias = " ".join(debias_parts[:3])  # Cap at 3 to keep readable

    status = "confirmed" if any(b.get("status") == "confirmed" for b in biases) else "suggested"
    dates = [b.get("date_added", "9999-99-99") for b in biases]

    return {
        "pattern": pattern,
        "detail": merged_detail,
        "debias": merged_debias,
        "date_added": min(dates),
        "status": status,
        "merged_count": len(biases),
    }


def consolidate_source(biases: list[dict]) -> list[dict]:
    """Consolidate biases for one source using theme-based grouping."""
    # Group by theme
    theme_groups: dict[str, list[dict]] = {}
    ungrouped: list[dict] = []

    for bias in biases:
        theme = assign_theme(bias)
        if theme:
            theme_groups.setdefault(theme, []).append(bias)
        else:
            ungrouped.append(bias)

    merged = []

    # Merge each theme group
    for theme_id, group in theme_groups.items():
        theme_label = THEMES[theme_id]["label"]
        merged.append(merge_group(group, theme_label))

    # Keep ungrouped biases as-is
    for bias in ungrouped:
        bias["merged_count"] = 1
        merged.append(bias)

    # Sort: confirmed first, then by date
    merged.sort(key=lambda x: (0 if x["status"] == "confirmed" else 1, x["date_added"]))
    return merged


def main():
    # Backup first
    original = BIASES_PATH.read_text()
    BACKUP_PATH.write_text(original)
    print(f"Backup saved to {BACKUP_PATH}")

    data = json.loads(original)

    print("\n=== Before consolidation ===")
    total_before = 0
    for key, info in data.items():
        count = len(info["biases"])
        total_before += count
        print(f"  {info['display_name']}: {count} biases")
    print(f"  TOTAL: {total_before}")

    for key, info in data.items():
        info["biases"] = consolidate_source(info["biases"])

    print("\n=== After consolidation ===")
    total_after = 0
    for key, info in data.items():
        count = len(info["biases"])
        total_after += count
        for b in info["biases"]:
            mc = b.get("merged_count", 1)
            if mc > 1:
                print(f"  {info['display_name']}: '{b['pattern']}' ({mc} merged)")
        print(f"  {info['display_name']}: {count} biases")
    print(f"  TOTAL: {total_after} (reduced from {total_before})")

    # Clean up merged_count before writing
    for key, info in data.items():
        for b in info["biases"]:
            b.pop("merged_count", None)

    BIASES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"\nWritten to {BIASES_PATH}")


if __name__ == "__main__":
    main()
