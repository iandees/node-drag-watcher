"""Watch OSM augmented diffs for accidental node drags."""

import argparse
import logging
import math
import os
import sys
import time
import xml.etree.ElementTree as ET

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

ADIFF_BASE = "https://adiffs.osmcha.org"
REPLICATION_STATE_URL = "https://planet.openstreetmap.org/replication/minute/state.txt"


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance in meters between two lat/lon points."""
    R = 6_371_000  # Earth radius in meters
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def detect_node_drags(root, threshold_meters=10):
    """Detect single-node drags in an augmented diff XML tree.

    Returns a list of dicts with info about each detected drag.
    """
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

    # Second pass: look at ways for single-node drags
    drags = []

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
            continue

        way_name = ""
        for tag in new_way.findall("tag"):
            if tag.get("k") == "name":
                way_name = tag.get("v", "")
                break

        if moved:
            node_ref, distance = moved[0]
            info = node_info.get(node_ref, {})
            changeset = info.get("changeset") or new_way.get("changeset", "")
            user = info.get("user") or new_way.get("user", "")

            drags.append({
                "way_id": new_way.get("id"),
                "way_name": way_name,
                "node_id": node_ref,
                "distance_meters": round(distance, 1),
                "changeset": changeset,
                "user": user,
            })
        elif substituted:
            old_ref, new_ref, distance = substituted[0]
            # Attribution: use the new way's changeset since the way itself
            # was modified (node list changed)
            changeset = new_way.get("changeset", "")
            user = new_way.get("user", "")

            drags.append({
                "way_id": new_way.get("id"),
                "way_name": way_name,
                "node_id": f"{old_ref}->{new_ref}",
                "distance_meters": round(distance, 1),
                "changeset": changeset,
                "user": user,
            })

    return drags


def send_slack_alert(webhook_url, drag):
    """Post a node drag alert to Slack."""
    way_label = f"way {drag['way_id']}"
    if drag["way_name"]:
        way_label += f" ({drag['way_name']})"

    text = (
        f":warning: Possible node drag detected\n"
        f"*{way_label}*: node {drag['node_id']} moved {drag['distance_meters']}m\n"
        f"User: {drag['user']} | "
        f"<https://osmcha.org/changesets/{drag['changeset']}|Changeset {drag['changeset']}> | "
        f"<https://www.openstreetmap.org/node/{drag['node_id']}|Node {drag['node_id']}>"
    )
    requests.post(webhook_url, json={"text": text}, timeout=10)


def fetch_adiff(url):
    """Fetch and parse an augmented diff XML from a URL."""
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return ET.fromstring(resp.content)


def get_latest_sequence():
    """Get the latest replication sequence number from OSM."""
    resp = requests.get(REPLICATION_STATE_URL, timeout=10, allow_redirects=True)
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


def process_adiff(url, threshold_meters, webhook_url=None):
    """Fetch an adiff, detect drags, and optionally alert."""
    root = fetch_adiff(url)
    drags = detect_node_drags(root, threshold_meters=threshold_meters)
    for drag in drags:
        log.info(
            "Node drag: way %s node %s moved %.1fm (changeset %s by %s)",
            drag["way_id"], drag["node_id"], drag["distance_meters"],
            drag["changeset"], drag["user"],
        )
        if webhook_url:
            send_slack_alert(webhook_url, drag)
    return drags


def run_polling(webhook_url, threshold_meters, state_file):
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
                    process_adiff(url, threshold_meters, webhook_url)
                except requests.HTTPError as e:
                    log.warning("Failed to fetch sequence %d: %s", s, e)
                write_state(state_file, s)

            seq = latest
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

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    threshold = float(os.environ.get("DRAG_THRESHOLD_METERS", "10"))
    state_file = os.environ.get("STATE_FILE", "/app/state/state.txt")

    if args.changeset:
        url = f"{ADIFF_BASE}/changesets/{args.changeset}.adiff"
        log.info("Processing changeset %d", args.changeset)
        drags = process_adiff(url, threshold, webhook_url)
        if not drags:
            log.info("No node drags detected")
        sys.exit(0)

    if not webhook_url:
        log.warning("SLACK_WEBHOOK_URL not set, will only log detections")

    run_polling(webhook_url, threshold, state_file)


if __name__ == "__main__":
    main()
