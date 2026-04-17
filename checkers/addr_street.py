"""addr:street abbreviation expansion checker.

Detects abbreviated street suffixes and directions in addr:street tags
(e.g. "Tyler ST NE" → "Tyler Street Northeast") and expands them.

Only expands abbreviations that are unambiguous in context. For example,
"ST" alone could be Street or Saint, so it's only expanded when it
appears as a street suffix (not the first word). Compound directions
(NE, NW, SE, SW) are always unambiguous in addr:street context.
"""

import logging

from checkers import Action, Issue, BaseChecker

log = logging.getLogger(__name__)

# Street suffix abbreviations → full form.
# These are only expanded when they appear after at least one preceding word
# (i.e. as a suffix, not a prefix like "St. Louis").
SUFFIX_EXPANSIONS = {
    "AVE": "Avenue",
    "AV": "Avenue",
    "BLVD": "Boulevard",
    "CIR": "Circle",
    "CT": "Court",
    "DR": "Drive",
    "HWY": "Highway",
    "LN": "Lane",
    "PKWY": "Parkway",
    "PKY": "Parkway",
    "PL": "Place",
    "RD": "Road",
    "ST": "Street",
    "TER": "Terrace",
    "TERR": "Terrace",
    "TRL": "Trail",
}

# Compound direction abbreviations → full form.
# These are unambiguous in addr:street context (no common English words
# match NE/NW/SE/SW). Expanded in any position.
DIRECTION_EXPANSIONS = {
    "NE": "Northeast",
    "NW": "Northwest",
    "SE": "Southeast",
    "SW": "Southwest",
}

# Single-letter directions are only expanded at the start or end of
# the street name to avoid false positives on middle initials, etc.
SINGLE_DIRECTION_EXPANSIONS = {
    "N": "North",
    "S": "South",
    "E": "East",
    "W": "West",
}


def _is_abbreviated(word: str, expansion: str) -> bool:
    """Check if a word is an abbreviation (not already the full form)."""
    return word != expansion


def _expand_street(street: str) -> str | None:
    """Expand unambiguous abbreviations in a street name.

    Returns the expanded form, or None if nothing changed.

    Rules to avoid ambiguity:
    - Suffix abbreviations (ST, AVE, etc.) only expand when NOT the first word.
      This avoids "St. Paul" or "Avenue Road" mismatches.
    - "ST" as the first word is skipped (could be "Saint").
    - Compound directions (NE, NW, SE, SW) expand in any position.
    - Single-letter directions (N, S, E, W) only expand at the first or last
      position to avoid middle initials.
    """
    words = street.split()
    if len(words) < 2:
        return None

    changed = False
    result = []

    for i, word in enumerate(words):
        # Strip trailing period (e.g. "St." → "St") for lookup
        stripped = word.rstrip(".")
        upper = stripped.upper()
        is_first = i == 0
        is_last = i == len(words) - 1

        # Compound directions: unambiguous anywhere
        if upper in DIRECTION_EXPANSIONS:
            expansion = DIRECTION_EXPANSIONS[upper]
            if _is_abbreviated(word, expansion):
                result.append(expansion)
                changed = True
                continue

        # Suffix abbreviations: only when not the first word
        if not is_first and upper in SUFFIX_EXPANSIONS:
            expansion = SUFFIX_EXPANSIONS[upper]
            if _is_abbreviated(word, expansion):
                result.append(expansion)
                changed = True
                continue

        # Single-letter directions: only at start or end
        if (is_first or is_last) and upper in SINGLE_DIRECTION_EXPANSIONS:
            expansion = SINGLE_DIRECTION_EXPANSIONS[upper]
            if _is_abbreviated(word, expansion):
                result.append(expansion)
                changed = True
                continue

        result.append(word)

    if not changed:
        return None

    return " ".join(result)


class AddrStreetChecker(BaseChecker):
    """Detect abbreviated street suffixes/directions in addr:street tags."""

    def check(self, action: Action) -> list[Issue]:
        if action.action_type == "delete":
            return []

        street_value = action.tags_new.get("addr:street")
        if street_value is None:
            return []

        # Skip if addr:street wasn't changed in this edit
        if action.tags_old.get("addr:street") == street_value:
            return []

        expanded = _expand_street(street_value)
        if expanded is None:
            return []

        return [Issue(
            element_type=action.element_type,
            element_id=action.element_id,
            element_version=action.version,
            changeset=action.changeset,
            user=action.user,
            check_name="addr_street_abbrev",
            summary="Expand abbreviated street name",
            tags_before={"addr:street": street_value},
            tags_after={"addr:street": expanded},
        )]
