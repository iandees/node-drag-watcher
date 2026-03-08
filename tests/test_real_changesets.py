"""Tests using real augmented diff data from known changesets."""

import xml.etree.ElementTree as ET
from pathlib import Path

from checkers.drag import detect_node_drags

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(changeset_id):
    return ET.parse(FIXTURES / f"{changeset_id}.adiff").getroot()


def test_179281034_classic_drag():
    """Node 9047977114 dragged 128.8m on 2 ways by coleunderpar."""
    root = load_fixture(179281034)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 2
    for drag in drags:
        assert drag["node_id"] == "9047977114"
        assert drag["changeset"] == "179281034"
        assert drag["user"] == "coleunderpar"
        assert drag["distance_meters"] > 100


def test_179326123_node_substitution():
    """Node ref swapped on a 2-node way, 364.6m apart (drag onto another node)."""
    root = load_fixture(179326123)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 1
    drag = drags[0]
    assert drag["way_id"] == "1224710341"
    assert drag["node_id"] == "11009507471"
    assert drag["is_substitution"] is True
    assert drag["distance_meters"] > 300
    assert drag["changeset"] == "179326123"
    assert drag["user"] == "Ami1500"


def test_179263148_substitution_three_ways():
    """Node dragged onto intersection node, affecting 3 ways."""
    root = load_fixture(179263148)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 3
    way_ids = {d["way_id"] for d in drags}
    assert way_ids == {"334334048", "761519453", "825789929"}
    for drag in drags:
        assert drag["node_id"] == "2704770829"
        assert drag["is_substitution"] is True
        assert drag["distance_meters"] > 300
        assert drag["user"] == "ixnnnn777"


def test_179136483_two_drags_same_changeset():
    """Two separate nodes dragged in the same changeset."""
    root = load_fixture(179136483)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 4
    node_ids = {d["node_id"] for d in drags}
    assert "12992129426" in node_ids
    assert "11542910653" in node_ids
    for drag in drags:
        assert drag["user"] == "RafaelKiendler128"


def test_179077097_large_drag():
    """Node 6643262274 dragged 803m."""
    root = load_fixture(179077097)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 1
    drag = drags[0]
    assert drag["node_id"] == "6643262274"
    assert drag["distance_meters"] > 800
    assert drag["user"] == "JeremySoudan74"


def test_179128865_no_drags():
    """Changeset with no node drags (counter-example)."""
    root = load_fixture(179128865)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 0
