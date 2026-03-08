"""Watch OSM augmented diffs for accidental node drags."""

import argparse
import io
import json
import logging
import math
import os
import sys
import tempfile
import time
import urllib.parse
from collections.abc import Callable
import xml.etree.ElementTree as ET

import requests
from PIL import Image, ImageDraw

import revert as revert_mod

from checkers.drag import (
    haversine_distance,
    angle_at_node,
    detect_node_drags,
    filter_drags,
)

from pythonjsonlogger.json import JsonFormatter

_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter(
    fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
    rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
))
logging.root.addHandler(_handler)
logging.root.setLevel(logging.INFO)
log = logging.getLogger(__name__)

ADIFF_BASE = "https://adiffs.osmcha.org"
REPLICATION_STATE_URL = "https://planet.openstreetmap.org/replication/minute/state.txt"






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


def upload_slack_image(
    bot_token: str, channel_id: str, image_bytes: bytes, filename: str,
    thread_ts: str | None = None,
) -> None:
    """Upload an image to Slack and share it in a channel (optionally as a thread reply)."""
    headers = {"Authorization": f"Bearer {bot_token}"}

    # Step 1: Get upload URL
    resp = requests.get(
        "https://slack.com/api/files.getUploadURLExternal",
        params={"filename": filename, "length": len(image_bytes)},
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        log.warning("Slack getUploadURLExternal failed: %s", data.get("error"))
        return
    upload_url = data["upload_url"]
    file_id = data["file_id"]

    # Step 2: Upload the file
    resp = requests.post(upload_url, data=image_bytes, timeout=30)
    resp.raise_for_status()

    # Step 3: Complete the upload and share to channel
    complete_payload: dict = {
        "files": [{"id": file_id}],
        "channel_id": channel_id,
    }
    if thread_ts:
        complete_payload["thread_ts"] = thread_ts

    resp = requests.post(
        "https://slack.com/api/files.completeUploadExternal",
        headers=headers,
        json=complete_payload,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        log.warning("Slack completeUploadExternal failed: %s", data.get("error"))


def _format_drag_text(drags: list[dict], changeset: str, user: str) -> str:
    """Format the mrkdwn text for a changeset drag alert."""
    by_node: dict[str, list[dict]] = {}
    for drag in drags:
        by_node.setdefault(drag["node_id"], []).append(drag)

    lines = [
        f":warning: Possible node drag in "
        f"<https://osmcha.org/changesets/{changeset}|changeset {changeset}> "
        f"by {user}",
    ]

    for node_id, node_drags in by_node.items():
        distance = node_drags[0]["distance_meters"]
        node_link = f"<https://www.openstreetmap.org/node/{node_id}|{node_id}>"

        way_labels = []
        for d in node_drags:
            label = f"<https://www.openstreetmap.org/way/{d['way_id']}|{d['way_id']}>"
            if d["way_name"]:
                label += f" ({d['way_name']})"
            way_labels.append(label)

        ways_str = ", ".join(way_labels)
        lines.append(
            f"• Node {node_link} moved {distance}m — "
            f"affects way{'s' if len(node_drags) > 1 else ''} {ways_str}"
        )

    return "\n".join(lines)


def build_drag_blocks(drags: list[dict], changeset: str, user: str) -> tuple[str, list[dict]]:
    """Build Block Kit blocks for a changeset alert with one revert button.

    Returns (text_fallback, blocks).
    """
    text = _format_drag_text(drags, changeset, user)

    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
    ]

    # Collect all affected node and way IDs across all drags in this changeset
    node_ids: list[str] = []
    way_ids_set: set[str] = set()
    seen_nodes: set[str] = set()
    for drag in drags:
        if drag["node_id"] not in seen_nodes:
            seen_nodes.add(drag["node_id"])
            node_ids.append(drag["node_id"])
        way_ids_set.add(drag["way_id"])
        # Include ways from membership changes
        for mc in drag.get("way_membership_changes", []):
            way_ids_set.add(mc["way_id"])

    way_ids = sorted(way_ids_set)

    value_dict = {
        "node_ids": node_ids,
        "way_ids": way_ids,
        "changeset": changeset,
    }

    button_value = json.dumps(value_dict)
    n_nodes = len(node_ids)
    n_ways = len(way_ids)

    blocks.append({
        "type": "actions",
        "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "Revert"},
            "style": "danger",
            "action_id": "revert_node_drag",
            "value": button_value,
            "confirm": {
                "title": {"type": "plain_text", "text": "Confirm Revert"},
                "text": {
                    "type": "mrkdwn",
                    "text": f"Revert {n_nodes} node{'s' if n_nodes != 1 else ''} and {n_ways} way{'s' if n_ways != 1 else ''}?",
                },
                "confirm": {"type": "plain_text", "text": "Revert"},
                "deny": {"type": "plain_text", "text": "Cancel"},
            },
        }],
    })

    return text, blocks


def _upload_node_images(
    bot_token: str, channel_id: str, drags: list[dict], thread_ts: str | None = None,
) -> None:
    """Generate and upload one image per unique dragged node as a thread reply."""
    by_node: dict[str, list[dict]] = {}
    for drag in drags:
        by_node.setdefault(drag["node_id"], []).append(drag)

    for node_id, node_drags in by_node.items():
        try:
            image_bytes = generate_drag_image(node_drags)
            if image_bytes:
                filename = f"drag_node{node_id}.png"
                upload_slack_image(bot_token, channel_id, image_bytes, filename, thread_ts)
        except Exception:
            log.debug("Failed to upload drag image for node %s", node_id, exc_info=True)


def _post_reverter_link(
    bot_token: str, channel_id: str, drags: list[dict], changeset: str,
    thread_ts: str | None = None,
) -> None:
    """Post a link to the OSM reverter as a threaded reply."""
    if not thread_ts:
        return

    # Collect affected node and way IDs
    node_ids: set[str] = set()
    way_ids: set[str] = set()
    for drag in drags:
        node_ids.add(drag["node_id"])
        way_ids.add(drag["way_id"])

    query_parts = [f"n{nid}" for nid in sorted(node_ids)] + [f"w{wid}" for wid in sorted(way_ids)]
    query_filter = ",".join(query_parts)

    discussion = (
        "Hi! It looks like a node in this changeset was "
        "accidentally moved. This can happen when panning "
        "the map. The node has been moved back to its "
        "previous position."
    )

    params = urllib.parse.urlencode({
        "changesets": changeset,
        "query-filter": query_filter,
        "comment": f"Revert accidental node drag from changeset {changeset}",
        "discussion": discussion,
    })

    reverter_url = f"https://revert.monicz.dev/?{params}"
    text = f"<{reverter_url}|:leftwards_arrow_with_hook: Open in Reverter>"

    _post_slack_message(bot_token, channel_id, text, thread_ts=thread_ts)


def _post_slack_message(
    bot_token: str, channel_id: str, text: str, blocks: list[dict] | None = None,
    thread_ts: str | None = None,
) -> str | None:
    """Post a message via chat.postMessage. Returns the message ts or None."""
    payload: dict = {"channel": channel_id, "text": text}
    if blocks:
        payload["blocks"] = blocks
    if thread_ts:
        payload["thread_ts"] = thread_ts

    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {bot_token}"},
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        log.warning("Slack chat.postMessage failed: %s", data.get("error"))
        return None
    return data.get("ts")


def send_slack_interactive(bot_token: str, channel_id: str, drags: list[dict]) -> None:
    """Post alerts via chat.postMessage with Block Kit blocks + buttons."""
    by_changeset: dict[str, list[dict]] = {}
    for drag in drags:
        by_changeset.setdefault(drag["changeset"], []).append(drag)

    for changeset, cs_drags in by_changeset.items():
        user = cs_drags[0]["user"]
        text, blocks = build_drag_blocks(cs_drags, changeset, user)
        ts = _post_slack_message(bot_token, channel_id, text, blocks)
        _upload_node_images(bot_token, channel_id, cs_drags, ts)
        _post_reverter_link(bot_token, channel_id, cs_drags, changeset, ts)


def handle_revert_action(ack: Callable, body: dict, client: object, osm_token: str,
                         api_base: str = revert_mod.DEFAULT_OSM_API_BASE) -> None:
    """Slack Bolt action handler for revert_node_drag buttons."""
    ack()

    action = body["actions"][0]
    value = json.loads(action["value"])
    node_ids = value["node_ids"]
    way_ids = value["way_ids"]
    original_changeset = value["changeset"]

    user = body["user"]["username"]
    channel = body["channel"]["id"]
    ts = body["message"]["ts"]

    comment = f"Revert accidental node drag from changeset {original_changeset}"
    node_list = ", ".join(node_ids)
    changeset_comment = (
        f"Hi! It looks like a node in this changeset was "
        f"accidentally moved. This can happen when panning "
        f"the map. The node has been moved back to its "
        f"previous position."
    )

    try:
        result = revert_mod.revert_changeset(
            osm_token, original_changeset, comment,
            node_ids=node_ids,
            way_ids=way_ids,
            changeset_comment=changeset_comment,
            api_base=api_base,
        )
        cs_id = result.revert_changeset_id

        # Update the message: remove buttons, add confirmation
        original_blocks = body["message"].get("blocks", [])
        new_blocks = [b for b in original_blocks if b.get("type") != "actions"]
        new_blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": (
                    f":white_check_mark: Reverted by @{user} in "
                    f"<https://www.openstreetmap.org/changeset/{cs_id}|changeset {cs_id}>"
                ),
            }],
        })

        client.chat_update(channel=channel, ts=ts, blocks=new_blocks, text="Reverted")

    except revert_mod.AlreadyRevertedError:
        _update_message_error(body, client, "Already reverted, nothing to do.")

    except revert_mod.ConflictError as e:
        log.error("Conflict during revert: %s", e)
        _update_message_error(body, client, f"Conflict: {e}")

    except revert_mod.AuthError as e:
        log.error("OSM auth failed: %s", e)
        _update_message_error(body, client, f"OSM auth failed: {e}")

    except Exception as e:
        log.exception("Revert failed for changeset %s", original_changeset)
        _update_message_error(body, client, f"Revert failed: {e}")


def _update_message_error(body: dict, client: object, error_msg: str) -> None:
    """Replace buttons with an error context block."""
    channel = body["channel"]["id"]
    ts = body["message"]["ts"]
    original_blocks = body["message"].get("blocks", [])
    new_blocks = [b for b in original_blocks if b.get("type") != "actions"]
    new_blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": f":x: {error_msg}",
        }],
    })
    client.chat_update(channel=channel, ts=ts, blocks=new_blocks, text=error_msg)


def start_socket_mode(app_token: str, bot_token: str, osm_token: str) -> None:
    """Start Slack Socket Mode in a daemon thread to handle button interactions."""
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = App(token=bot_token)

    @app.action("revert_node_drag")
    def _handle(ack, body, client):
        handle_revert_action(ack, body, client, osm_token)

    handler = SocketModeHandler(app, app_token)
    handler.connect()
    log.info("Socket Mode started for interactive revert buttons")


def send_slack_summary(bot_token: str, channel_id: str, drags: list[dict], interactive: bool = False) -> None:
    """Post one Slack message per changeset summarizing detected drags."""
    if interactive:
        send_slack_interactive(bot_token, channel_id, drags)
        return

    by_changeset: dict[str, list[dict]] = {}
    for drag in drags:
        by_changeset.setdefault(drag["changeset"], []).append(drag)

    for changeset, cs_drags in by_changeset.items():
        user = cs_drags[0]["user"]
        text = _format_drag_text(cs_drags, changeset, user)
        ts = _post_slack_message(bot_token, channel_id, text)
        _upload_node_images(bot_token, channel_id, cs_drags, ts)
        _post_reverter_link(bot_token, channel_id, cs_drags, changeset, ts)


def fetch_adiff(url):
    """Fetch augmented diff XML to a temp file. Caller must delete the file."""
    resp = requests.get(url, timeout=120, stream=True, headers={"User-Agent": "node-drag-watcher/0.1"})
    resp.raise_for_status()
    f = tempfile.NamedTemporaryFile(delete=False, suffix=".adiff")
    try:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
        f.close()
        return f.name
    except Exception:
        f.close()
        os.unlink(f.name)
        raise


def get_latest_sequence():
    """Get the latest replication sequence number from OSM."""
    resp = requests.get(REPLICATION_STATE_URL, timeout=10, allow_redirects=True, headers={"User-Agent": "node-drag-watcher/0.1"})
    resp.raise_for_status()
    for line in resp.text.splitlines():
        if line.startswith("sequenceNumber="):
            return int(line.split("=")[1])
    raise ValueError("Could not parse sequence number from state.txt")


def read_state(state_file):
    """Read the last processed sequence number from state file."""
    try:
        with open(state_file) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def write_state(state_file, seq):
    """Write the last processed sequence number to state file."""
    with open(state_file, "w") as f:
        f.write(str(seq))


def process_adiff(url: str, threshold_meters: float, bot_token: str | None = None, channel_id: str | None = None, interactive: bool = False) -> list[dict]:
    """Fetch an adiff, detect drags, and optionally alert."""
    path = fetch_adiff(url)
    try:
        drags = detect_node_drags(path, threshold_meters=threshold_meters)
    finally:
        os.unlink(path)
    drags = filter_drags(drags)
    for drag in drags:
        log.info(
            "Node drag: way %s node %s moved %.1fm (changeset %s by %s)",
            drag["way_id"], drag["node_id"], drag["distance_meters"],
            drag["changeset"], drag["user"],
        )
    if drags and bot_token and channel_id:
        send_slack_summary(bot_token, channel_id, drags, interactive=interactive)
    return drags


def run_polling(threshold_meters: float, state_file: str, bot_token: str, channel_id: str, interactive: bool = False) -> None:
    """Continuously poll for new replication diffs and process them."""
    seq = read_state(state_file)
    if seq is None:
        seq = get_latest_sequence()
        log.info("No state file found, starting from sequence %d", seq)
        write_state(state_file, seq)

    while True:
        try:
            latest = get_latest_sequence()
            if latest <= seq:
                log.debug("No new diffs (at %d)", seq)
                time.sleep(60)
                continue

            for s in range(seq + 1, latest + 1):
                url = f"{ADIFF_BASE}/replication/minute/{s}.adiff"
                log.info("Processing sequence %d", s)
                try:
                    process_adiff(url, threshold_meters, bot_token, channel_id, interactive)
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code == 404:
                        log.debug("Sequence %d not yet available, will retry", s)
                        break
                    log.warning("Failed to fetch sequence %d: %s", s, e)
                write_state(state_file, s)
                seq = s
        except Exception:
            log.exception("Error in polling loop")

        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="Watch OSM diffs for node drags")
    parser.add_argument(
        "--changeset",
        type=int,
        help="Process a single changeset ID and exit",
    )
    args = parser.parse_args()

    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    channel_id = os.environ.get("SLACK_CHANNEL_ID")
    app_token = os.environ.get("SLACK_APP_TOKEN")
    osm_token = os.environ.get("OSM_ACCESS_TOKEN")
    threshold = float(os.environ.get("DRAG_THRESHOLD_METERS", "10"))
    state_file = os.environ.get("STATE_FILE", "/app/state/state.txt")

    # SLACK_BOT_TOKEN and SLACK_CHANNEL_ID are always required
    if not bot_token:
        log.error("SLACK_BOT_TOKEN is required.")
        sys.exit(1)
    if not channel_id:
        log.error("SLACK_CHANNEL_ID is required.")
        sys.exit(1)

    # Interactive revert requires SLACK_APP_TOKEN and OSM_ACCESS_TOKEN
    interactive = bool(app_token and osm_token)
    if app_token and not osm_token:
        log.error("SLACK_APP_TOKEN is set but OSM_ACCESS_TOKEN is missing.")
        sys.exit(1)
    if osm_token and not app_token:
        log.error("OSM_ACCESS_TOKEN is set but SLACK_APP_TOKEN is missing.")
        sys.exit(1)

    if interactive:
        try:
            start_socket_mode(app_token, bot_token, osm_token)
        except Exception:
            log.warning("Failed to start Socket Mode (revert buttons won't work)", exc_info=True)

    if args.changeset:
        url = f"{ADIFF_BASE}/changesets/{args.changeset}.adiff"
        log.info("Processing changeset %d", args.changeset)
        drags = process_adiff(url, threshold, bot_token, channel_id, interactive)
        if not drags:
            log.info("No node drags detected")
        sys.exit(0)

    if interactive:
        log.info("Interactive revert buttons enabled")

    run_polling(threshold, state_file, bot_token, channel_id, interactive)


if __name__ == "__main__":
    main()
