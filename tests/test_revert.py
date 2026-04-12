"""Tests for the revert module — all HTTP requests are mocked."""

import xml.etree.ElementTree as ET
from unittest.mock import patch, MagicMock

import pytest

from revert import (
    RevertResult,
    RevertError, AlreadyRevertedError, ConflictError, AuthError,
    revert_changeset, fetch_changeset_download, fetch_element_version,
    _find_in_osmchange, _discover_deleted_nodes, _nd_refs,
)


# -- Helpers -------------------------------------------------------------------

def _ok(text="", status=200):
    r = MagicMock(status_code=status, ok=True, text=text)
    r.raise_for_status = MagicMock()
    return r


def _err(status):
    r = MagicMock(status_code=status, ok=False)
    r.raise_for_status = MagicMock(side_effect=Exception(f"HTTP {status}"))
    return r


def _node_xml(node_id, version, lat, lon, visible=True, tags=None):
    tags_str = ""
    if tags:
        for k, v in tags.items():
            tags_str += f'<tag k="{k}" v="{v}"/>'
    vis = ' visible="true"' if visible else ' visible="false"'
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<osm><node id="{node_id}" version="{version}" lat="{lat}" lon="{lon}"{vis}>'
        f'{tags_str}</node></osm>'
    )


def _way_xml(way_id, version, nd_refs, tags=None):
    nds = "".join(f'<nd ref="{r}"/>' for r in nd_refs)
    tags_str = ""
    if tags:
        for k, v in tags.items():
            tags_str += f'<tag k="{k}" v="{v}"/>'
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<osm><way id="{way_id}" version="{version}">'
        f'{nds}{tags_str}</way></osm>'
    )


def _osmchange_xml(actions):
    """Build osmChange XML from a list of (action_type, element_xml) tuples.

    element_xml should be just the element, e.g. '<node id="42" version="5" .../>'.
    """
    parts = ['<?xml version="1.0" encoding="UTF-8"?><osmChange>']
    for action_type, elem_xml in actions:
        parts.append(f'<{action_type}>{elem_xml}</{action_type}>')
    parts.append('</osmChange>')
    return "".join(parts)


# -- Mock router ---------------------------------------------------------------

def _make_router(responses):
    """Create a side_effect function that routes requests.get by URL substring.

    responses is a dict of {url_substring: response_or_list}.
    If the value is a list, responses are consumed in order.
    """
    call_counts = {}

    def router(url, **kwargs):
        for pattern, resp in responses.items():
            if pattern in url:
                if isinstance(resp, list):
                    idx = call_counts.get(pattern, 0)
                    call_counts[pattern] = idx + 1
                    return resp[idx]
                return resp
        raise ValueError(f"No mock for GET {url}")
    return router


def _make_put_router(responses):
    """Create a side_effect function for requests.put that routes by URL substring."""
    call_counts = {}

    def router(url, **kwargs):
        for pattern, resp in responses.items():
            if pattern in url:
                if isinstance(resp, list):
                    idx = call_counts.get(pattern, 0)
                    call_counts[pattern] = idx + 1
                    return resp[idx]
                return resp
        raise ValueError(f"No mock for PUT {url}")
    return router


# ==============================================================================
# _find_in_osmchange tests
# ==============================================================================

class TestFindInOsmChange:
    def test_finds_modified_node(self):
        xml = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
        ])
        root = ET.fromstring(xml)
        result = _find_in_osmchange(root, "node", "42")
        assert result is not None
        assert result[0] == "modify"
        assert result[1].get("id") == "42"

    def test_finds_deleted_node(self):
        xml = _osmchange_xml([
            ("delete", '<node id="100" version="5" lat="51.0" lon="-1.0"/>'),
        ])
        root = ET.fromstring(xml)
        result = _find_in_osmchange(root, "node", "100")
        assert result is not None
        assert result[0] == "delete"

    def test_not_found(self):
        xml = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
        ])
        root = ET.fromstring(xml)
        assert _find_in_osmchange(root, "node", "999") is None


# ==============================================================================
# _discover_deleted_nodes tests
# ==============================================================================

class TestDiscoverDeletedNodes:
    def test_discovers_deleted_node(self):
        xml = _osmchange_xml([
            ("delete", '<node id="100" version="5" lat="51.0" lon="-1.0"/>'),
        ])
        root = ET.fromstring(xml)
        result = _discover_deleted_nodes(root, {"100", "1", "3"}, {"200", "1", "3"})
        assert result == ["100"]

    def test_ignores_non_deleted(self):
        xml = _osmchange_xml([
            ("modify", '<node id="100" version="5" lat="51.0" lon="-1.0"/>'),
        ])
        root = ET.fromstring(xml)
        result = _discover_deleted_nodes(root, {"100", "1"}, {"1"})
        assert result == []


# ==============================================================================
# Node move revert tests
# ==============================================================================

class TestNodeMoveRevert:
    def test_happy_path(self):
        """Node modified in changeset, still at changeset version → moved back."""
        osmchange = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
        ])
        node_v4 = _node_xml("42", "4", "51.0", "-1.0")
        current_node = _node_xml("42", "5", "51.1", "-1.1")

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "node/42/4": _ok(node_v4),
                "node/42": _ok(current_node),
            }))
            mock_req.put = MagicMock(side_effect=_make_put_router({
                "changeset/create": _ok("12345"),
                "node/42": _ok("6"),
                "changeset/12345/close": _ok(),
            }))
            mock_req.post = MagicMock()

            result = revert_changeset(
                "token", "999", "Revert drag",
                node_ids=["42"], way_ids=[],
            )

        assert result.revert_changeset_id == "12345"
        assert result.nodes_moved == ["42"]
        assert result.skipped == []

    def test_version_conflict_rebases(self):
        """Node version changed since changeset → revert rebases onto current version."""
        osmchange = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
        ])
        node_v4 = _node_xml("42", "4", "51.0", "-1.0")
        current_node = _node_xml("42", "6", "51.2", "-1.2")  # version 6, not 5

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "node/42/4": _ok(node_v4),
                "node/42": _ok(current_node),
            }))
            mock_req.put = MagicMock(side_effect=_make_put_router({
                "changeset/create": _ok("12345"),
                "node/42": _ok("7"),
                "changeset/12345/close": _ok(),
            }))

            result = revert_changeset(
                "token", "999", "Revert",
                node_ids=["42"], way_ids=[],
            )

        assert result.nodes_moved == ["42"]
        # Verify the PUT used version 6 (current), not version 5 (changeset)
        node_put_calls = [
            c for c in mock_req.put.call_args_list
            if "node/42" in c[0][0] and "changeset" not in c[0][0]
        ]
        assert len(node_put_calls) == 1
        data = node_put_calls[0][1]["data"]
        assert 'version="6"' in data
        assert 'lat="51.0"' in data
        assert 'lon="-1.0"' in data

    def test_preserves_tags(self):
        """Node update should include original tags from before version."""
        osmchange = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
        ])
        node_v4 = _node_xml("42", "4", "51.0", "-1.0", tags={"name": "Test", "highway": "crossing"})
        current_node = _node_xml("42", "5", "51.1", "-1.1")

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "node/42/4": _ok(node_v4),
                "node/42": _ok(current_node),
            }))
            mock_req.put = MagicMock(side_effect=_make_put_router({
                "changeset/create": _ok("12345"),
                "node/42": _ok("6"),
                "changeset/12345/close": _ok(),
            }))

            revert_changeset(
                "token", "999", "Revert",
                node_ids=["42"], way_ids=[],
            )

        # Find the node update PUT call
        node_put_calls = [
            c for c in mock_req.put.call_args_list
            if "node/42" in c[0][0] and "changeset" not in c[0][0]
        ]
        assert len(node_put_calls) == 1
        data = node_put_calls[0][1]["data"]
        assert 'lat="51.0"' in data
        assert 'lon="-1.0"' in data

    def test_two_nodes_moved(self):
        """Two nodes modified in one changeset."""
        osmchange = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
            ("modify", '<node id="43" version="3" lat="52.1" lon="-2.1"/>'),
        ])
        node42_v4 = _node_xml("42", "4", "51.0", "-1.0")
        node43_v2 = _node_xml("43", "2", "52.0", "-2.0")
        current42 = _node_xml("42", "5", "51.1", "-1.1")
        current43 = _node_xml("43", "3", "52.1", "-2.1")

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "node/42/4": _ok(node42_v4),
                "node/43/2": _ok(node43_v2),
                "node/42": _ok(current42),
                "node/43": _ok(current43),
            }))
            mock_req.put = MagicMock(side_effect=_make_put_router({
                "changeset/create": _ok("12345"),
                "node/42": _ok("6"),
                "node/43": _ok("4"),
                "changeset/12345/close": _ok(),
            }))

            result = revert_changeset(
                "token", "999", "Revert",
                node_ids=["42", "43"], way_ids=[],
            )

        assert result.nodes_moved == ["42", "43"]


# ==============================================================================
# Way revert tests
# ==============================================================================

class TestWayRevert:
    def test_way_nd_list_restored(self):
        """Way modified in changeset → nd list restored to before state."""
        osmchange = _osmchange_xml([
            ("modify", '<way id="111" version="3"><nd ref="1"/><nd ref="200"/><nd ref="3"/></way>'),
        ])
        way_v2 = _way_xml("111", "2", ["1", "100", "3"])
        current_way = _way_xml("111", "3", ["1", "200", "3"])

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "way/111/2": _ok(way_v2),
                "way/111": _ok(current_way),
            }))
            mock_req.put = MagicMock(side_effect=_make_put_router({
                "changeset/create": _ok("12345"),
                "way/111": _ok("4"),
                "changeset/12345/close": _ok(),
            }))

            result = revert_changeset(
                "token", "999", "Revert",
                node_ids=[], way_ids=["111"],
            )

        assert result.ways_updated == ["111"]
        way_put_calls = [
            c for c in mock_req.put.call_args_list
            if "way/111" in c[0][0] and "changeset" not in c[0][0]
        ]
        assert len(way_put_calls) == 1
        data = way_put_calls[0][1]["data"]
        assert 'ref="1"' in data
        assert 'ref="100"' in data
        assert 'ref="3"' in data
        assert 'ref="200"' not in data

    def test_way_version_conflict_rebases(self):
        """Way version changed since changeset → revert rebases onto current version."""
        osmchange = _osmchange_xml([
            ("modify", '<way id="111" version="3"><nd ref="1"/><nd ref="200"/><nd ref="3"/></way>'),
        ])
        way_v2 = _way_xml("111", "2", ["1", "100", "3"])
        current_way = _way_xml("111", "4", ["1", "200", "3"])  # version 4

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "way/111/2": _ok(way_v2),
                "way/111": _ok(current_way),
            }))
            mock_req.put = MagicMock(side_effect=_make_put_router({
                "changeset/create": _ok("12345"),
                "way/111": _ok("5"),
                "changeset/12345/close": _ok(),
            }))

            result = revert_changeset(
                "token", "999", "Revert",
                node_ids=[], way_ids=["111"],
            )

        assert result.ways_updated == ["111"]
        # Verify the PUT used version 4 (current), not version 3 (changeset)
        way_put_calls = [
            c for c in mock_req.put.call_args_list
            if "way/111" in c[0][0] and "changeset" not in c[0][0]
        ]
        assert len(way_put_calls) == 1
        data = way_put_calls[0][1]["data"]
        assert 'version="4"' in data
        assert 'ref="100"' in data
        assert 'ref="200"' not in data

    def test_way_preserves_tags(self):
        """Way update preserves tags."""
        osmchange = _osmchange_xml([
            ("modify", '<way id="111" version="3"><nd ref="1"/><nd ref="200"/><nd ref="3"/></way>'),
        ])
        way_v2_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<osm><way id="111" version="2">'
            '<nd ref="1"/><nd ref="100"/><nd ref="3"/>'
            '<tag k="name" v="Main St"/><tag k="highway" v="residential"/>'
            '</way></osm>'
        )
        current_way = _way_xml("111", "3", ["1", "200", "3"],
                               tags={"name": "Main St", "highway": "residential"})

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "way/111/2": _ok(way_v2_xml),
                "way/111": _ok(current_way),
            }))
            mock_req.put = MagicMock(side_effect=_make_put_router({
                "changeset/create": _ok("12345"),
                "way/111": _ok("4"),
                "changeset/12345/close": _ok(),
            }))

            revert_changeset(
                "token", "999", "Revert",
                node_ids=[], way_ids=["111"],
            )

        way_put_calls = [
            c for c in mock_req.put.call_args_list
            if "way/111" in c[0][0] and "changeset" not in c[0][0]
        ]
        data = way_put_calls[0][1]["data"]
        assert "Main St" in data
        assert "highway" in data


# ==============================================================================
# Auto-discover deleted nodes
# ==============================================================================

class TestAutoDiscoverDeletedNodes:
    def test_deleted_node_auto_undeleted(self):
        """Way revert discovers deleted node and undeletes it."""
        osmchange = _osmchange_xml([
            ("modify", '<way id="111" version="3"><nd ref="1"/><nd ref="200"/><nd ref="3"/></way>'),
            ("delete", '<node id="100" version="5" lat="51.0" lon="-1.0"/>'),
        ])
        way_v2 = _way_xml("111", "2", ["1", "100", "3"])
        current_way = _way_xml("111", "3", ["1", "200", "3"])
        node100_v4 = _node_xml("100", "4", "51.0", "-1.0")  # before deletion

        # Deleted node: 410 then history
        node100_410 = MagicMock(status_code=410)
        node100_hist = _ok(_node_xml("100", "5", "51.0", "-1.0", visible=False))

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "way/111/2": _ok(way_v2),
                "way/111": _ok(current_way),
                "node/100/4": _ok(node100_v4),
                "node/100/history": _ok(node100_hist.text),
                "node/100": [node100_410, node100_hist, node100_410],
            }))
            mock_req.put = MagicMock(side_effect=_make_put_router({
                "changeset/create": _ok("12345"),
                "node/100": _ok("6"),
                "way/111": _ok("4"),
                "changeset/12345/close": _ok(),
            }))

            result = revert_changeset(
                "token", "999", "Revert",
                node_ids=[], way_ids=["111"],
            )

        assert "100" in result.nodes_undeleted
        assert "111" in result.ways_updated


# ==============================================================================
# Combined node + way revert tests
# ==============================================================================

class TestCombined:
    def test_node_and_way_reverted(self):
        """Node moved + way modified → both reverted."""
        osmchange = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
            ("modify", '<way id="111" version="3"><nd ref="1"/><nd ref="42"/><nd ref="3"/></way>'),
        ])
        node_v4 = _node_xml("42", "4", "51.0", "-1.0")
        current_node = _node_xml("42", "5", "51.1", "-1.1")
        way_v2 = _way_xml("111", "2", ["1", "42", "3"])
        current_way = _way_xml("111", "3", ["1", "42", "3"])

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "node/42/4": _ok(node_v4),
                "node/42": _ok(current_node),
                "way/111/2": _ok(way_v2),
                "way/111": _ok(current_way),
            }))
            mock_req.put = MagicMock(side_effect=_make_put_router({
                "changeset/create": _ok("12345"),
                "node/42": _ok("6"),
                "way/111": _ok("4"),
                "changeset/12345/close": _ok(),
            }))

            result = revert_changeset(
                "token", "999", "Revert",
                node_ids=["42"], way_ids=["111"],
            )

        assert result.nodes_moved == ["42"]
        assert result.ways_updated == ["111"]


# ==============================================================================
# Safety / error tests
# ==============================================================================

class TestSafetyErrors:
    def test_changeset_closed_on_error(self):
        """Changeset is always closed even when an update fails."""
        osmchange = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
        ])
        node_v4 = _node_xml("42", "4", "51.0", "-1.0")
        current_node = _node_xml("42", "5", "51.1", "-1.1")

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "node/42/4": _ok(node_v4),
                "node/42": _ok(current_node),
            }))
            mock_req.put = MagicMock(side_effect=_make_put_router({
                "changeset/create": _ok("12345"),
                "node/42": MagicMock(status_code=409, ok=False),
                "changeset/12345/close": _ok(),
            }))

            with pytest.raises(ConflictError):
                revert_changeset(
                    "token", "999", "Revert",
                    node_ids=["42"], way_ids=[],
                )

        close_calls = [
            c for c in mock_req.put.call_args_list if "close" in c[0][0]
        ]
        assert len(close_calls) == 1

    def test_401_raises_auth_error(self):
        """HTTP 401 on changeset create → AuthError."""
        osmchange = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
        ])
        node_v4 = _node_xml("42", "4", "51.0", "-1.0")
        current_node = _node_xml("42", "5", "51.1", "-1.1")

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "node/42/4": _ok(node_v4),
                "node/42": _ok(current_node),
            }))
            mock_req.put = MagicMock(return_value=MagicMock(status_code=401, ok=False, text="unauthorized"))

            with pytest.raises(AuthError):
                revert_changeset(
                    "token", "999", "Revert",
                    node_ids=["42"], way_ids=[],
                )

    def test_no_changeset_when_nothing_to_do(self):
        """No changeset created when node was deleted (cannot move)."""
        osmchange = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
        ])
        node_v4 = _node_xml("42", "4", "51.0", "-1.0")
        # Node was deleted by someone else — API returns 410
        deleted_history = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<osm><node id="42" version="6" visible="false"/></osm>'
        )

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "node/42/history": _ok(deleted_history),
                "node/42/4": _ok(node_v4),
                "node/42": _ok(status=410),
            }))
            mock_req.put = MagicMock()

            with pytest.raises(AlreadyRevertedError):
                revert_changeset(
                    "token", "999", "Revert",
                    node_ids=["42"], way_ids=[],
                )

        mock_req.put.assert_not_called()


# ==============================================================================
# Comment test
# ==============================================================================

class TestComment:
    def test_comment_posted_on_original_changeset(self):
        """revert_changeset posts comment on original changeset when requested."""
        osmchange = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
        ])
        node_v4 = _node_xml("42", "4", "51.0", "-1.0")
        current_node = _node_xml("42", "5", "51.1", "-1.1")

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "node/42/4": _ok(node_v4),
                "node/42": _ok(current_node),
            }))
            mock_req.put = MagicMock(side_effect=_make_put_router({
                "changeset/create": _ok("12345"),
                "node/42": _ok("6"),
                "changeset/12345/close": _ok(),
            }))
            mock_req.post = MagicMock(return_value=_ok())

            revert_changeset(
                "token", "999", "Revert",
                node_ids=["42"], way_ids=[],
                changeset_comment="Reverted accidental drag",
            )

        mock_req.post.assert_called_once()
        assert "999/comment" in mock_req.post.call_args[0][0]
        assert mock_req.post.call_args[1]["data"] == {"text": "Reverted accidental drag"}


# ==============================================================================
# Ordering test
# ==============================================================================

class TestOrdering:
    def test_undeletes_before_moves_before_ways(self):
        """Undeletes → node moves → way updates ordering."""
        osmchange = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
            ("delete", '<node id="100" version="5" lat="51.0" lon="-1.0"/>'),
            ("modify", '<way id="111" version="3"><nd ref="1"/><nd ref="200"/><nd ref="3"/></way>'),
        ])
        node42_v4 = _node_xml("42", "4", "51.0", "-1.0")
        current42 = _node_xml("42", "5", "51.1", "-1.1")
        node100_v4 = _node_xml("100", "4", "51.0", "-1.0")
        node100_410 = MagicMock(status_code=410)
        node100_hist = _ok(_node_xml("100", "5", "51.0", "-1.0", visible=False))
        way_v2 = _way_xml("111", "2", ["1", "100", "3"])
        current_way = _way_xml("111", "3", ["1", "200", "3"])

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "node/42/4": _ok(node42_v4),
                "node/42": _ok(current42),
                "node/100/4": _ok(node100_v4),
                "node/100/history": _ok(node100_hist.text),
                "node/100": [node100_410, node100_hist, node100_410],
                "way/111/2": _ok(way_v2),
                "way/111": _ok(current_way),
            }))

            put_urls = []
            original_put_router = _make_put_router({
                "changeset/create": _ok("12345"),
                "node/100": _ok("6"),
                "node/42": _ok("6"),
                "way/111": _ok("4"),
                "changeset/12345/close": _ok(),
            })

            def tracking_put(url, **kwargs):
                put_urls.append(url)
                return original_put_router(url, **kwargs)

            mock_req.put = MagicMock(side_effect=tracking_put)

            result = revert_changeset(
                "token", "999", "Revert",
                node_ids=["42"], way_ids=["111"],
            )

        # Filter to just element update URLs (not changeset create/close)
        element_urls = [u for u in put_urls if "changeset" not in u]
        # undelete node/100 should come before update node/42
        # and both before way/111
        node100_idx = next(i for i, u in enumerate(element_urls) if "node/100" in u)
        node42_idx = next(i for i, u in enumerate(element_urls) if "node/42" in u)
        way111_idx = next(i for i, u in enumerate(element_urls) if "way/111" in u)
        assert node100_idx < node42_idx < way111_idx


# ==============================================================================
# HTTP 409 retry tests
# ==============================================================================

class TestConflictRetry:
    def test_node_move_retries_on_409(self):
        """Node move retries with fresh version after HTTP 409."""
        osmchange = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
        ])
        node_v4 = _node_xml("42", "4", "51.0", "-1.0")
        current_v5 = _node_xml("42", "5", "51.1", "-1.1")
        current_v6 = _node_xml("42", "6", "51.1", "-1.1")  # updated by someone else

        conflict_resp = MagicMock(status_code=409, ok=False, text="Version mismatch")

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "node/42/4": _ok(node_v4),
                # First fetch returns v5, retry fetch returns v6
                "node/42": [_ok(current_v5), _ok(current_v6)],
            }))
            mock_req.put = MagicMock(side_effect=_make_put_router({
                "changeset/create": _ok("12345"),
                # First PUT fails with 409, second succeeds
                "node/42": [conflict_resp, _ok("7")],
                "changeset/12345/close": _ok(),
            }))

            result = revert_changeset(
                "token", "999", "Revert",
                node_ids=["42"], way_ids=[],
            )

        assert result.nodes_moved == ["42"]
        # Should have made 2 PUT calls for the node (409 + success)
        node_put_calls = [
            c for c in mock_req.put.call_args_list
            if "node/42" in c[0][0] and "changeset" not in c[0][0]
        ]
        assert len(node_put_calls) == 2
        # Second call should use version 6
        data = node_put_calls[1][1]["data"]
        assert 'version="6"' in data

    def test_node_move_raises_after_max_retries(self):
        """Node move raises ConflictError after exhausting retries."""
        osmchange = _osmchange_xml([
            ("modify", '<node id="42" version="5" lat="51.1" lon="-1.1"/>'),
        ])
        node_v4 = _node_xml("42", "4", "51.0", "-1.0")
        current_node = _node_xml("42", "5", "51.1", "-1.1")
        conflict_resp = MagicMock(status_code=409, ok=False, text="Version mismatch")

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=_make_router({
                "changeset/999/download": _ok(osmchange),
                "node/42/4": _ok(node_v4),
                "node/42": _ok(current_node),
            }))
            mock_req.put = MagicMock(side_effect=_make_put_router({
                "changeset/create": _ok("12345"),
                "node/42": [conflict_resp, conflict_resp, conflict_resp],
                "changeset/12345/close": _ok(),
            }))

            with pytest.raises(ConflictError):
                revert_changeset(
                    "token", "999", "Revert",
                    node_ids=["42"], way_ids=[],
                )
