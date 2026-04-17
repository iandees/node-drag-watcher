"""Apply tag corrections to OSM elements.

Fetches current element, verifies version matches, updates tags.
Used by phone and website checkers.
"""

import logging
import xml.etree.ElementTree as ET

import requests

from checkers import Issue
from revert import (
    _osm_headers, _check_response, _xml_escape,
    create_changeset, close_changeset,
    ConflictError,
    DEFAULT_OSM_API_BASE,
)

log = logging.getLogger(__name__)

_READ_HEADERS = {"User-Agent": "node-drag-watcher/0.1"}


class TagFixError(Exception):
    ...


class VersionConflictError(TagFixError):
    """Element version changed since issue was detected."""


def _fetch_element(element_type: str, element_id: str,
                   api_base: str = DEFAULT_OSM_API_BASE) -> ET.Element:
    """Fetch current state of an element."""
    resp = requests.get(
        f"{api_base}/{element_type}/{element_id}",
        timeout=15,
        headers=_READ_HEADERS,
    )
    _check_response(resp, f"fetch {element_type} {element_id}")
    root = ET.fromstring(resp.text)
    return root.find(element_type)


def _build_element_xml(elem: ET.Element, cs_id: str,
                       tag_updates: dict[str, str],
                       keys_to_remove: set[str] | None = None) -> str:
    """Build XML for updating an element with corrected tags.

    Preserves all existing tags, geometry, and nd refs,
    only replacing tags that are in tag_updates and removing
    keys in keys_to_remove (for key renames like typo fixes).
    """
    element_type = elem.tag
    element_id = elem.get("id")
    version = elem.get("version")

    # Build tags: merge updates into existing, remove old keys
    tags = {}
    for tag in elem.findall("tag"):
        k = tag.get("k")
        if keys_to_remove and k in keys_to_remove:
            continue
        tags[k] = tag.get("v")
    tags.update(tag_updates)

    tags_xml = "".join(
        f'<tag k="{_xml_escape(k)}" v="{_xml_escape(v)}"/>'
        for k, v in tags.items()
    )

    # Preserve nd refs for ways
    nds_xml = ""
    if element_type == "way":
        nds_xml = "".join(
            f'<nd ref="{nd.get("ref")}"/>'
            for nd in elem.findall("nd")
        )

    # Preserve members for relations
    members_xml = ""
    if element_type == "relation":
        for member in elem.findall("member"):
            members_xml += (
                f'<member type="{member.get("type")}" '
                f'ref="{member.get("ref")}" '
                f'role="{_xml_escape(member.get("role", ""))}"/>'
            )

    # Node-specific attributes
    attrs = f'id="{element_id}" version="{version}" changeset="{cs_id}"'
    if element_type == "node":
        attrs += f' lat="{elem.get("lat")}" lon="{elem.get("lon")}"'

    return (
        f'<osm><{element_type} {attrs}>'
        f'{nds_xml}{members_xml}{tags_xml}'
        f'</{element_type}></osm>'
    )


def fix_tags(
    osm_token: str,
    issues: list[Issue],
    api_base: str = DEFAULT_OSM_API_BASE,
) -> str:
    """Apply tag corrections from issues to OSM.

    All issues should be for the same changeset. Multiple issues for the
    same element are merged into a single update to avoid version conflicts.
    Returns the new changeset ID.
    """
    # Group issues by element so multiple tag fixes on the same element
    # are applied in one PUT request
    element_key = lambda i: (i.element_type, i.element_id)
    grouped: dict[tuple[str, str], list[Issue]] = {}
    for issue in issues:
        grouped.setdefault(element_key(issue), []).append(issue)

    # Verify tags still need fixing and collect merged updates
    updates: list[tuple[str, str, ET.Element, dict[str, str], set[str]]] = []

    for (etype, eid), element_issues in grouped.items():
        elem = _fetch_element(etype, eid, api_base)
        current_version = elem.get("version")
        expected_version = element_issues[0].element_version
        if current_version != expected_version:
            log.info(
                "%s %s: version changed (%s → %s), will rebase tag fix onto current version",
                etype, eid, expected_version, current_version,
            )

        # Check if the tags we want to fix still have the "before" values.
        # If they already have the "after" values, someone else fixed them.
        current_tags = {tag.get("k"): tag.get("v") for tag in elem.findall("tag")}
        merged_tags: dict[str, str] = {}
        keys_to_remove: set[str] = set()
        for issue in element_issues:
            for key, after_val in issue.tags_after.items():
                current_val = current_tags.get(key)
                if current_val == after_val:
                    log.info("%s %s: tag %s already has correct value, skipping", etype, eid, key)
                    continue
                merged_tags[key] = after_val
            # Track old keys that differ from new keys (key renames like typo fixes)
            for old_key in issue.tags_before:
                if old_key not in issue.tags_after:
                    keys_to_remove.add(old_key)

        if merged_tags:
            updates.append((etype, eid, elem, merged_tags, keys_to_remove))

    if not updates:
        raise VersionConflictError("All tags already have correct values, nothing to fix")

    # Build changeset comment from check names
    check_names = list(dict.fromkeys(i.check_name for i in issues if i.check_name))
    comment = f"Fix tag formatting ({', '.join(check_names or ['tags'])})"
    cs_id = create_changeset(osm_token, comment, api_base=api_base)

    max_retries = 3

    try:
        for etype, eid, elem, tag_updates, remove_keys in updates:
            for attempt in range(max_retries):
                xml = _build_element_xml(elem, cs_id, tag_updates, remove_keys)
                resp = requests.put(
                    f"{api_base}/{etype}/{eid}",
                    data=xml,
                    headers=_osm_headers(osm_token),
                    timeout=15,
                )
                try:
                    _check_response(resp, f"update {etype} {eid}")
                    break
                except ConflictError:
                    if attempt == max_retries - 1:
                        raise
                    log.info("%s %s: version conflict, retrying (%d/%d)",
                             etype, eid, attempt + 1, max_retries)
                    elem = _fetch_element(etype, eid, api_base)
    finally:
        close_changeset(osm_token, cs_id, api_base=api_base)

    return cs_id
