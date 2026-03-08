"""Tests for adiff Action parser."""

import xml.etree.ElementTree as ET

from checkers import Action
from watcher import parse_adiff_actions


MODIFY_NODE_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <node id="42" version="1" lat="51.0" lon="-1.0" user="u1" changeset="100">
        <tag k="phone" v="123"/>
      </node>
    </old>
    <new>
      <node id="42" version="2" lat="51.1" lon="-1.1" user="u2" changeset="200">
        <tag k="phone" v="456"/>
      </node>
    </new>
  </action>
</osm>"""


CREATE_NODE_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="create">
    <new>
      <node id="99" version="1" lat="40.0" lon="-74.0" user="bob" changeset="300">
        <tag k="website" v="example.com"/>
      </node>
    </new>
  </action>
</osm>"""


MODIFY_WAY_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <way id="111" version="1" user="u1" changeset="100">
        <nd ref="1" lat="51.0" lon="-1.0"/>
        <nd ref="2" lat="51.1" lon="-1.1"/>
        <tag k="name" v="Main St"/>
      </way>
    </old>
    <new>
      <way id="111" version="2" user="u2" changeset="200">
        <nd ref="1" lat="51.0" lon="-1.0"/>
        <nd ref="2" lat="51.2" lon="-1.2"/>
        <tag k="name" v="Main St"/>
      </way>
    </new>
  </action>
</osm>"""


class TestParseAdiffActions:
    def test_modify_node(self):
        root = ET.fromstring(MODIFY_NODE_ADIFF)
        actions = list(parse_adiff_actions(root))
        assert len(actions) == 1
        a = actions[0]
        assert a.action_type == "modify"
        assert a.element_type == "node"
        assert a.element_id == "42"
        assert a.version == "2"
        assert a.changeset == "200"
        assert a.user == "u2"
        assert a.tags_old == {"phone": "123"}
        assert a.tags_new == {"phone": "456"}
        assert a.coords_old == (51.0, -1.0)
        assert a.coords_new == (51.1, -1.1)

    def test_create_node(self):
        root = ET.fromstring(CREATE_NODE_ADIFF)
        actions = list(parse_adiff_actions(root))
        assert len(actions) == 1
        a = actions[0]
        assert a.action_type == "create"
        assert a.element_type == "node"
        assert a.tags_new == {"website": "example.com"}
        assert a.tags_old == {}
        assert a.coords_old is None
        assert a.coords_new == (40.0, -74.0)

    def test_modify_way(self):
        root = ET.fromstring(MODIFY_WAY_ADIFF)
        actions = list(parse_adiff_actions(root))
        assert len(actions) == 1
        a = actions[0]
        assert a.action_type == "modify"
        assert a.element_type == "way"
        assert a.element_id == "111"
        assert a.nd_refs_old == ["1", "2"]
        assert a.nd_refs_new == ["1", "2"]
        assert a.node_coords_old == {"1": (51.0, -1.0), "2": (51.1, -1.1)}
        assert a.node_coords_new == {"1": (51.0, -1.0), "2": (51.2, -1.2)}
        assert a.tags_old == {"name": "Main St"}
        assert a.tags_new == {"name": "Main St"}
