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
                       tag_updates: dict[str, str]) -> str:
    """Build XML for updating an element with corrected tags.

    Preserves all existing tags, geometry, and nd refs,
    only replacing tags that are in tag_updates.
    """
    element_type = elem.tag
    element_id = elem.get("id")
    version = elem.get("version")

    # Build tags: merge updates into existing
    tags = {}
    for tag in elem.findall("tag"):
        tags[tag.get("k")] = tag.get("v")
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

    All issues should be for the same changeset.
    Returns the new changeset ID.
    """
    # Verify versions and collect updates
    updates: list[tuple[Issue, ET.Element]] = []

    for issue in issues:
        elem = _fetch_element(issue.element_type, issue.element_id, api_base)
        current_version = elem.get("version")
        if current_version != issue.element_version:
            raise VersionConflictError(
                f"{issue.element_type} {issue.element_id}: "
                f"version {issue.element_version} → {current_version}"
            )
        updates.append((issue, elem))

    # Create changeset
    comment = f"Fix tag formatting ({issues[0].check_name})"
    cs_id = create_changeset(osm_token, comment, api_base=api_base)

    try:
        for issue, elem in updates:
            xml = _build_element_xml(elem, cs_id, issue.tags_after)
            resp = requests.put(
                f"{api_base}/{issue.element_type}/{issue.element_id}",
                data=xml,
                headers=_osm_headers(osm_token),
                timeout=15,
            )
            _check_response(resp, f"update {issue.element_type} {issue.element_id}")
    finally:
        close_changeset(osm_token, cs_id, api_base=api_base)

    return cs_id
