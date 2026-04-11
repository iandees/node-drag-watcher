"""Tag key typo checker.

Detects misspelled or miscapitalized OSM tag keys and suggests
the correct version. Uses a curated dictionary of known misspellings
plus capitalization normalization against common valid keys.
"""

import logging

from checkers import Action, Issue, BaseChecker

log = logging.getLogger(__name__)

# The most common valid OSM tag keys (from taginfo, sorted by usage count).
# Used for capitalization normalization: if a tag key matches one of these
# when lowercased with spaces replaced by underscores, we suggest the fix.
COMMON_KEYS = {
    "building", "source", "highway", "addr:housenumber", "addr:street",
    "addr:city", "addr:postcode", "name", "natural", "surface",
    "addr:country", "landuse", "power", "waterway", "building:levels",
    "amenity", "barrier", "service", "addr:state", "access", "oneway",
    "height", "ref", "maxspeed", "lanes", "start_date", "addr:district",
    "layer", "operator", "lit", "crossing", "type", "footway", "wall",
    "addr:place", "leisure", "ele", "addr:suburb", "leaf_type", "tracktype",
    "addr:neighbourhood", "addr:hamlet", "addr:province", "man_made",
    "place", "roof:shape", "bicycle", "foot", "railway", "name:en",
    "bridge", "intermittent", "shop", "smoothness", "public_transport",
    "leaf_cycle", "tactile_paving", "tunnel", "material", "water",
    "entrance", "roof:levels", "direction", "bus", "sidewalk", "parking",
    "opening_hours", "note", "website", "wikidata", "building:part",
    "location", "addr:unit", "width", "denomination", "religion",
    "addr:flats", "description", "cuisine", "phone", "level",
    "healthcare", "sport", "capacity", "tourism", "office",
    "contact:phone", "contact:website", "email", "contact:email",
    "wheelchair", "brand", "brand:wikidata", "brand:wikipedia",
    "internet_access", "outdoor_seating", "payment:cash",
    "payment:credit_cards", "diet:vegetarian", "drive_through",
    "takeaway", "delivery", "smoking", "air_conditioning",
}

# Lookup: normalized form -> correct key
_NORMALIZED_TO_KEY = {}
for _key in COMMON_KEYS:
    _norm = _key.lower().replace(" ", "_")
    _NORMALIZED_TO_KEY[_norm] = _key

# Curated dictionary of known misspellings -> correct key.
# Seeded with common transposition, omission, and doubling typos.
MISSPELLINGS = {
    # building
    "biulding": "building",
    "buiding": "building",
    "bulding": "building",
    "builing": "building",
    "builidng": "building",
    "buildng": "building",
    "buidling": "building",
    "buildign": "building",
    "buildin": "building",
    "biulding:levels": "building:levels",
    # highway
    "hihgway": "highway",
    "highwy": "highway",
    "higway": "highway",
    "hgihway": "highway",
    "highwya": "highway",
    "highway": "highway",
    # amenity
    "ameniy": "amenity",
    "amenty": "amenity",
    "amenitiy": "amenity",
    "amentiy": "amenity",
    "amnity": "amenity",
    # name
    "nme": "name",
    "nmae": "name",
    "naem": "name",
    # natural
    "natrual": "natural",
    "natual": "natural",
    "naturla": "natural",
    # surface
    "suface": "surface",
    "surfce": "surface",
    "surace": "surface",
    "surfcae": "surface",
    # landuse
    "landue": "landuse",
    "landse": "landuse",
    "lnaduse": "landuse",
    "landues": "landuse",
    # layer
    "kayer": "layer",
    "layr": "layer",
    "laeyr": "layer",
    "lyaer": "layer",
    "laye": "layer",
    # leisure
    "lesiure": "leisure",
    "lesure": "leisure",
    "liesure": "leisure",
    # tourism
    "tousim": "tourism",
    "tourisim": "tourism",
    "tourims": "tourism",
    # access
    "acces": "access",
    "acess": "access",
    "acccess": "access",
    # opening_hours
    "opeing_hours": "opening_hours",
    "opening_horus": "opening_hours",
    "openinghours": "opening_hours",
    "opening_hour": "opening_hours",
    # addr:street
    "addr:steet": "addr:street",
    "addr:stret": "addr:street",
    "addr:sreet": "addr:street",
    # addr:housenumber
    "addr:housenumer": "addr:housenumber",
    "addr:housnumber": "addr:housenumber",
    "addr:housenumbe": "addr:housenumber",
    # addr:city
    "addr:ciy": "addr:city",
    "addr:cty": "addr:city",
    # addr:postcode
    "addr:postocode": "addr:postcode",
    "addr:postocde": "addr:postcode",
    "addr:pstcode": "addr:postcode",
    # website
    "webiste": "website",
    "wesite": "website",
    "websit": "website",
    "webstie": "website",
    # phone
    "phon": "phone",
    "pohne": "phone",
    # shop
    "shp": "shop",
    "shpo": "shop",
    # operator
    "oeprator": "operator",
    "opertor": "operator",
    "operater": "operator",
    # description
    "descrption": "description",
    "descripton": "description",
    "desciption": "description",
    # cuisine
    "cusine": "cuisine",
    "cuisne": "cuisine",
    "cuising": "cuisine",
    # religion
    "religon": "religion",
    "relgion": "religion",
    # denomination
    "denominaton": "denomination",
    "denomiation": "denomination",
    # wheelchair
    "wheelcahir": "wheelchair",
    "weelchair": "wheelchair",
    "wheelchar": "wheelchair",
}


def _normalize_key(key: str) -> str:
    """Normalize a tag key for capitalization comparison."""
    return key.lower().replace(" ", "_")


class TagTypoChecker(BaseChecker):
    """Detect misspelled or miscapitalized OSM tag keys."""

    def check(self, action: Action) -> list[Issue]:
        if action.action_type == "delete":
            return []

        issues = []

        for tag_key, tag_value in action.tags_new.items():
            # Skip tags that weren't changed in this edit
            if action.tags_old.get(tag_key) == tag_value:
                continue

            corrected_key = self._find_correction(tag_key, action.tags_new)
            if corrected_key is None:
                continue

            issues.append(Issue(
                element_type=action.element_type,
                element_id=action.element_id,
                element_version=action.version,
                changeset=action.changeset,
                user=action.user,
                check_name="tag_typo",
                summary=f"{tag_key}={tag_value} → {corrected_key}={tag_value}",
                tags_before={tag_key: tag_value},
                tags_after={corrected_key: tag_value},
                extra={"old_key": tag_key, "new_key": corrected_key},
            ))

        return issues

    def _find_correction(self, key: str, all_tags: dict[str, str]) -> str | None:
        """Find the correct key for a possibly misspelled/miscapitalized key.

        Returns the corrected key, or None if no correction needed.
        """
        # Check curated misspellings first
        if key in MISSPELLINGS:
            corrected = MISSPELLINGS[key]
            # Don't suggest if the correct key already exists on this element
            if corrected not in all_tags:
                return corrected

        # Check capitalization: skip if already lowercase with underscores
        normalized = _normalize_key(key)
        if normalized == key:
            return None

        # See if the normalized form matches a known valid key
        corrected = _NORMALIZED_TO_KEY.get(normalized)
        if corrected is not None and corrected != key and corrected not in all_tags:
            return corrected

        return None
