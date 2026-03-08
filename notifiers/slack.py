"""Slack notification for OSM watcher issues."""

import json
import logging
import urllib.parse
from collections.abc import Callable

import requests

import revert as revert_mod
from checkers.drag import generate_drag_image

log = logging.getLogger(__name__)


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
    changeset_comment = (
        "Hi! It looks like a node in this changeset was "
        "accidentally moved. This can happen when panning "
        "the map. The node has been moved back to its "
        "previous position."
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
