"""General-purpose OSM changeset revert logic.

This module knows about OSM elements (nodes, ways) and changesets.
It does NOT know about "drags", "angles", or watcher-specific concepts.
Callers describe WHAT to revert using simple element-level instructions.
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

OSM_API_BASE = "https://api.openstreetmap.org/api/0.6"

# -- Exceptions ---------------------------------------------------------------


class RevertError(Exception):
    ...


class AlreadyRevertedError(RevertError):
    """Nothing to do — all instructions were already satisfied."""


class ConflictError(RevertError):
    """Version conflict (HTTP 409)."""


class AuthError(RevertError):
    """Authentication/authorization failure (HTTP 401/403)."""


# -- Revert instructions -------------------------------------------------------


@dataclass
class NodeMove:
    """Move a node back to old_lat/old_lon.
    Only applied if the node is still at new_lat/new_lon."""
    node_id: str
    old_lat: float
    old_lon: float
    new_lat: float
    new_lon: float


@dataclass
class NodeUndelete:
    """Restore a deleted node at the given position."""
    node_id: str
    lat: float
    lon: float


@dataclass
class WayNodeSwap:
    """In a way's nd list, replace new_node_ref with old_node_ref.
    Only applied if the way still references new_node_ref."""
    way_id: str
    old_node_ref: str
    new_node_ref: str


@dataclass
class RevertResult:
    revert_changeset_id: str | None = None
    nodes_moved: list[str] = field(default_factory=list)
    nodes_undeleted: list[str] = field(default_factory=list)
    ways_updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# -- OSM API helpers -----------------------------------------------------------

POSITION_TOLERANCE = 1e-6


def _osm_headers(osm_token: str) -> dict:
    return {
        "Authorization": f"Bearer {osm_token}",
        "Content-Type": "application/xml",
    }


def _check_response(resp: requests.Response, context: str) -> None:
    """Map HTTP errors to typed exceptions."""
    if resp.status_code in (401, 403):
        raise AuthError(f"{context}: HTTP {resp.status_code}")
    if resp.status_code == 409:
        raise ConflictError(f"{context}: HTTP 409 Conflict")
    resp.raise_for_status()


def create_changeset(osm_token: str, comment: str) -> str:
    changeset_xml = (
        '<osm><changeset>'
        f'<tag k="comment" v="{_xml_escape(comment)}"/>'
        '<tag k="created_by" v="node-drag-watcher"/>'
        '</changeset></osm>'
    )
    resp = requests.put(
        f"{OSM_API_BASE}/changeset/create",
        data=changeset_xml,
        headers=_osm_headers(osm_token),
        timeout=15,
    )
    _check_response(resp, "create changeset")
    return resp.text.strip()


def close_changeset(osm_token: str, changeset_id: str) -> None:
    resp = requests.put(
        f"{OSM_API_BASE}/changeset/{changeset_id}/close",
        headers=_osm_headers(osm_token),
        timeout=15,
    )
    # Best-effort close — don't raise on close failures
    if not resp.ok:
        log.warning("Failed to close changeset %s: HTTP %s", changeset_id, resp.status_code)


def fetch_node(node_id: str) -> tuple[ET.Element, bool]:
    """Fetch a node. Returns (element, is_visible).

    For deleted nodes (410), fetches the last version via history.
    """
    resp = requests.get(f"{OSM_API_BASE}/node/{node_id}", timeout=15)
    if resp.status_code == 410:
        # Node is deleted — fetch history and return last version
        hist_resp = requests.get(f"{OSM_API_BASE}/node/{node_id}/history", timeout=15)
        hist_resp.raise_for_status()
        root = ET.fromstring(hist_resp.text)
        nodes = root.findall("node")
        return nodes[-1], False
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    return root.find("node"), True


def update_node(osm_token: str, cs_id: str, node_elem: ET.Element, lat: float, lon: float) -> None:
    """Update a visible node's position, preserving tags."""
    node_id = node_elem.get("id")
    version = node_elem.get("version")
    tags_xml = _tags_to_xml(node_elem)
    node_xml = (
        f'<osm><node id="{node_id}" version="{version}" changeset="{cs_id}" '
        f'lat="{lat}" lon="{lon}">{tags_xml}</node></osm>'
    )
    resp = requests.put(
        f"{OSM_API_BASE}/node/{node_id}",
        data=node_xml,
        headers=_osm_headers(osm_token),
        timeout=15,
    )
    _check_response(resp, f"update node {node_id}")


def undelete_node(osm_token: str, cs_id: str, node_elem: ET.Element, lat: float, lon: float) -> None:
    """Restore a deleted node by PUTting with visible=true."""
    node_id = node_elem.get("id")
    version = node_elem.get("version")
    tags_xml = _tags_to_xml(node_elem)
    node_xml = (
        f'<osm><node id="{node_id}" version="{version}" changeset="{cs_id}" '
        f'visible="true" lat="{lat}" lon="{lon}">{tags_xml}</node></osm>'
    )
    resp = requests.put(
        f"{OSM_API_BASE}/node/{node_id}",
        data=node_xml,
        headers=_osm_headers(osm_token),
        timeout=15,
    )
    _check_response(resp, f"undelete node {node_id}")


def fetch_way(way_id: str) -> ET.Element:
    resp = requests.get(f"{OSM_API_BASE}/way/{way_id}", timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    return root.find("way")


def update_way_node_ref(osm_token: str, cs_id: str, way_elem: ET.Element,
                        old_ref: str, new_ref: str) -> None:
    """Swap a node reference in a way, preserving everything else."""
    way_id = way_elem.get("id")
    version = way_elem.get("version")

    nds_xml = ""
    for nd in way_elem.findall("nd"):
        ref = nd.get("ref")
        if ref == new_ref:
            ref = old_ref
        nds_xml += f'<nd ref="{ref}"/>'

    tags_xml = _tags_to_xml(way_elem)
    way_xml = (
        f'<osm><way id="{way_id}" version="{version}" changeset="{cs_id}">'
        f'{nds_xml}{tags_xml}</way></osm>'
    )
    resp = requests.put(
        f"{OSM_API_BASE}/way/{way_id}",
        data=way_xml,
        headers=_osm_headers(osm_token),
        timeout=15,
    )
    _check_response(resp, f"update way {way_id}")


def comment_on_changeset(osm_token: str, changeset_id: str, text: str) -> None:
    resp = requests.post(
        f"{OSM_API_BASE}/changeset/{changeset_id}/comment",
        data={"text": text},
        headers={"Authorization": f"Bearer {osm_token}"},
        timeout=15,
    )
    resp.raise_for_status()


# -- Safety checks -------------------------------------------------------------


def _node_at_position(node_elem: ET.Element, lat: float, lon: float) -> bool:
    """Check if a node is at the given lat/lon within tolerance."""
    node_lat = float(node_elem.get("lat", 0))
    node_lon = float(node_elem.get("lon", 0))
    return abs(node_lat - lat) < POSITION_TOLERANCE and abs(node_lon - lon) < POSITION_TOLERANCE


def _way_has_node_ref(way_elem: ET.Element, node_ref: str) -> bool:
    """Check if a way's nd list contains the given ref."""
    return any(nd.get("ref") == node_ref for nd in way_elem.findall("nd"))


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


# -- Main entry point ----------------------------------------------------------


def revert_changeset(
    osm_token: str,
    changeset_id: str,
    comment: str,
    node_moves: list[NodeMove] | None = None,
    node_undeletes: list[NodeUndelete] | None = None,
    way_node_swaps: list[WayNodeSwap] | None = None,
    changeset_comment: str | None = None,
) -> RevertResult:
    """Revert specific elements from a changeset.

    Pre-flight checks are done before creating a changeset.
    Returns a RevertResult describing what was done.
    """
    node_moves = node_moves or []
    node_undeletes = node_undeletes or []
    way_node_swaps = way_node_swaps or []

    result = RevertResult()

    # -- Pre-flight checks (no changeset created yet) --------------------------

    pending_moves: list[tuple[NodeMove, ET.Element]] = []
    for nm in node_moves:
        node_elem, is_visible = fetch_node(nm.node_id)
        if not is_visible:
            result.skipped.append(f"node {nm.node_id}: deleted, cannot move")
            continue
        if not _node_at_position(node_elem, nm.new_lat, nm.new_lon):
            result.skipped.append(f"node {nm.node_id}: no longer at expected position")
            continue
        pending_moves.append((nm, node_elem))

    pending_undeletes: list[tuple[NodeUndelete, ET.Element]] = []
    for nu in node_undeletes:
        node_elem, is_visible = fetch_node(nu.node_id)
        if is_visible:
            result.skipped.append(f"node {nu.node_id}: already visible, skip undelete")
            continue
        pending_undeletes.append((nu, node_elem))

    pending_swaps: list[tuple[WayNodeSwap, ET.Element]] = []
    for ws in way_node_swaps:
        way_elem = fetch_way(ws.way_id)
        if not _way_has_node_ref(way_elem, ws.new_node_ref):
            result.skipped.append(f"way {ws.way_id}: does not reference node {ws.new_node_ref}")
            continue
        pending_swaps.append((ws, way_elem))

    # -- Nothing to do? --------------------------------------------------------

    if not pending_moves and not pending_undeletes and not pending_swaps:
        raise AlreadyRevertedError("All elements already in expected state")

    # -- Create changeset and execute ------------------------------------------

    cs_id = create_changeset(osm_token, comment)
    result.revert_changeset_id = cs_id

    try:
        # Undeletes first (ways may reference these nodes)
        for nu, node_elem in pending_undeletes:
            undelete_node(osm_token, cs_id, node_elem, nu.lat, nu.lon)
            result.nodes_undeleted.append(nu.node_id)

        # Node moves
        for nm, node_elem in pending_moves:
            update_node(osm_token, cs_id, node_elem, nm.old_lat, nm.old_lon)
            result.nodes_moved.append(nm.node_id)

        # Way node swaps
        for ws, way_elem in pending_swaps:
            update_way_node_ref(osm_token, cs_id, way_elem, ws.old_node_ref, ws.new_node_ref)
            result.ways_updated.append(ws.way_id)
    finally:
        close_changeset(osm_token, cs_id)

    # -- Comment on original changeset -----------------------------------------

    if changeset_comment:
        comment_on_changeset(osm_token, changeset_id, changeset_comment)

    return result
