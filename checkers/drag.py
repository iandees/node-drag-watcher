"""Node drag detection checker."""

import logging
import math
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance in meters between two lat/lon points."""
    R = 6_371_000  # Earth radius in meters
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def angle_at_node(prev, node, next_):
    """Angle in degrees at `node` formed by prev->node->next.

    Returns 180 for a straight line, smaller for sharper bends.
    Each argument is a (lat, lon) tuple.
    """
    v1 = (prev[0] - node[0], prev[1] - node[1])
    v2 = (next_[0] - node[0], next_[1] - node[1])
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    mag1 = math.sqrt(v1[0] ** 2 + v1[1] ** 2)
    mag2 = math.sqrt(v2[0] ** 2 + v2[1] ** 2)
    if mag1 == 0 or mag2 == 0:
        return 180.0
    cos_angle = max(-1.0, min(1.0, dot / (mag1 * mag2)))
    return math.degrees(math.acos(cos_angle))


def _get_way_membership_changes(old_way, new_way):
    """Return (added_refs, removed_refs) for a way modification."""
    old_refs = {nd.get("ref") for nd in old_way.findall("nd")}
    new_refs = {nd.get("ref") for nd in new_way.findall("nd")}
    return new_refs - old_refs, old_refs - new_refs


def _check_way_for_drag(old_way, new_way, node_info, threshold_meters):
    """Check a single way modification action for a node drag.

    Returns a list of drag dicts (usually 0 or 1 items).
    """
    old_nd_list = [
        (nd.get("ref"), float(nd.get("lat")), float(nd.get("lon")))
        for nd in old_way.findall("nd")
    ]
    new_nd_list = [
        (nd.get("ref"), float(nd.get("lat")), float(nd.get("lon")))
        for nd in new_way.findall("nd")
    ]

    old_nds = {ref: (lat, lon) for ref, lat, lon in old_nd_list}
    new_nds = {ref: (lat, lon) for ref, lat, lon in new_nd_list}

    common_refs = set(old_nds) & set(new_nds)

    # Check for same-ref moves (node kept its ID but position changed)
    moved = []
    for ref in common_refs:
        old_lat, old_lon = old_nds[ref]
        new_lat, new_lon = new_nds[ref]
        dist = haversine_distance(old_lat, old_lon, new_lat, new_lon)
        if dist >= threshold_meters:
            moved.append((ref, dist))

    # Check for node substitutions (node ref replaced by a different ref,
    # e.g. user dragged a node onto another node and the editor merged them)
    substituted = []
    old_only = [ref for ref, _, _ in old_nd_list if ref not in new_nds]
    new_only = [ref for ref, _, _ in new_nd_list if ref not in old_nds]
    if len(old_only) == 1 and len(new_only) == 1:
        old_ref = old_only[0]
        new_ref = new_only[0]
        old_lat, old_lon = old_nds[old_ref]
        new_lat, new_lon = new_nds[new_ref]
        dist = haversine_distance(old_lat, old_lon, new_lat, new_lon)
        if dist >= threshold_meters:
            substituted.append((old_ref, new_ref, dist))

    # Exactly one anomaly total, and at least one other node stayed put
    total_anomalies = len(moved) + len(substituted)
    stable_nodes = len(common_refs) - len(moved)
    if total_anomalies != 1 or stable_nodes < 1:
        return []

    way_name = ""
    for tag in new_way.findall("tag"):
        if tag.get("k") == "name":
            way_name = tag.get("v", "")
            break

    # Compute angle at the moved/substituted node
    new_refs = [ref for ref, _, _ in new_nd_list]
    old_refs = [ref for ref, _, _ in old_nd_list]

    if moved:
        node_ref, distance = moved[0]
        info = node_info.get(node_ref, {})
        changeset = info.get("changeset") or new_way.get("changeset", "")
        user = info.get("user") or new_way.get("user", "")

        # Angle at moved node in old and new geometry
        old_angle = None
        new_angle = None
        if node_ref in old_refs:
            idx = old_refs.index(node_ref)
            if 0 < idx < len(old_refs) - 1:
                old_angle = round(angle_at_node(
                    old_nds[old_refs[idx - 1]], old_nds[node_ref], old_nds[old_refs[idx + 1]]
                ), 1)
        if node_ref in new_refs:
            idx = new_refs.index(node_ref)
            if 0 < idx < len(new_refs) - 1:
                new_angle = round(angle_at_node(
                    new_nds[new_refs[idx - 1]], new_nds[node_ref], new_nds[new_refs[idx + 1]]
                ), 1)

        # Sum of angle deltas across all interior nodes of the way
        way_angle_delta_sum = None
        if old_refs == new_refs:
            total = 0.0
            for i in range(1, len(new_refs) - 1):
                r = new_refs[i]
                oa = angle_at_node(
                    old_nds[old_refs[i - 1]], old_nds[r], old_nds[old_refs[i + 1]]
                )
                na = angle_at_node(
                    new_nds[new_refs[i - 1]], new_nds[r], new_nds[new_refs[i + 1]]
                )
                total += abs(na - oa)
            way_angle_delta_sum = round(total, 1)

        return [{
            "way_id": new_way.get("id"),
            "way_name": way_name,
            "node_id": node_ref,
            "is_substitution": False,
            "distance_meters": round(distance, 1),
            "changeset": changeset,
            "user": user,
            "old_angle": old_angle,
            "new_angle": new_angle,
            "way_angle_delta_sum": way_angle_delta_sum,
            "old_way_coords": [(lat, lon) for _, lat, lon in old_nd_list],
            "new_way_coords": [(lat, lon) for _, lat, lon in new_nd_list],
            "dragged_node_old": old_nds[node_ref],
            "dragged_node_new": new_nds[node_ref],
        }]
    elif substituted:
        old_ref, new_ref, distance = substituted[0]
        changeset = new_way.get("changeset", "")
        user = new_way.get("user", "")

        # Angle at the new node position
        new_angle = None
        if new_ref in new_refs:
            idx = new_refs.index(new_ref)
            if 0 < idx < len(new_refs) - 1:
                new_angle = round(angle_at_node(
                    new_nds[new_refs[idx - 1]], new_nds[new_ref], new_nds[new_refs[idx + 1]]
                ), 1)

        old_angle = None
        if old_ref in old_refs:
            idx = old_refs.index(old_ref)
            if 0 < idx < len(old_refs) - 1:
                old_angle = round(angle_at_node(
                    old_nds[old_refs[idx - 1]], old_nds[old_ref], old_nds[old_refs[idx + 1]]
                ), 1)

        return [{
            "way_id": new_way.get("id"),
            "way_name": way_name,
            "node_id": new_ref,
            "old_node_ref": old_ref,
            "is_substitution": True,
            "distance_meters": round(distance, 1),
            "changeset": changeset,
            "user": user,
            "old_angle": old_angle,
            "new_angle": new_angle,
            "way_angle_delta_sum": None,
            "old_way_coords": [(lat, lon) for _, lat, lon in old_nd_list],
            "new_way_coords": [(lat, lon) for _, lat, lon in new_nd_list],
            "dragged_node_old": old_nds[old_ref],
            "dragged_node_new": new_nds[new_ref],
        }]

    return []


def detect_node_drags(source, threshold_meters=10):
    """Detect single-node drags in an augmented diff.

    source can be an Element (for tests) or a file path (for streaming parse).
    Returns a list of dicts with info about each detected drag.
    """
    if isinstance(source, ET.Element):
        return _detect_node_drags_tree(source, threshold_meters)
    return _detect_node_drags_file(source, threshold_meters)


def _attach_way_membership_changes(drags, way_changes):
    """Attach way_membership_changes to each drag from other ways."""
    for drag in drags:
        node_id = drag["node_id"]
        drag_way_id = drag["way_id"]
        changes = [
            entry for entry in way_changes.get(node_id, [])
            if entry["way_id"] != drag_way_id
        ]
        drag["way_membership_changes"] = changes


def _detect_node_drags_tree(root, threshold_meters):
    """Detect drags from an in-memory XML tree (two-pass)."""
    # First pass: collect changeset/user info from node modification actions
    node_info = {}
    for action in root.findall("action"):
        if action.get("type") != "modify":
            continue
        new = action.find("new")
        if new is None:
            continue
        node = new.find("node")
        if node is not None:
            node_info[node.get("id")] = {
                "changeset": node.get("changeset", ""),
                "user": node.get("user", ""),
            }

    # Second pass: look at ways for single-node drags and track membership changes
    drags = []
    # way_changes: node_ref -> [{"way_id": ..., "change": "added"/"removed"}]
    way_changes: dict[str, list[dict]] = {}
    for action in root.findall("action"):
        if action.get("type") != "modify":
            continue
        old = action.find("old")
        new = action.find("new")
        if old is None or new is None:
            continue
        old_way = old.find("way")
        new_way = new.find("way")
        if old_way is None or new_way is None:
            continue

        # Track membership changes
        way_id = new_way.get("id")
        added_refs, removed_refs = _get_way_membership_changes(old_way, new_way)
        for ref in added_refs:
            way_changes.setdefault(ref, []).append({"way_id": way_id, "change": "added"})
        for ref in removed_refs:
            way_changes.setdefault(ref, []).append({"way_id": way_id, "change": "removed"})

        drags.extend(_check_way_for_drag(old_way, new_way, node_info, threshold_meters))

    _attach_way_membership_changes(drags, way_changes)
    return drags


def _detect_node_drags_file(path, threshold_meters):
    """Detect drags by streaming an XML file (single-pass, low memory).

    Uses start/end events to skip relation actions (which can have millions
    of descendants) by clearing their children as they are parsed.
    """
    node_info = {}
    drags = []
    way_changes: dict[str, list[dict]] = {}
    root = None
    skip_action = False

    for event, elem in ET.iterparse(path, events=("start", "end")):
        if event == "start":
            if root is None:
                root = elem
            elif elem.tag == "relation":
                skip_action = True
            continue

        # event == "end"
        if elem.tag != "action":
            if skip_action:
                elem.clear()
            continue

        # End of an <action> element
        if not skip_action and elem.get("type") == "modify":
            new = elem.find("new")
            if new is not None:
                # Collect node changeset/user info
                node = new.find("node")
                if node is not None:
                    node_info[node.get("id")] = {
                        "changeset": node.get("changeset", ""),
                        "user": node.get("user", ""),
                    }

                # Check for way drags
                old = elem.find("old")
                if old is not None:
                    old_way = old.find("way")
                    new_way = new.find("way")
                    if old_way is not None and new_way is not None:
                        # Track membership changes
                        way_id = new_way.get("id")
                        added_refs, removed_refs = _get_way_membership_changes(old_way, new_way)
                        for ref in added_refs:
                            way_changes.setdefault(ref, []).append({"way_id": way_id, "change": "added"})
                        for ref in removed_refs:
                            way_changes.setdefault(ref, []).append({"way_id": way_id, "change": "removed"})

                        drags.extend(
                            _check_way_for_drag(
                                old_way, new_way, node_info, threshold_meters
                            )
                        )

        skip_action = False
        elem.clear()
        root.remove(elem)

    _attach_way_membership_changes(drags, way_changes)
    return drags


def filter_drags(drags):
    """Filter out likely intentional edits using angle analysis.

    A real accidental drag creates a sharp spike in the way geometry —
    the angle at the dragged node drops dramatically (e.g. from 170° to 5°).
    Intentional edits (road realignment) maintain smooth geometry.

    For interior nodes: require new_angle < 45° (sharp spike).
    For endpoint nodes (no angle available): only keep if the same node
    was also detected as a sharp-angle interior drag on another way.
    """
    # First pass: find nodes confirmed as drags by angle analysis
    confirmed_nodes = set()
    for drag in drags:
        new_angle = drag.get("new_angle")
        way_sum = drag.get("way_angle_delta_sum")
        is_substitution = drag.get("is_substitution", False)
        if new_angle is not None and new_angle < 45:
            # way_sum is None when node list changed (nodes added/removed);
            # for non-substitution drags this means intentional editing.
            # For substitutions, None is expected (refs differ by design).
            if is_substitution or (way_sum is not None and way_sum >= 150):
                confirmed_nodes.add(drag["node_id"])

    kept = []
    for drag in drags:
        new_angle = drag.get("new_angle")
        way_sum = drag.get("way_angle_delta_sum")
        is_substitution = drag.get("is_substitution", False)

        if new_angle is not None:
            if new_angle < 45 and (
                is_substitution or (way_sum is not None and way_sum >= 150)
            ):
                kept.append(drag)
            else:
                log.debug(
                    "Suppressing drag on way %s: new_angle=%.1f° way_sum=%s (not a drag)",
                    drag["way_id"], new_angle, way_sum,
                )
        else:
            # Endpoint node: only keep if confirmed by interior angle elsewhere
            if drag["node_id"] in confirmed_nodes:
                kept.append(drag)
            else:
                log.debug(
                    "Suppressing endpoint drag on way %s: node %s not confirmed by angle",
                    drag["way_id"], drag["node_id"],
                )
    return kept
