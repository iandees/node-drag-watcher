"""Watch OSM augmented diffs for accidental node drags."""

import argparse
import logging
import os
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

import requests

from checkers import Action, Issue
from checkers.drag import (
    detect_node_drags,
    filter_drags,
)
from checkers.phone import PhoneChecker
from checkers.website import WebsiteChecker
from notifiers.slack import (
    send_slack_summary,
    send_tag_issue_summary,
    start_socket_mode,
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


def _extract_tags(elem: ET.Element) -> dict[str, str]:
    """Extract tags from an OSM element."""
    return {tag.get("k"): tag.get("v", "") for tag in elem.findall("tag")}


def _extract_node_action(action_type: str, old_elem, new_elem) -> Action:
    """Build an Action from a node action element."""
    primary = new_elem if new_elem is not None else old_elem
    return Action(
        action_type=action_type,
        element_type="node",
        element_id=primary.get("id"),
        version=primary.get("version", ""),
        changeset=primary.get("changeset", ""),
        user=primary.get("user", ""),
        tags_old=_extract_tags(old_elem) if old_elem is not None else {},
        tags_new=_extract_tags(new_elem) if new_elem is not None else {},
        coords_old=(float(old_elem.get("lat")), float(old_elem.get("lon")))
            if old_elem is not None and old_elem.get("lat") else None,
        coords_new=(float(new_elem.get("lat")), float(new_elem.get("lon")))
            if new_elem is not None and new_elem.get("lat") else None,
    )


def _extract_way_action(action_type: str, old_elem, new_elem) -> Action:
    """Build an Action from a way action element."""
    primary = new_elem if new_elem is not None else old_elem

    def _way_data(way):
        if way is None:
            return None, None, {}
        refs = [nd.get("ref") for nd in way.findall("nd")]
        coords = {}
        for nd in way.findall("nd"):
            lat, lon = nd.get("lat"), nd.get("lon")
            if lat and lon:
                coords[nd.get("ref")] = (float(lat), float(lon))
        return refs, coords if coords else None, _extract_tags(way)

    old_refs, old_coords, old_tags = _way_data(old_elem)
    new_refs, new_coords, new_tags = _way_data(new_elem)

    return Action(
        action_type=action_type,
        element_type="way",
        element_id=primary.get("id"),
        version=primary.get("version", ""),
        changeset=primary.get("changeset", ""),
        user=primary.get("user", ""),
        tags_old=old_tags,
        tags_new=new_tags,
        nd_refs_old=old_refs,
        nd_refs_new=new_refs,
        node_coords_old=old_coords,
        node_coords_new=new_coords,
    )


def _extract_relation_action(action_type: str, old_elem, new_elem) -> Action:
    """Build an Action from a relation action element."""
    primary = new_elem if new_elem is not None else old_elem
    return Action(
        action_type=action_type,
        element_type="relation",
        element_id=primary.get("id"),
        version=primary.get("version", ""),
        changeset=primary.get("changeset", ""),
        user=primary.get("user", ""),
        tags_old=_extract_tags(old_elem) if old_elem is not None else {},
        tags_new=_extract_tags(new_elem) if new_elem is not None else {},
    )


def parse_adiff_actions(root: ET.Element) -> list[Action]:
    """Parse an augmented diff XML tree into Action objects.

    Handles all element types (node, way, relation) and action types
    (create, modify, delete).
    """
    actions = []
    for action_elem in root.findall("action"):
        action_type = action_elem.get("type")
        old = action_elem.find("old")
        new = action_elem.find("new")

        old_child = None
        new_child = None

        # Determine element type from whichever side exists
        if new is not None:
            for elem_type in ("node", "way", "relation"):
                new_child = new.find(elem_type)
                if new_child is not None:
                    if old is not None:
                        old_child = old.find(elem_type)
                    break
        elif old is not None:
            for elem_type in ("node", "way", "relation"):
                old_child = old.find(elem_type)
                if old_child is not None:
                    break

        if new_child is None and old_child is None:
            continue

        elem_type = (new_child if new_child is not None else old_child).tag

        if elem_type == "node":
            actions.append(_extract_node_action(action_type, old_child, new_child))
        elif elem_type == "way":
            actions.append(_extract_way_action(action_type, old_child, new_child))
        elif elem_type == "relation":
            actions.append(_extract_relation_action(action_type, old_child, new_child))

    return actions




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


_tag_checkers = [PhoneChecker(), WebsiteChecker()]


def process_adiff(url: str, threshold_meters: float, bot_token: str | None = None, channel_id: str | None = None, interactive: bool = False) -> list[dict]:
    """Fetch an adiff, detect drags and tag issues, and optionally alert."""
    path = fetch_adiff(url)
    try:
        drags = detect_node_drags(path, threshold_meters=threshold_meters)

        # Parse again for tag checkers (adiff files are small enough)
        root = ET.parse(path).getroot()
        actions = parse_adiff_actions(root)
    finally:
        os.unlink(path)

    drags = filter_drags(drags)
    for drag in drags:
        log.info(
            "Node drag: way %s node %s moved %.1fm (changeset %s by %s)",
            drag["way_id"], drag["node_id"], drag["distance_meters"],
            drag["changeset"], drag["user"],
        )

    # Run tag checkers
    tag_issues: list[Issue] = []
    for action in actions:
        for checker in _tag_checkers:
            tag_issues.extend(checker.check(action))

    for issue in tag_issues:
        log.info("Tag issue: %s %s — %s", issue.element_type, issue.element_id, issue.summary)

    if bot_token and channel_id:
        if drags:
            send_slack_summary(bot_token, channel_id, drags, interactive=interactive)
        if tag_issues:
            send_tag_issue_summary(bot_token, channel_id, tag_issues, interactive=interactive)

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
