"""Tests for filtering out intentional edits."""

import xml.etree.ElementTree as ET
from pathlib import Path

from watcher import detect_node_drags, filter_drags

FIXTURES = Path(__file__).parent / "fixtures"


def test_filters_multi_node_changeset():
    """Changeset 179414342: road realignment with many nodes moved = not a drag."""
    root = ET.parse(FIXTURES / "179414342.adiff").getroot()
    drags = detect_node_drags(root, threshold_meters=10)
    # Raw detection finds multiple drags
    assert len(drags) > 1
    distinct_nodes = {d["node_id"] for d in drags}
    assert len(distinct_nodes) > 1

    # Filter suppresses them all
    filtered = filter_drags(drags)
    assert len(filtered) == 0


def test_keeps_single_node_drag():
    """Changeset 179281034: classic single node drag should be kept."""
    root = ET.parse(FIXTURES / "179281034.adiff").getroot()
    drags = detect_node_drags(root, threshold_meters=10)
    filtered = filter_drags(drags)
    assert len(filtered) == len(drags)
    assert all(d["node_id"] == "9047977114" for d in filtered)


def test_filter_preserves_multi_way_single_node():
    """One node dragged on multiple ways (same node) should be kept."""
    drags = [
        {"node_id": "123", "changeset": "999", "way_id": "1", "distance_meters": 50, "user": "u", "way_name": ""},
        {"node_id": "123", "changeset": "999", "way_id": "2", "distance_meters": 50, "user": "u", "way_name": ""},
        {"node_id": "123", "changeset": "999", "way_id": "3", "distance_meters": 50, "user": "u", "way_name": ""},
    ]
    filtered = filter_drags(drags)
    assert len(filtered) == 3


def test_filter_removes_multi_node_changeset():
    """Multiple distinct nodes in one changeset = intentional, remove all."""
    drags = [
        {"node_id": "100", "changeset": "999", "way_id": "1", "distance_meters": 50, "user": "u", "way_name": ""},
        {"node_id": "200", "changeset": "999", "way_id": "2", "distance_meters": 50, "user": "u", "way_name": ""},
    ]
    filtered = filter_drags(drags)
    assert len(filtered) == 0
