"""Tests for filtering out intentional edits."""

import xml.etree.ElementTree as ET
from pathlib import Path

from watcher import detect_node_drags, filter_drags

FIXTURES = Path(__file__).parent / "fixtures"


def test_filters_road_realignment_by_angle():
    """Changeset 179414342: road realignment has smooth angles, should be filtered."""
    root = ET.parse(FIXTURES / "179414342.adiff").getroot()
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) > 1

    filtered = filter_drags(drags)
    assert len(filtered) == 0


def test_keeps_single_node_drag_sharp_angle():
    """Changeset 179281034: classic drag creates sharp spike, should be kept."""
    root = ET.parse(FIXTURES / "179281034.adiff").getroot()
    drags = detect_node_drags(root, threshold_meters=10)
    filtered = filter_drags(drags)
    # At least the interior node detection should be kept (sharp angle)
    assert len(filtered) >= 1
    assert all(d["node_id"] == "9047977114" for d in filtered)


def test_keeps_drag_with_sharp_new_angle():
    """Interior node with new_angle < 45° should be kept."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": 175.0, "new_angle": 5.0},
    ]
    assert len(filter_drags(drags)) == 1


def test_filters_smooth_new_angle():
    """Interior node with new_angle >= 45° should be filtered (not a spike)."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": 170.0, "new_angle": 165.0},
    ]
    assert len(filter_drags(drags)) == 0


def test_endpoint_kept_if_single_node_in_changeset():
    """Endpoint node (no angle) should be kept if it's the only node in the changeset."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": None, "new_angle": None},
    ]
    assert len(filter_drags(drags)) == 1


def test_endpoint_filtered_if_multi_node_changeset():
    """Endpoint nodes filtered if changeset has multiple distinct dragged nodes."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": None, "new_angle": None},
        {"node_id": "2", "changeset": "100", "way_id": "20",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": None, "new_angle": None},
    ]
    assert len(filter_drags(drags)) == 0


def test_mixed_angle_and_endpoint():
    """Sharp interior node kept, endpoint in same changeset also kept (same node)."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": 180.0, "new_angle": 3.0},
        {"node_id": "1", "changeset": "100", "way_id": "20",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": None, "new_angle": None},
    ]
    # Both kept: interior has sharp angle, endpoint is same node (1 distinct)
    assert len(filter_drags(drags)) == 2
