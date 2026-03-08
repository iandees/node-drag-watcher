"""General-purpose OSM changeset revert logic.

This module knows about OSM elements (nodes, ways) and changesets.
It does NOT know about "drags", "angles", or watcher-specific concepts.
Callers provide node_ids and way_ids to revert; this module fetches
the changeset download to compute what changed and undoes it.
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

DEFAULT_OSM_API_BASE = "https://api.openstreetmap.org/api/0.6"

# -- Exceptions ---------------------------------------------------------------


class RevertError(Exception):
    ...


class AlreadyRevertedError(RevertError):
    """Nothing to do — all elements already in expected state."""


class ConflictError(RevertError):
    """Version conflict (HTTP 409)."""


class AuthError(RevertError):
    """Authentication/authorization failure (HTTP 401/403)."""


# -- Result -------------------------------------------------------------------


@dataclass
class RevertResult:
    revert_changeset_id: str | None = None
    nodes_moved: list[str] = field(default_factory=list)
    nodes_undeleted: list[str] = field(default_factory=list)
    ways_updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# -- OSM API helpers -----------------------------------------------------------

_READ_HEADERS = {"User-Agent": "node-drag-watcher/0.1"}


def _osm_headers(osm_token: str) -> dict:
    return {
        "Authorization": f"Bearer {osm_token}",
        "Content-Type": "application/xml",
        "User-Agent": "node-drag-watcher/0.1",
    }


def _check_response(resp: requests.Response, context: str) -> None:
    """Map HTTP errors to typed exceptions."""
    if resp.status_code in (401, 403):
        raise AuthError(f"{context}: HTTP {resp.status_code} — {resp.text}")
    if resp.status_code == 409:
        raise ConflictError(f"{context}: HTTP 409 Conflict — {resp.text}")
    resp.raise_for_status()


def create_changeset(osm_token: str, comment: str,
                     api_base: str = DEFAULT_OSM_API_BASE) -> str:
    changeset_xml = (
        '<osm><changeset>'
        f'<tag k="comment" v="{_xml_escape(comment)}"/>'
        '<tag k="created_by" v="node-drag-watcher"/>'
        '</changeset></osm>'
    )
    resp = requests.put(
        f"{api_base}/changeset/create",
        data=changeset_xml,
        headers=_osm_headers(osm_token),
        timeout=15,
    )
    _check_response(resp, "create changeset")
    return resp.text.strip()


def close_changeset(osm_token: str, changeset_id: str,
                    api_base: str = DEFAULT_OSM_API_BASE) -> None:
    resp = requests.put(
        f"{api_base}/changeset/{changeset_id}/close",
        headers=_osm_headers(osm_token),
        timeout=15,
    )
    # Best-effort close — don't raise on close failures
    if not resp.ok:
        log.warning("Failed to close changeset %s: HTTP %s", changeset_id, resp.status_code)


def fetch_changeset_download(changeset_id: str,
                             api_base: str = DEFAULT_OSM_API_BASE) -> ET.Element:
    """Fetch osmChange XML for a changeset. Returns the parsed root element."""
    resp = requests.get(
        f"{api_base}/changeset/{changeset_id}/download",
        timeout=30,
        headers=_READ_HEADERS,
    )
    _check_response(resp, f"fetch changeset {changeset_id} download")
    return ET.fromstring(resp.text)


def fetch_element_version(element_type: str, element_id: str, version: int,
                          api_base: str = DEFAULT_OSM_API_BASE) -> ET.Element:
    """Fetch a specific version of a node or way."""
    resp = requests.get(
        f"{api_base}/{element_type}/{element_id}/{version}",
        timeout=15,
        headers=_READ_HEADERS,
    )
    _check_response(resp, f"fetch {element_type} {element_id} v{version}")
    root = ET.fromstring(resp.text)
    return root.find(element_type)


def fetch_node(node_id: str,
               api_base: str = DEFAULT_OSM_API_BASE) -> tuple[ET.Element, bool]:
    """Fetch a node. Returns (element, is_visible).

    For deleted nodes (410), fetches the last version via history.
    """
    resp = requests.get(f"{api_base}/node/{node_id}", timeout=15, headers=_READ_HEADERS)
    if resp.status_code == 410:
        # Node is deleted — fetch history and return last version
        hist_resp = requests.get(f"{api_base}/node/{node_id}/history", timeout=15, headers=_READ_HEADERS)
        _check_response(hist_resp, f"fetch node {node_id} history")
        root = ET.fromstring(hist_resp.text)
        nodes = root.findall("node")
        return nodes[-1], False
    _check_response(resp, f"fetch node {node_id}")
    root = ET.fromstring(resp.text)
    return root.find("node"), True


def fetch_way(way_id: str,
              api_base: str = DEFAULT_OSM_API_BASE) -> ET.Element:
    resp = requests.get(f"{api_base}/way/{way_id}", timeout=15, headers=_READ_HEADERS)
    _check_response(resp, f"fetch way {way_id}")
    root = ET.fromstring(resp.text)
    return root.find("way")


def update_node(osm_token: str, cs_id: str, node_elem: ET.Element,
                lat: float, lon: float,
                api_base: str = DEFAULT_OSM_API_BASE) -> None:
    """Update a visible node's position, preserving tags."""
    node_id = node_elem.get("id")
    version = node_elem.get("version")
    tags_xml = _tags_to_xml(node_elem)
    node_xml = (
        f'<osm><node id="{node_id}" version="{version}" changeset="{cs_id}" '
        f'lat="{lat}" lon="{lon}">{tags_xml}</node></osm>'
    )
    resp = requests.put(
        f"{api_base}/node/{node_id}",
        data=node_xml,
        headers=_osm_headers(osm_token),
        timeout=15,
    )
    _check_response(resp, f"update node {node_id}")


def undelete_node(osm_token: str, cs_id: str, node_elem: ET.Element,
                  lat: float, lon: float,
                  api_base: str = DEFAULT_OSM_API_BASE) -> None:
    """Restore a deleted node by PUTting with visible=true."""
    node_id = node_elem.get("id")
    version = node_elem.get("version")
    tags_xml = _tags_to_xml(node_elem)
    node_xml = (
        f'<osm><node id="{node_id}" version="{version}" changeset="{cs_id}" '
        f'visible="true" lat="{lat}" lon="{lon}">{tags_xml}</node></osm>'
    )
    resp = requests.put(
        f"{api_base}/node/{node_id}",
        data=node_xml,
        headers=_osm_headers(osm_token),
        timeout=15,
    )
    _check_response(resp, f"undelete node {node_id}")


def update_way(osm_token: str, cs_id: str, way_elem: ET.Element,
               nd_refs: list[str],
               api_base: str = DEFAULT_OSM_API_BASE) -> None:
    """Update a way's nd list, preserving tags."""
    way_id = way_elem.get("id")
    version = way_elem.get("version")
    nds_xml = "".join(f'<nd ref="{ref}"/>' for ref in nd_refs)
    tags_xml = _tags_to_xml(way_elem)
    way_xml = (
        f'<osm><way id="{way_id}" version="{version}" changeset="{cs_id}">'
        f'{nds_xml}{tags_xml}</way></osm>'
    )
    resp = requests.put(
        f"{api_base}/way/{way_id}",
        data=way_xml,
        headers=_osm_headers(osm_token),
        timeout=15,
    )
    _check_response(resp, f"update way {way_id}")


def comment_on_changeset(osm_token: str, changeset_id: str, text: str,
                         api_base: str = DEFAULT_OSM_API_BASE) -> None:
    resp = requests.post(
        f"{api_base}/changeset/{changeset_id}/comment",
        data={"text": text},
        headers={"Authorization": f"Bearer {osm_token}", "User-Agent": "node-drag-watcher/0.1"},
        timeout=15,
    )
    _check_response(resp, f"comment on changeset {changeset_id}")


# -- Internal helpers ----------------------------------------------------------


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _tags_to_xml(elem: ET.Element) -> str:
    parts = []
    for tag in elem.findall("tag"):
        k = _xml_escape(tag.get("k", ""))
        v = _xml_escape(tag.get("v", ""))
        parts.append(f'<tag k="{k}" v="{v}"/>')
    return "".join(parts)


def _nd_refs(elem: ET.Element) -> list[str]:
    """Extract nd refs from a way element."""
    return [nd.get("ref") for nd in elem.findall("nd")]


# -- Changeset analysis -------------------------------------------------------


def _find_in_osmchange(osmchange: ET.Element, element_type: str,
                       element_id: str) -> tuple[str, ET.Element] | None:
    """Find an element in osmChange XML.

    Returns (action_type, element) where action_type is
    "create", "modify", or "delete". Returns None if not found.
    """
    for action_tag in ("create", "modify", "delete"):
        for action_block in osmchange.findall(action_tag):
            for elem in action_block.findall(element_type):
                if elem.get("id") == element_id:
                    return action_tag, elem
    return None


def _discover_deleted_nodes(osmchange: ET.Element,
                            before_refs: set[str],
                            after_refs: set[str]) -> list[str]:
    """Find nodes removed from a way that were also deleted in the changeset."""
    missing_refs = before_refs - after_refs
    deleted_node_ids = []
    for action_block in osmchange.findall("delete"):
        for node in action_block.findall("node"):
            if node.get("id") in missing_refs:
                deleted_node_ids.append(node.get("id"))
    return deleted_node_ids


# -- Main entry point ----------------------------------------------------------


def revert_changeset(
    osm_token: str,
    changeset_id: str,
    comment: str,
    node_ids: list[str],
    way_ids: list[str],
    changeset_comment: str | None = None,
    api_base: str = DEFAULT_OSM_API_BASE,
) -> RevertResult:
    """Revert specific nodes and ways from a changeset.

    Fetches the changeset download to determine what changed,
    then reverses those changes. Auto-discovers deleted nodes
    referenced by ways being reverted.
    """
    result = RevertResult()

    # -- Fetch changeset download ----------------------------------------------
    osmchange = fetch_changeset_download(changeset_id, api_base=api_base)

    # -- Analyze ways first to auto-discover deleted nodes ---------------------
    extra_node_ids: set[str] = set()
    way_reverts: list[tuple[str, ET.Element]] = []  # (way_id, before_elem)

    for way_id in way_ids:
        found = _find_in_osmchange(osmchange, "way", way_id)
        if found is None:
            result.skipped.append(f"way {way_id}: not found in changeset")
            continue
        action_type, cs_elem = found
        cs_version = int(cs_elem.get("version"))

        if action_type == "modify":
            before_elem = fetch_element_version("way", way_id, cs_version - 1, api_base=api_base)
            before_refs = set(_nd_refs(before_elem))
            after_refs = set(_nd_refs(cs_elem))
            # Auto-discover deleted nodes
            deleted = _discover_deleted_nodes(osmchange, before_refs, after_refs)
            extra_node_ids.update(deleted)
            way_reverts.append((way_id, before_elem))
        else:
            result.skipped.append(f"way {way_id}: action '{action_type}' not supported")

    # Merge auto-discovered node IDs with explicitly requested ones
    all_node_ids = list(dict.fromkeys(list(node_ids) + list(extra_node_ids)))

    # -- Analyze nodes ---------------------------------------------------------
    pending_moves: list[tuple[str, ET.Element, ET.Element]] = []  # (node_id, before, current)
    pending_undeletes: list[tuple[str, ET.Element]] = []  # (node_id, before)

    for node_id in all_node_ids:
        found = _find_in_osmchange(osmchange, "node", node_id)
        if found is None:
            result.skipped.append(f"node {node_id}: not found in changeset")
            continue
        action_type, cs_elem = found
        cs_version = int(cs_elem.get("version"))

        if action_type == "delete":
            # Node was deleted — check if still deleted
            current_elem, is_visible = fetch_node(node_id, api_base=api_base)
            if is_visible:
                result.skipped.append(f"node {node_id}: already visible, skip undelete")
                continue
            # Get the version before deletion for position/tags
            before_elem = fetch_element_version("node", node_id, cs_version - 1, api_base=api_base)
            pending_undeletes.append((node_id, before_elem))

        elif action_type == "modify":
            before_elem = fetch_element_version("node", node_id, cs_version - 1, api_base=api_base)
            # Check current state
            current_elem, is_visible = fetch_node(node_id, api_base=api_base)
            if not is_visible:
                result.skipped.append(f"node {node_id}: deleted, cannot move")
                continue
            # Only revert if current version matches changeset version
            current_version = int(current_elem.get("version"))
            if current_version != cs_version:
                result.skipped.append(
                    f"node {node_id}: version changed ({cs_version} → {current_version}), skipping"
                )
                continue
            pending_moves.append((node_id, before_elem, current_elem))

        else:
            result.skipped.append(f"node {node_id}: action '{action_type}' not supported for revert")

    # -- Pre-flight ways: check current version matches changeset version ------
    pending_way_updates: list[tuple[str, list[str], ET.Element]] = []  # (way_id, before_refs, current)

    for way_id, before_elem in way_reverts:
        current_elem = fetch_way(way_id, api_base=api_base)
        found = _find_in_osmchange(osmchange, "way", way_id)
        cs_version = int(found[1].get("version"))
        current_version = int(current_elem.get("version"))
        if current_version != cs_version:
            result.skipped.append(
                f"way {way_id}: version changed ({cs_version} → {current_version}), skipping"
            )
            continue
        before_refs = _nd_refs(before_elem)
        pending_way_updates.append((way_id, before_refs, current_elem))

    # -- Nothing to do? --------------------------------------------------------
    if not pending_moves and not pending_undeletes and not pending_way_updates:
        raise AlreadyRevertedError("All elements already in expected state")

    # -- Create changeset and execute ------------------------------------------
    cs_id = create_changeset(osm_token, comment, api_base=api_base)
    result.revert_changeset_id = cs_id

    try:
        # Undeletes first (ways may reference these nodes)
        for node_id, before_elem in pending_undeletes:
            lat = float(before_elem.get("lat"))
            lon = float(before_elem.get("lon"))
            # Use current (deleted) element for version
            current_elem, _ = fetch_node(node_id, api_base=api_base)
            undelete_node(osm_token, cs_id, current_elem, lat, lon, api_base=api_base)
            result.nodes_undeleted.append(node_id)

        # Node moves
        for node_id, before_elem, current_elem in pending_moves:
            lat = float(before_elem.get("lat"))
            lon = float(before_elem.get("lon"))
            update_node(osm_token, cs_id, current_elem, lat, lon, api_base=api_base)
            result.nodes_moved.append(node_id)

        # Way updates (restore old nd list)
        for way_id, before_refs, current_elem in pending_way_updates:
            update_way(osm_token, cs_id, current_elem, before_refs, api_base=api_base)
            result.ways_updated.append(way_id)
    finally:
        close_changeset(osm_token, cs_id, api_base=api_base)

    # -- Comment on original changeset -----------------------------------------
    if changeset_comment:
        comment_on_changeset(osm_token, changeset_id, changeset_comment, api_base=api_base)

    return result
