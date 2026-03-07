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

    # Should have a section and an actions block
    types = [b["type"] for b in blocks]
    assert "section" in types
    assert "actions" in types


def test_button_value_contains_required_fields():
    drags = [_make_drag()]
    _, blocks = build_drag_blocks(drags, "999", "bob")

    actions_block = [b for b in blocks if b["type"] == "actions"][0]
    button = actions_block["elements"][0]
    value = json.loads(button["value"])

    assert value["node_id"] == "42"
    assert value["old_lat"] == 51.0
    assert value["old_lon"] == -1.0
    assert value["new_lat"] == 51.1
    assert value["new_lon"] == -1.1
    assert value["changeset"] == "999"
    assert value["way_ids"] == ["111"]


def test_button_has_confirm_dialog():
    drags = [_make_drag()]
    _, blocks = build_drag_blocks(drags, "999", "bob")

    actions_block = [b for b in blocks if b["type"] == "actions"][0]
    button = actions_block["elements"][0]

    assert "confirm" in button
    assert button["style"] == "danger"
    assert button["action_id"] == "revert_node_drag"


def test_substitution_node_uses_new_ref():
    drags = [_make_drag(node_id="200", is_substitution=True)]
    _, blocks = build_drag_blocks(drags, "999", "bob")

    actions_block = [b for b in blocks if b["type"] == "actions"][0]
    button = actions_block["elements"][0]
    value = json.loads(button["value"])

    assert value["node_id"] == "200"
    assert "200" in button["text"]["text"]


def test_one_button_per_unique_node():
    drags = [
        _make_drag(way_id="111"),
        _make_drag(way_id="222"),  # same node_id, different way
    ]
    _, blocks = build_drag_blocks(drags, "999", "bob")

    actions_blocks = [b for b in blocks if b["type"] == "actions"]
    assert len(actions_blocks) == 1  # one button for one unique node


def test_multiple_nodes_get_multiple_buttons():
    drags = [
        _make_drag(node_id="42"),
        _make_drag(node_id="43", way_id="222"),
    ]
    _, blocks = build_drag_blocks(drags, "999", "bob")

    actions_blocks = [b for b in blocks if b["type"] == "actions"]
    assert len(actions_blocks) == 2


def test_button_value_includes_membership_changes():
    drags = [_make_drag(way_membership_changes=[
        {"way_id": "555", "change": "added"},
    ])]
    _, blocks = build_drag_blocks(drags, "999", "bob")

    actions_block = [b for b in blocks if b["type"] == "actions"][0]
    button = actions_block["elements"][0]
    value = json.loads(button["value"])

    assert "way_membership_changes" in value
    assert len(value["way_membership_changes"]) == 1
    assert value["way_membership_changes"][0]["way_id"] == "555"
    assert value["way_membership_changes"][0]["change"] == "added"


def test_button_value_deduplicates_membership_changes():
    """When multiple drags for same node have same membership change, deduplicate."""
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

    assert len(value["way_membership_changes"]) == 1
