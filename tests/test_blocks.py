import json
from watcher import build_drag_blocks


def _make_drag(**overrides):
    drag = {
        "way_id": "111",
        "way_name": "Main St",
        "node_id": "42",
        "distance_meters": 100.0,
        "changeset": "999",
        "user": "bob",
        "old_angle": 180.0,
        "new_angle": 3.0,
        "dragged_node_old": (51.0, -1.0),
        "dragged_node_new": (51.1, -1.1),
    }
    drag.update(overrides)
    return drag


def test_basic_structure():
    drags = [_make_drag()]
    text, blocks = build_drag_blocks(drags, "999", "bob")

    assert "999" in text
    assert "bob" in text

    types = [b["type"] for b in blocks]
    assert "section" in types
    assert "actions" in types


def test_button_value_contains_required_fields():
    drags = [_make_drag()]
    _, blocks = build_drag_blocks(drags, "999", "bob")

    actions_block = [b for b in blocks if b["type"] == "actions"][0]
    button = actions_block["elements"][0]
    value = json.loads(button["value"])

    assert value["node_ids"] == ["42"]
    assert "111" in value["way_ids"]
    assert value["changeset"] == "999"
    # Old fields should NOT be present
    assert "old_lat" not in value
    assert "new_lat" not in value
    assert "is_substitution" not in value
    assert "old_node_ref" not in value
    assert "way_membership_changes" not in value


def test_button_has_confirm_dialog():
    drags = [_make_drag()]
    _, blocks = build_drag_blocks(drags, "999", "bob")

    actions_block = [b for b in blocks if b["type"] == "actions"][0]
    button = actions_block["elements"][0]

    assert "confirm" in button
    assert button["style"] == "danger"
    assert button["action_id"] == "revert_node_drag"


def test_one_button_per_changeset():
    """Multiple drags for same changeset → one button."""
    drags = [
        _make_drag(way_id="111"),
        _make_drag(way_id="222"),
    ]
    _, blocks = build_drag_blocks(drags, "999", "bob")

    actions_blocks = [b for b in blocks if b["type"] == "actions"]
    assert len(actions_blocks) == 1


def test_multiple_nodes_in_one_button():
    """Multiple nodes in same changeset → one button with all node_ids."""
    drags = [
        _make_drag(node_id="42", way_id="111"),
        _make_drag(node_id="43", way_id="222"),
    ]
    _, blocks = build_drag_blocks(drags, "999", "bob")

    actions_blocks = [b for b in blocks if b["type"] == "actions"]
    assert len(actions_blocks) == 1

    button = actions_blocks[0]["elements"][0]
    value = json.loads(button["value"])
    assert value["node_ids"] == ["42", "43"]
    assert "111" in value["way_ids"]
    assert "222" in value["way_ids"]


def test_button_value_includes_membership_change_ways():
    """Way IDs from membership changes are included in way_ids."""
    drags = [_make_drag(way_membership_changes=[
        {"way_id": "555", "change": "added"},
    ])]
    _, blocks = build_drag_blocks(drags, "999", "bob")

    actions_block = [b for b in blocks if b["type"] == "actions"][0]
    button = actions_block["elements"][0]
    value = json.loads(button["value"])

    assert "555" in value["way_ids"]
    assert "111" in value["way_ids"]


def test_button_value_deduplicates_way_ids():
    """Way IDs are deduplicated across drags and membership changes."""
    drags = [
        _make_drag(way_id="111", way_membership_changes=[
            {"way_id": "555", "change": "added"},
        ]),
        _make_drag(way_id="222", way_membership_changes=[
            {"way_id": "555", "change": "added"},
        ]),
    ]
    _, blocks = build_drag_blocks(drags, "999", "bob")

    actions_block = [b for b in blocks if b["type"] == "actions"][0]
    button = actions_block["elements"][0]
    value = json.loads(button["value"])

    # 555 should appear only once
    assert value["way_ids"].count("555") == 1


def test_confirm_text_shows_counts():
    """Confirm dialog shows correct node and way counts."""
    drags = [
        _make_drag(node_id="42", way_id="111"),
        _make_drag(node_id="43", way_id="222"),
    ]
    _, blocks = build_drag_blocks(drags, "999", "bob")

    actions_block = [b for b in blocks if b["type"] == "actions"][0]
    button = actions_block["elements"][0]
    confirm_text = button["confirm"]["text"]["text"]

    assert "2 nodes" in confirm_text
    assert "2 ways" in confirm_text
