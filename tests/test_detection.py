import xml.etree.ElementTree as ET
import math
import pytest

from watcher import detect_node_drags, haversine_distance

# Minimal adiff XML: one way where exactly 1 of 4 nodes moved significantly
# The node action has different changeset/user than the way (realistic scenario)
SINGLE_DRAG_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <node id="2" version="1" lat="40.0010" lon="-74.0010"/>
    </old>
    <new>
      <node id="2" version="2" timestamp="2025-01-01T00:00:00Z" uid="200" user="dragger" changeset="88888" lat="40.0015" lon="-74.0010"/>
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
      <node id="2" version="1" lat="40.0010" lon="-74.0010"/>
    </old>
    <new>
      <node id="2" version="2" timestamp="2025-01-01T00:00:00Z" uid="100" user="testuser" changeset="99999" lat="40.0015" lon="-74.0010"/>
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
    assert drag["changeset"] == "88888"
    assert drag["user"] == "dragger"
    assert drag["way_name"] == "Test Street"
    # Geometry keys
    assert len(drag["old_way_coords"]) == 4
    assert len(drag["new_way_coords"]) == 4
    assert drag["dragged_node_old"] == (40.0010, -74.0010)
    assert drag["dragged_node_new"] == (40.0015, -74.0010)


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


# Node substitution: user drags node onto another node, editor merges them,
# so the way's node list has a ref swapped out for a different ref far away
NODE_SUBSTITUTION_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <way id="99999" version="1" user="olduser" uid="1" timestamp="2024-01-01T00:00:00Z" changeset="11111">
        <nd ref="100" lat="10.3036" lon="-85.8023"/>
        <nd ref="200" lat="10.3041" lon="-85.8027"/>
        <tag k="highway" v="service"/>
      </way>
    </old>
    <new>
      <way id="99999" version="2" timestamp="2025-01-01T00:00:00Z" uid="500" user="draguser" changeset="55555">
        <nd ref="300" lat="10.3005" lon="-85.8012"/>
        <nd ref="200" lat="10.3041" lon="-85.8027"/>
        <tag k="highway" v="service"/>
      </way>
    </new>
  </action>
</osm>"""


def test_detects_node_substitution_drag():
    """Detect when a node ref is replaced by a different ref far away."""
    root = ET.fromstring(NODE_SUBSTITUTION_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 1
    drag = drags[0]
    assert drag["way_id"] == "99999"
    assert drag["node_id"] == "300"
    assert drag["is_substitution"] is True
    assert drag["distance_meters"] > 300
    assert drag["changeset"] == "55555"
    assert drag["user"] == "draguser"
    # Geometry keys for substitution
    assert drag["dragged_node_old"] == (10.3036, -85.8023)
    assert drag["dragged_node_new"] == (10.3005, -85.8012)


# Node substitution but distance is small (not a drag)
NODE_SUBSTITUTION_SMALL_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <way id="99999" version="1" user="olduser" uid="1" timestamp="2024-01-01T00:00:00Z" changeset="11111">
        <nd ref="100" lat="10.30000" lon="-85.80000"/>
        <nd ref="200" lat="10.30410" lon="-85.80270"/>
      </way>
    </old>
    <new>
      <way id="99999" version="2" timestamp="2025-01-01T00:00:00Z" uid="500" user="draguser" changeset="55555">
        <nd ref="300" lat="10.30001" lon="-85.80001"/>
        <nd ref="200" lat="10.30410" lon="-85.80270"/>
      </way>
    </new>
  </action>
</osm>"""


def test_ignores_small_node_substitution():
    """Don't flag node substitutions where the replacement is nearby."""
    root = ET.fromstring(NODE_SUBSTITUTION_SMALL_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 0


# Adiff where a node is moved on one way AND added to another way
DRAG_WITH_MEMBERSHIP_CHANGE_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <node id="2" version="1" lat="40.0010" lon="-74.0010"/>
    </old>
    <new>
      <node id="2" version="2" timestamp="2025-01-01T00:00:00Z" uid="200" user="dragger" changeset="88888" lat="40.0015" lon="-74.0010"/>
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
      <way id="12345" version="6" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.0015" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
        <tag k="highway" v="residential"/>
      </way>
    </new>
  </action>
  <action type="modify">
    <old>
      <way id="67890" version="2" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="10" lat="40.1000" lon="-74.1000"/>
        <nd ref="11" lat="40.1010" lon="-74.1010"/>
      </way>
    </old>
    <new>
      <way id="67890" version="3" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="10" lat="40.1000" lon="-74.1000"/>
        <nd ref="2" lat="40.0015" lon="-74.0010"/>
        <nd ref="11" lat="40.1010" lon="-74.1010"/>
      </way>
    </new>
  </action>
</osm>"""


def test_detects_way_membership_changes():
    """Detect when a dragged node is also added to another way."""
    root = ET.fromstring(DRAG_WITH_MEMBERSHIP_CHANGE_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 1
    drag = drags[0]
    assert drag["node_id"] == "2"
    assert drag["way_id"] == "12345"

    # Should have detected node 2 was added to way 67890
    changes = drag["way_membership_changes"]
    assert len(changes) == 1
    assert changes[0]["way_id"] == "67890"
    assert changes[0]["change"] == "added"


def test_no_membership_changes_for_own_way():
    """Membership changes on the drag's own way are excluded."""
    root = ET.fromstring(SINGLE_DRAG_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 1
    assert drags[0]["way_membership_changes"] == []
