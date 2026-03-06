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
    assert len(filtered) >= 1
    assert all(d["node_id"] == "9047977114" for d in filtered)


def test_keeps_drag_with_sharp_new_angle():
    """Interior node with new_angle < 45 should be kept."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": 175.0, "new_angle": 5.0, "way_angle_delta_sum": 280.0},
    ]
    assert len(filter_drags(drags)) == 1


def test_filters_smooth_new_angle():
    """Interior node with new_angle >= 45 should be filtered (not a spike)."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": 170.0, "new_angle": 165.0},
    ]
    assert len(filter_drags(drags)) == 0


def test_endpoint_kept_if_confirmed_by_interior():
    """Endpoint node kept if the same node has a sharp interior detection."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": 180.0, "new_angle": 3.0, "way_angle_delta_sum": 300.0},
        {"node_id": "1", "changeset": "100", "way_id": "20",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": None, "new_angle": None},
    ]
    assert len(filter_drags(drags)) == 2


def test_endpoint_filtered_if_not_confirmed():
    """Endpoint-only node should be filtered (no interior angle confirmation)."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": None, "new_angle": None},
    ]
    assert len(filter_drags(drags)) == 0


def test_endpoint_filtered_even_multi_way():
    """Multiple endpoint-only detections for same node still filtered."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": None, "new_angle": None},
        {"node_id": "1", "changeset": "100", "way_id": "20",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": None, "new_angle": None},
    ]
    assert len(filter_drags(drags)) == 0


def test_filters_sharp_angle_with_low_way_sum():
    """Sharp new_angle but low way_angle_delta_sum = building squaring, not a drag."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 20, "user": "u", "way_name": "",
         "old_angle": 2.3, "new_angle": 4.0, "way_angle_delta_sum": 11.6},
    ]
    assert len(filter_drags(drags)) == 0


def test_keeps_sharp_angle_with_high_way_sum():
    """Sharp new_angle with high way_angle_delta_sum = real drag."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": 180.0, "new_angle": 5.0, "way_angle_delta_sum": 280.0},
    ]
    assert len(filter_drags(drags)) == 1


def test_keeps_substitution_with_no_way_sum():
    """Substitution drag with no way_angle_delta_sum = kept."""
    drags = [
        {"node_id": "100->200", "changeset": "100", "way_id": "10",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": 180.0, "new_angle": 5.0, "way_angle_delta_sum": None},
    ]
    assert len(filter_drags(drags)) == 1


def test_filters_non_substitution_with_no_way_sum():
    """Non-substitution drag with no way_sum (nodes added/removed) = filtered."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 50, "user": "u", "way_name": "",
         "old_angle": 51.0, "new_angle": 39.8, "way_angle_delta_sum": None},
    ]
    assert len(filter_drags(drags)) == 0


def test_endpoint_not_confirmed_by_low_way_sum():
    """Endpoint node not confirmed if interior detection has low way_angle_delta_sum."""
    drags = [
        {"node_id": "1", "changeset": "100", "way_id": "10",
         "distance_meters": 20, "user": "u", "way_name": "",
         "old_angle": 88.0, "new_angle": 37.0, "way_angle_delta_sum": 100.0},
        {"node_id": "1", "changeset": "100", "way_id": "20",
         "distance_meters": 20, "user": "u", "way_name": "",
         "old_angle": None, "new_angle": None},
    ]
    assert len(filter_drags(drags)) == 0
