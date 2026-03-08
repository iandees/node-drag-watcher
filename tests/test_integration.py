"""Integration tests against the OSM dev API.

Requires OSM_DEV_TOKEN env var. Run with:
    OSM_DEV_TOKEN=<token> uv run pytest tests/test_integration.py -v -m integration
"""

import os
import xml.etree.ElementTree as ET

import pytest
import requests

from revert import (
    create_changeset,
    close_changeset,
    fetch_node,
    fetch_way,
    update_node,
    revert_changeset,
)

DEV_API_BASE = "https://master.apis.dev.openstreetmap.org/api/0.6"

TOKEN = os.environ.get("OSM_DEV_TOKEN", "")

# Positions for testing
POS_A = (51.5, -0.1)  # "original" position
POS_B = (51.6, -0.2)  # "dragged" position


def _osm_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/xml",
    }


def _create_node(token: str, cs_id: str, lat: float, lon: float) -> str:
    """Create a node on the dev API, return its ID."""
    node_xml = (
        f'<osm><node changeset="{cs_id}" lat="{lat}" lon="{lon}"/></osm>'
    )
    resp = requests.put(
        f"{DEV_API_BASE}/node/create",
        data=node_xml,
        headers=_osm_headers(token),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.text.strip()


def _create_way(token: str, cs_id: str, node_ids: list[str]) -> str:
    """Create a way on the dev API, return its ID."""
    nds = "".join(f'<nd ref="{nid}"/>' for nid in node_ids)
    way_xml = f'<osm><way changeset="{cs_id}">{nds}</way></osm>'
    resp = requests.put(
        f"{DEV_API_BASE}/way/create",
        data=way_xml,
        headers=_osm_headers(token),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.text.strip()


def _delete_way(token: str, cs_id: str, way_id: str, version: str) -> None:
    """Delete a way on the dev API."""
    way_xml = (
        f'<osm><way id="{way_id}" version="{version}" changeset="{cs_id}"/></osm>'
    )
    resp = requests.delete(
        f"{DEV_API_BASE}/way/{way_id}",
        data=way_xml,
        headers=_osm_headers(token),
        timeout=15,
    )
    resp.raise_for_status()


def _update_way_refs(token: str, cs_id: str, way_id: str, version: str,
                     node_ids: list[str]) -> None:
    """Update a way's node list on the dev API."""
    nds = "".join(f'<nd ref="{nid}"/>' for nid in node_ids)
    way_xml = (
        f'<osm><way id="{way_id}" version="{version}" changeset="{cs_id}">'
        f'{nds}</way></osm>'
    )
    resp = requests.put(
        f"{DEV_API_BASE}/way/{way_id}",
        data=way_xml,
        headers=_osm_headers(token),
        timeout=15,
    )
    resp.raise_for_status()


def _delete_node(token: str, cs_id: str, node_id: str, version: str) -> None:
    """Delete a node on the dev API."""
    node_xml = (
        f'<osm><node id="{node_id}" version="{version}" changeset="{cs_id}" '
        f'lat="0" lon="0" visible="false"/></osm>'
    )
    resp = requests.delete(
        f"{DEV_API_BASE}/node/{node_id}",
        data=node_xml,
        headers=_osm_headers(token),
        timeout=15,
    )
    resp.raise_for_status()


def _way_node_refs(way_elem) -> list[str]:
    """Extract nd refs from a way element."""
    return [nd.get("ref") for nd in way_elem.findall("nd")]


@pytest.mark.integration
class TestRevertIntegration:
    """End-to-end revert test against the OSM dev API."""

    @pytest.fixture(autouse=True)
    def _skip_without_token(self):
        if not TOKEN:
            pytest.skip("OSM_DEV_TOKEN not set")

    def test_create_move_revert_node(self):
        # 1. Create a node at position A
        cs1 = create_changeset(TOKEN, "Integration test: create node", api_base=DEV_API_BASE)
        node_id = _create_node(TOKEN, cs1, POS_A[0], POS_A[1])
        close_changeset(TOKEN, cs1, api_base=DEV_API_BASE)

        # 2. Move the node to position B (simulating a drag)
        cs2 = create_changeset(TOKEN, "Integration test: move node", api_base=DEV_API_BASE)
        node_elem, visible = fetch_node(node_id, api_base=DEV_API_BASE)
        assert visible
        update_node(TOKEN, cs2, node_elem, POS_B[0], POS_B[1], api_base=DEV_API_BASE)
        close_changeset(TOKEN, cs2, api_base=DEV_API_BASE)

        # 3. Revert the drag
        result = revert_changeset(
            TOKEN, cs2, "Integration test: revert drag",
            node_ids=[node_id], way_ids=[],
            api_base=DEV_API_BASE,
        )
        assert result.revert_changeset_id is not None
        assert node_id in result.nodes_moved

        # 4. Verify the node is back at position A
        node_elem, visible = fetch_node(node_id, api_base=DEV_API_BASE)
        assert visible
        lat = float(node_elem.get("lat"))
        lon = float(node_elem.get("lon"))
        assert abs(lat - POS_A[0]) < 1e-6
        assert abs(lon - POS_A[1]) < 1e-6

        # 5. Clean up: delete the test node
        cs_cleanup = create_changeset(TOKEN, "Integration test: cleanup", api_base=DEV_API_BASE)
        node_elem, _ = fetch_node(node_id, api_base=DEV_API_BASE)
        _delete_node(TOKEN, cs_cleanup, node_id, node_elem.get("version"))
        close_changeset(TOKEN, cs_cleanup, api_base=DEV_API_BASE)

    def test_way_node_substitution_revert(self):
        """Drag a node onto another way's node (substitution), then revert.

        Setup:
          way_a: [a1, a2, a3]  — a2 is the node that will be "dragged"
          way_b: [b1, b2, b3]  — b2 is the node it merges into

        Simulate drag: editor replaces a2 with b2 in way_a, deletes a2.
          way_a becomes: [a1, b2, a3]
          node a2 is deleted

        Revert should: undelete a2, swap b2 back to a2 in way_a.
          way_a restored to: [a1, a2, a3]
          node a2 is visible again at its original position
        """
        # -- Setup: create nodes and ways --
        cs_setup = create_changeset(TOKEN, "Integration test: setup substitution",
                                    api_base=DEV_API_BASE)
        # Way A nodes: a line at lat 51.5
        a1 = _create_node(TOKEN, cs_setup, 51.5, -0.13)
        a2 = _create_node(TOKEN, cs_setup, 51.5, -0.12)  # will be "dragged"
        a3 = _create_node(TOKEN, cs_setup, 51.5, -0.11)

        # Way B nodes: a line at lat 51.501
        b1 = _create_node(TOKEN, cs_setup, 51.501, -0.13)
        b2 = _create_node(TOKEN, cs_setup, 51.501, -0.12)  # target of merge
        b3 = _create_node(TOKEN, cs_setup, 51.501, -0.11)

        way_a = _create_way(TOKEN, cs_setup, [a1, a2, a3])
        way_b = _create_way(TOKEN, cs_setup, [b1, b2, b3])
        close_changeset(TOKEN, cs_setup, api_base=DEV_API_BASE)

        # Record a2's original position
        a2_elem, _ = fetch_node(a2, api_base=DEV_API_BASE)
        a2_lat = float(a2_elem.get("lat"))
        a2_lon = float(a2_elem.get("lon"))

        # -- Simulate substitution drag --
        # Replace a2 with b2 in way_a, then delete a2
        cs_drag = create_changeset(TOKEN, "Integration test: simulate substitution drag",
                                   api_base=DEV_API_BASE)
        way_a_elem = fetch_way(way_a, api_base=DEV_API_BASE)
        _update_way_refs(TOKEN, cs_drag, way_a, way_a_elem.get("version"), [a1, b2, a3])
        a2_elem, _ = fetch_node(a2, api_base=DEV_API_BASE)
        _delete_node(TOKEN, cs_drag, a2, a2_elem.get("version"))
        close_changeset(TOKEN, cs_drag, api_base=DEV_API_BASE)

        # Verify: way_a now references b2, a2 is deleted
        way_a_elem = fetch_way(way_a, api_base=DEV_API_BASE)
        assert _way_node_refs(way_a_elem) == [a1, b2, a3]
        _, a2_visible = fetch_node(a2, api_base=DEV_API_BASE)
        assert not a2_visible

        # -- Revert the substitution --
        # Now we just pass node_ids and way_ids; revert_changeset figures out
        # what to do from the changeset download
        result = revert_changeset(
            TOKEN, cs_drag, "Integration test: revert substitution",
            node_ids=[], way_ids=[way_a],
            api_base=DEV_API_BASE,
        )
        # a2 should be auto-discovered as deleted and undeleted
        assert a2 in result.nodes_undeleted
        assert way_a in result.ways_updated

        # -- Verify revert --
        # a2 is visible again at original position
        a2_elem, a2_visible = fetch_node(a2, api_base=DEV_API_BASE)
        assert a2_visible
        assert abs(float(a2_elem.get("lat")) - a2_lat) < 1e-6
        assert abs(float(a2_elem.get("lon")) - a2_lon) < 1e-6

        # way_a has a2 back (not b2)
        way_a_elem = fetch_way(way_a, api_base=DEV_API_BASE)
        assert _way_node_refs(way_a_elem) == [a1, a2, a3]

        # way_b is unchanged
        way_b_elem = fetch_way(way_b, api_base=DEV_API_BASE)
        assert _way_node_refs(way_b_elem) == [b1, b2, b3]

        # -- Clean up --
        cs_cleanup = create_changeset(TOKEN, "Integration test: cleanup substitution",
                                      api_base=DEV_API_BASE)
        # Delete ways first (can't delete nodes referenced by ways)
        way_a_elem = fetch_way(way_a, api_base=DEV_API_BASE)
        _delete_way(TOKEN, cs_cleanup, way_a, way_a_elem.get("version"))
        way_b_elem = fetch_way(way_b, api_base=DEV_API_BASE)
        _delete_way(TOKEN, cs_cleanup, way_b, way_b_elem.get("version"))
        # Delete all nodes
        for nid in [a1, a2, a3, b1, b2, b3]:
            elem, visible = fetch_node(nid, api_base=DEV_API_BASE)
            if visible:
                _delete_node(TOKEN, cs_cleanup, nid, elem.get("version"))
        close_changeset(TOKEN, cs_cleanup, api_base=DEV_API_BASE)
