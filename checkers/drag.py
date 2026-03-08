"""Node drag detection checker."""

import io
import logging
import math
import xml.etree.ElementTree as ET
from collections.abc import Iterable

import requests
from PIL import Image, ImageDraw

from checkers import Action

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


def _check_way_action_for_drag(action: Action, node_info: dict, threshold_meters: float) -> list[dict]:
    """Check a single way modify action for a node drag.

    Returns a list of drag dicts (usually 0 or 1 items).
    """
    if (action.nd_refs_old is None or action.nd_refs_new is None
            or action.node_coords_old is None or action.node_coords_new is None):
        return []

    old_nds = action.node_coords_old
    new_nds = action.node_coords_new
    old_refs = action.nd_refs_old
    new_refs = action.nd_refs_new

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
    old_only = [ref for ref in old_refs if ref not in new_nds]
    new_only = [ref for ref in new_refs if ref not in old_nds]
    if len(old_only) == 1 and len(new_only) == 1:
        old_ref = old_only[0]
        new_ref = new_only[0]
        if old_ref in old_nds and new_ref in new_nds:
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

    way_name = action.tags_new.get("name", "")

    old_way_coords = [(old_nds[r][0], old_nds[r][1]) for r in old_refs if r in old_nds]
    new_way_coords = [(new_nds[r][0], new_nds[r][1]) for r in new_refs if r in new_nds]

    if moved:
        node_ref, distance = moved[0]
        info = node_info.get(node_ref, {})
        changeset = info.get("changeset") or action.changeset
        user = info.get("user") or action.user

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
            "way_id": action.element_id,
            "way_name": way_name,
            "node_id": node_ref,
            "is_substitution": False,
            "distance_meters": round(distance, 1),
            "changeset": changeset,
            "user": user,
            "old_angle": old_angle,
            "new_angle": new_angle,
            "way_angle_delta_sum": way_angle_delta_sum,
            "old_way_coords": old_way_coords,
            "new_way_coords": new_way_coords,
            "dragged_node_old": old_nds[node_ref],
            "dragged_node_new": new_nds[node_ref],
        }]
    elif substituted:
        old_ref, new_ref, distance = substituted[0]

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
            "way_id": action.element_id,
            "way_name": way_name,
            "node_id": new_ref,
            "old_node_ref": old_ref,
            "is_substitution": True,
            "distance_meters": round(distance, 1),
            "changeset": action.changeset,
            "user": action.user,
            "old_angle": old_angle,
            "new_angle": new_angle,
            "way_angle_delta_sum": None,
            "old_way_coords": old_way_coords,
            "new_way_coords": new_way_coords,
            "dragged_node_old": old_nds[old_ref],
            "dragged_node_new": new_nds[new_ref],
        }]

    return []


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


def detect_drags_from_actions(actions: Iterable[Action], threshold_meters: float = 10) -> list[dict]:
    """Detect single-node drags from an iterable of Action objects.

    Two-pass: first collects node changeset/user info, then checks ways.
    For streaming use, pass a list (actions are consumed twice).
    """
    actions = list(actions)

    # First pass: collect changeset/user info from node modifications
    node_info = {}
    for action in actions:
        if action.action_type == "modify" and action.element_type == "node":
            node_info[action.element_id] = {
                "changeset": action.changeset,
                "user": action.user,
            }

    # Second pass: check ways for drags and track membership changes
    drags = []
    way_changes: dict[str, list[dict]] = {}
    for action in actions:
        if action.action_type != "modify" or action.element_type != "way":
            continue
        if action.nd_refs_old is None or action.nd_refs_new is None:
            continue

        # Track membership changes
        old_set = set(action.nd_refs_old)
        new_set = set(action.nd_refs_new)
        for ref in new_set - old_set:
            way_changes.setdefault(ref, []).append({"way_id": action.element_id, "change": "added"})
        for ref in old_set - new_set:
            way_changes.setdefault(ref, []).append({"way_id": action.element_id, "change": "removed"})

        drags.extend(_check_way_action_for_drag(action, node_info, threshold_meters))

    _attach_way_membership_changes(drags, way_changes)
    return drags


def detect_node_drags(source, threshold_meters=10):
    """Detect single-node drags in an augmented diff.

    source can be an Element (for tests) or a file path (for streaming parse).
    Returns a list of dicts with info about each detected drag.

    Deprecated: prefer detect_drags_from_actions() with pre-parsed Actions.
    """
    from watcher import parse_adiff_actions, iter_adiff_actions_from_file
    if isinstance(source, ET.Element):
        actions = parse_adiff_actions(source)
    else:
        actions = list(iter_adiff_actions_from_file(source))
    return detect_drags_from_actions(actions, threshold_meters)


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


def _lon_to_tile_x(lon, zoom):
    """Convert longitude to fractional tile X coordinate."""
    return (lon + 180.0) / 360.0 * (2 ** zoom)


def _lat_to_tile_y(lat, zoom):
    """Convert latitude to fractional tile Y coordinate."""
    lat_rad = math.radians(lat)
    return (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * (2 ** zoom)


def _latlon_to_pixel(lat, lon, zoom, origin_tx, origin_ty):
    """Convert lat/lon to pixel coordinates relative to tile origin."""
    x = (_lon_to_tile_x(lon, zoom) - origin_tx) * 256
    y = (_lat_to_tile_y(lat, zoom) - origin_ty) * 256
    return int(x), int(y)


def _choose_zoom(min_lat, min_lon, max_lat, max_lon, target_size=512):
    """Choose a zoom level so the bounding box fits within target_size pixels."""
    for zoom in range(18, 0, -1):
        x_span = (_lon_to_tile_x(max_lon, zoom) - _lon_to_tile_x(min_lon, zoom)) * 256
        y_span = (_lat_to_tile_y(min_lat, zoom) - _lat_to_tile_y(max_lat, zoom)) * 256
        if x_span <= target_size and y_span <= target_size:
            return zoom
    return 1


def generate_drag_image(drags: list[dict]) -> bytes | None:
    """Generate a PNG image showing all affected ways for a node drag.

    drags is a list of drag dicts for the same node (one per affected way).
    Returns PNG bytes or None on failure.
    """
    if not drags:
        return None

    node_old = drags[0].get("dragged_node_old")
    node_new = drags[0].get("dragged_node_new")
    if not node_old or not node_new:
        return None

    # Collect all way coords across all affected ways
    way_pairs: list[tuple[list, list]] = []
    for drag in drags:
        old_coords = drag.get("old_way_coords", [])
        new_coords = drag.get("new_way_coords", [])
        if not old_coords or not new_coords:
            continue
        way_pairs.append((old_coords, new_coords))

    if not way_pairs:
        return None

    # Focus bounding box on the dragged node and its nearby neighbors
    # rather than the entire way (which can be hundreds of meters long)
    NEIGHBOR_COUNT = 3  # nodes on each side of the dragged node
    focus_lats: list[float] = [node_old[0], node_new[0]]
    focus_lons: list[float] = [node_old[1], node_new[1]]
    for old_coords, new_coords in way_pairs:
        for coords, ref_pos in [(old_coords, node_old), (new_coords, node_new)]:
            # Find the dragged node in this coord list
            best_idx = None
            best_dist = float("inf")
            for i, (lat, lon) in enumerate(coords):
                d = abs(lat - ref_pos[0]) + abs(lon - ref_pos[1])
                if d < best_dist:
                    best_dist = d
                    best_idx = i
            if best_idx is not None:
                start = max(0, best_idx - NEIGHBOR_COUNT)
                end = min(len(coords), best_idx + NEIGHBOR_COUNT + 1)
                for lat, lon in coords[start:end]:
                    focus_lats.append(lat)
                    focus_lons.append(lon)

    padding = 0.2
    lat_range = max(focus_lats) - min(focus_lats) or 0.001
    lon_range = max(focus_lons) - min(focus_lons) or 0.001
    min_lat = min(focus_lats) - lat_range * padding
    max_lat = max(focus_lats) + lat_range * padding
    min_lon = min(focus_lons) - lon_range * padding
    max_lon = max(focus_lons) + lon_range * padding

    zoom = _choose_zoom(min_lat, min_lon, max_lat, max_lon)

    tx_min = int(_lon_to_tile_x(min_lon, zoom))
    tx_max = int(_lon_to_tile_x(max_lon, zoom))
    ty_min = int(_lat_to_tile_y(max_lat, zoom))
    ty_max = int(_lat_to_tile_y(min_lat, zoom))

    img_w = (tx_max - tx_min + 1) * 256
    img_h = (ty_max - ty_min + 1) * 256
    img = Image.new("RGB", (img_w, img_h))

    for ty in range(ty_min, ty_max + 1):
        for tx in range(tx_min, tx_max + 1):
            tile_url = f"https://tile.openstreetmap.org/{zoom}/{tx}/{ty}.png"
            try:
                resp = requests.get(
                    tile_url,
                    timeout=10,
                    headers={"User-Agent": "node-drag-watcher/0.1"},
                )
                resp.raise_for_status()
                tile = Image.open(io.BytesIO(resp.content))
                img.paste(tile, ((tx - tx_min) * 256, (ty - ty_min) * 256))
            except Exception:
                log.debug("Failed to fetch tile %s/%s/%s", zoom, tx, ty)

    draw = ImageDraw.Draw(img)

    def to_px(lat: float, lon: float) -> tuple[int, int]:
        return _latlon_to_pixel(lat, lon, zoom, tx_min, ty_min)

    # Draw all ways
    for old_coords, new_coords in way_pairs:
        if len(old_coords) >= 2:
            draw.line([to_px(lat, lon) for lat, lon in old_coords], fill=(0, 100, 255), width=3)
        if len(new_coords) >= 2:
            draw.line([to_px(lat, lon) for lat, lon in new_coords], fill=(255, 50, 50), width=3)

    # Draw dragged node positions
    ox, oy = to_px(*node_old)
    draw.ellipse([ox - 6, oy - 6, ox + 6, oy + 6], fill=(0, 100, 255), outline=(255, 255, 255), width=2)
    nx, ny = to_px(*node_new)
    draw.ellipse([nx - 6, ny - 6, nx + 6, ny + 6], fill=(255, 50, 50), outline=(255, 255, 255), width=2)

    # Arrow from old to new
    draw.line([(ox, oy), (nx, ny)], fill=(80, 80, 80), width=1)
    dx, dy = nx - ox, ny - oy
    length = math.sqrt(dx * dx + dy * dy)
    if length > 0:
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        head_len = min(8, length * 0.3)
        head_w = head_len * 0.5
        draw.polygon([
            (nx, ny),
            (nx - ux * head_len + px * head_w, ny - uy * head_len + py * head_w),
            (nx - ux * head_len - px * head_w, ny - uy * head_len - py * head_w),
        ], fill=(80, 80, 80))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
