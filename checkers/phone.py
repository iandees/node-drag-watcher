"""Phone number formatting checker.

Detects phone/fax tags that don't follow international format
and suggests the correctly formatted version.
"""

import logging
import re

import phonenumbers

from checkers import Action, Issue, BaseChecker

log = logging.getLogger(__name__)

# Tags that contain phone numbers
PHONE_TAG_PATTERN = re.compile(
    r'^(phone|fax|contact:phone|contact:fax)(:.+)?$'
)


def _infer_country_code(coords: tuple[float, float] | None) -> str | None:
    """Infer ISO country code from coordinates using a simple lat/lon mapping."""
    if coords is None:
        return None
    try:
        lat, lon = coords
        return _coords_to_country(lat, lon)
    except Exception:
        return None


def _coords_to_country(lat: float, lon: float) -> str | None:
    """Very rough lat/lon to country code mapping for phone number parsing.

    Covers major regions. Returns None for ambiguous areas.
    """
    # North America
    if 24 < lat < 50 and -130 < lon < -60:
        return "US"
    # UK/Ireland
    if 49 < lat < 61 and -11 < lon < 2:
        return "GB"
    # Western Europe
    if 42 < lat < 55 and 2 < lon < 15:
        return "DE"
    # Scandinavia
    if 55 < lat < 72 and 4 < lon < 32:
        return "SE"
    # France/Spain/Portugal
    if 36 < lat < 49 and -10 < lon < 4:
        return "FR"
    # Italy
    if 36 < lat < 47 and 6 < lon < 19:
        return "IT"
    # Australia
    if -45 < lat < -10 and 110 < lon < 155:
        return "AU"
    # Japan
    if 24 < lat < 46 and 123 < lon < 146:
        return "JP"
    # Brazil
    if -34 < lat < 6 and -74 < lon < -34:
        return "BR"
    return None


def _format_phone(raw: str, country: str | None) -> str | None:
    """Try to parse and format a phone number that needs a country code added.

    Only returns a formatted number if the raw value can't be parsed without
    a country hint — meaning it's missing the international prefix. If the
    number already parses on its own (has a valid country code), we skip it
    since consumers can reformat it themselves.
    """
    # If it parses without a country hint, the country code is already there.
    # Just reformatting (spacing/dashes) isn't worth a changeset.
    try:
        parsed = phonenumbers.parse(raw, None)
        if phonenumbers.is_valid_number(parsed):
            return None
    except phonenumbers.NumberParseException:
        pass

    # Needs a country hint to parse — this means it's missing the country code
    if country:
        try:
            parsed = phonenumbers.parse(raw, country)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
                )
        except phonenumbers.NumberParseException:
            pass

    return None


def _format_phone_value(value: str, country: str | None) -> str | None:
    """Format a phone tag value, handling semicolon-separated numbers.

    Returns formatted value or None if nothing changed.
    """
    parts = [p.strip() for p in value.split(";")]
    formatted_parts = []
    any_changed = False

    for part in parts:
        if not part:
            continue
        formatted = _format_phone(part, country)
        if formatted is None:
            formatted_parts.append(part)
        else:
            formatted_parts.append(formatted)
            if formatted != part:
                any_changed = True

    if not any_changed:
        return None

    return ";".join(formatted_parts)


class PhoneChecker(BaseChecker):
    """Detect phone/fax tags that aren't in international format."""

    def check(self, action: Action) -> list[Issue]:
        if action.action_type == "delete":
            return []

        country = _infer_country_code(action.coords_new)
        issues = []

        for tag_key, tag_value in action.tags_new.items():
            if not PHONE_TAG_PATTERN.match(tag_key):
                continue

            formatted = _format_phone_value(tag_value, country)
            if formatted is None:
                continue

            issues.append(Issue(
                element_type=action.element_type,
                element_id=action.element_id,
                element_version=action.version,
                changeset=action.changeset,
                user=action.user,
                check_name="phone_format",
                summary=f"{tag_key}: {tag_value} → {formatted}",
                tags_before={tag_key: tag_value},
                tags_after={tag_key: formatted},
            ))

        return issues
