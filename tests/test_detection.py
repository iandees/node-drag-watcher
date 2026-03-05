import xml.etree.ElementTree as ET
import math
import pytest

from watcher import detect_node_drags, haversine_distance

# Minimal adiff XML: one way where exactly 1 of 4 nodes moved significantly
SINGLE_DRAG_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.0010" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
        <tag k="highway" v="residential"/>
        <tag k="name" v="Test Street"/>
      </way>
    </old>
    <new>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.0015" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
        <tag k="highway" v="residential"/>
        <tag k="name" v="Test Street"/>
      </way>
    </new>
  </action>
</osm>"""

# Adiff where multiple nodes moved (not a drag)
MULTI_NODE_MOVE_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.0010" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
      </way>
    </old>
    <new>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0005" lon="-74.0005"/>
        <nd ref="2" lat="40.0015" lon="-74.0015"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
      </way>
    </new>
  </action>
</osm>"""

# Adiff where 1 node moved but less than threshold
SMALL_MOVE_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.00001" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
      </way>
    </old>
    <new>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.00002" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
      </way>
    </new>
  </action>
</osm>"""

# Adiff with no ways (only node modifications)
NO_WAYS_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <node id="999" version="1" lat="40.0" lon="-74.0"/>
    </old>
    <new>
      <node id="999" version="2" lat="40.1" lon="-74.1" timestamp="2025-01-01T00:00:00Z" uid="100" user="testuser" changeset="99999"/>
    </new>
  </action>
</osm>"""

# Adiff with a drag alongside other edits in the same changeset
DRAG_WITH_OTHER_EDITS_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="create">
    <new>
      <node id="888" version="1" lat="41.0" lon="-75.0" timestamp="2025-01-01T00:00:00Z" uid="100" user="testuser" changeset="99999"/>
    </new>
  </action>
  <action type="modify">
    <old>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.0010" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
        <tag k="highway" v="residential"/>
      </way>
    </old>
    <new>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.0015" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
        <tag k="highway" v="residential"/>
      </way>
    </new>
  </action>
</osm>"""


def test_haversine_distance():
    # Known distance: ~111km per degree of latitude at equator
    d = haversine_distance(0.0, 0.0, 1.0, 0.0)
    assert 110_000 < d < 112_000

    # Same point = 0
    d = haversine_distance(40.0, -74.0, 40.0, -74.0)
    assert d == 0.0

    # Small distance (~55m)
    d = haversine_distance(40.0000, -74.0000, 40.0005, -74.0000)
    assert 50 < d < 60


def test_detects_single_node_drag():
    root = ET.fromstring(SINGLE_DRAG_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 1
    drag = drags[0]
    assert drag["way_id"] == "12345"
    assert drag["node_id"] == "2"
    assert drag["distance_meters"] > 10
    assert drag["changeset"] == "99999"
    assert drag["user"] == "testuser"
    assert drag["way_name"] == "Test Street"


def test_ignores_multi_node_move():
    root = ET.fromstring(MULTI_NODE_MOVE_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 0


def test_ignores_small_move():
    root = ET.fromstring(SMALL_MOVE_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 0


def test_no_ways_returns_empty():
    root = ET.fromstring(NO_WAYS_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 0


def test_drag_detected_alongside_other_edits():
    root = ET.fromstring(DRAG_WITH_OTHER_EDITS_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 1
    assert drags[0]["node_id"] == "2"
