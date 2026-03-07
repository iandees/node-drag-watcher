"""Tests for the revert module — all HTTP requests are mocked."""

import xml.etree.ElementTree as ET
from unittest.mock import patch, MagicMock, call

import pytest

from revert import (
    NodeMove, NodeUndelete, WayNodeSwap, RevertResult,
    RevertError, AlreadyRevertedError, ConflictError, AuthError,
    revert_changeset, comment_on_changeset,
    _node_at_position, _way_has_node_ref,
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


# ==============================================================================
# NodeMove tests
# ==============================================================================

class TestNodeMove:
    def test_happy_path_node_moved_back(self):
        """Node at new position → moved back to old position."""
        node_resp = _ok(_node_xml("42", "5", "51.1", "-1.1"))
        create_resp = _ok("12345")
        update_resp = _ok("6")
        close_resp = _ok()

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(return_value=node_resp)
            mock_req.put = MagicMock(side_effect=[create_resp, update_resp, close_resp])
            mock_req.post = MagicMock()

            result = revert_changeset(
                "token", "999", "Revert drag",
                node_moves=[NodeMove("42", 51.0, -1.0, 51.1, -1.1)],
            )

        assert result.revert_changeset_id == "12345"
        assert result.nodes_moved == ["42"]
        assert result.skipped == []

    def test_already_fixed_raises(self):
        """Node no longer at expected position → AlreadyRevertedError."""
        node_resp = _ok(_node_xml("42", "5", "51.0", "-1.0"))  # already at old pos

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(return_value=node_resp)

            with pytest.raises(AlreadyRevertedError):
                revert_changeset(
                    "token", "999", "Revert",
                    node_moves=[NodeMove("42", 51.0, -1.0, 51.1, -1.1)],
                )

    def test_preserves_tags(self):
        """Node update XML should include original tags."""
        node_resp = _ok(_node_xml("42", "5", "51.1", "-1.1", tags={"name": "Test", "highway": "crossing"}))
        create_resp = _ok("12345")
        update_resp = _ok("6")
        close_resp = _ok()

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(return_value=node_resp)
            mock_req.put = MagicMock(side_effect=[create_resp, update_resp, close_resp])

            revert_changeset(
                "token", "999", "Revert",
                node_moves=[NodeMove("42", 51.0, -1.0, 51.1, -1.1)],
            )

        # The update PUT (second call) should contain the tags
        update_call = mock_req.put.call_args_list[1]
        data = update_call[1]["data"]
        assert "name" in data
        assert "Test" in data
        assert "highway" in data

    def test_multiple_moves_in_one_call(self):
        """Two nodes moved in one changeset."""
        node42 = _ok(_node_xml("42", "5", "51.1", "-1.1"))
        node43 = _ok(_node_xml("43", "3", "52.1", "-2.1"))
        create_resp = _ok("12345")
        update1 = _ok("6")
        update2 = _ok("4")
        close_resp = _ok()

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=[node42, node43])
            mock_req.put = MagicMock(side_effect=[create_resp, update1, update2, close_resp])

            result = revert_changeset(
                "token", "999", "Revert",
                node_moves=[
                    NodeMove("42", 51.0, -1.0, 51.1, -1.1),
                    NodeMove("43", 52.0, -2.0, 52.1, -2.1),
                ],
            )

        assert result.nodes_moved == ["42", "43"]


# ==============================================================================
# NodeUndelete tests
# ==============================================================================

class TestNodeUndelete:
    def test_happy_path_deleted_node_restored(self):
        """Deleted node → undeleted at given position."""
        # First GET returns 410 (deleted), then history fetch
        get_resp = MagicMock(status_code=410)
        hist_xml = _node_xml("100", "5", "51.0", "-1.0", visible=False)
        hist_resp = _ok(hist_xml)
        create_resp = _ok("12345")
        undelete_resp = _ok("6")
        close_resp = _ok()

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=[get_resp, hist_resp])
            mock_req.put = MagicMock(side_effect=[create_resp, undelete_resp, close_resp])

            result = revert_changeset(
                "token", "999", "Revert",
                node_undeletes=[NodeUndelete("100", 51.0, -1.0)],
            )

        assert result.nodes_undeleted == ["100"]

        # Verify the PUT includes visible="true"
        undelete_call = mock_req.put.call_args_list[1]
        assert 'visible="true"' in undelete_call[1]["data"]

    def test_already_visible_skipped(self):
        """Node already visible → skip, raises AlreadyRevertedError."""
        node_resp = _ok(_node_xml("100", "5", "51.0", "-1.0"))

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(return_value=node_resp)

            with pytest.raises(AlreadyRevertedError):
                revert_changeset(
                    "token", "999", "Revert",
                    node_undeletes=[NodeUndelete("100", 51.0, -1.0)],
                )

    def test_410_fetches_history(self):
        """When node returns 410, history endpoint is used to get last version."""
        get_resp = MagicMock(status_code=410)
        hist_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<osm>'
            '<node id="100" version="4" lat="51.0" lon="-1.0" visible="true"/>'
            '<node id="100" version="5" lat="51.0" lon="-1.0" visible="false"/>'
            '</osm>'
        )
        hist_resp = _ok(hist_xml)
        create_resp = _ok("12345")
        undelete_resp = _ok("6")
        close_resp = _ok()

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=[get_resp, hist_resp])
            mock_req.put = MagicMock(side_effect=[create_resp, undelete_resp, close_resp])

            result = revert_changeset(
                "token", "999", "Revert",
                node_undeletes=[NodeUndelete("100", 51.0, -1.0)],
            )

        # Should have fetched history
        assert "history" in mock_req.get.call_args_list[1][0][0]
        assert result.nodes_undeleted == ["100"]


# ==============================================================================
# WayNodeSwap tests
# ==============================================================================

class TestWayNodeSwap:
    def test_happy_path_ref_swapped(self):
        """Way references new_ref → swapped to old_ref."""
        way_resp = _ok(_way_xml("111", "3", ["1", "200", "3"]))
        create_resp = _ok("12345")
        update_resp = _ok("4")
        close_resp = _ok()

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(return_value=way_resp)
            mock_req.put = MagicMock(side_effect=[create_resp, update_resp, close_resp])

            result = revert_changeset(
                "token", "999", "Revert",
                way_node_swaps=[WayNodeSwap("111", "100", "200")],
            )

        assert result.ways_updated == ["111"]
        # Verify the way XML has old_ref instead of new_ref
        update_call = mock_req.put.call_args_list[1]
        data = update_call[1]["data"]
        assert 'ref="100"' in data
        assert 'ref="200"' not in data

    def test_way_already_fixed_skipped(self):
        """Way doesn't reference new_ref → skip, raises AlreadyRevertedError."""
        way_resp = _ok(_way_xml("111", "3", ["1", "100", "3"]))  # already has old_ref

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(return_value=way_resp)

            with pytest.raises(AlreadyRevertedError):
                revert_changeset(
                    "token", "999", "Revert",
                    way_node_swaps=[WayNodeSwap("111", "100", "200")],
                )

    def test_multi_way_updated(self):
        """Two ways swapped in one changeset."""
        way111 = _ok(_way_xml("111", "3", ["1", "200", "3"]))
        way222 = _ok(_way_xml("222", "5", ["4", "200", "6"]))
        create_resp = _ok("12345")
        update1 = _ok("4")
        update2 = _ok("6")
        close_resp = _ok()

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=[way111, way222])
            mock_req.put = MagicMock(side_effect=[create_resp, update1, update2, close_resp])

            result = revert_changeset(
                "token", "999", "Revert",
                way_node_swaps=[
                    WayNodeSwap("111", "100", "200"),
                    WayNodeSwap("222", "100", "200"),
                ],
            )

        assert result.ways_updated == ["111", "222"]

    def test_preserves_way_tags_and_other_nds(self):
        """Way update preserves tags and non-swapped nds."""
        way_resp = _ok(_way_xml("111", "3", ["1", "200", "3"],
                                tags={"name": "Main St", "highway": "residential"}))
        create_resp = _ok("12345")
        update_resp = _ok("4")
        close_resp = _ok()

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(return_value=way_resp)
            mock_req.put = MagicMock(side_effect=[create_resp, update_resp, close_resp])

            revert_changeset(
                "token", "999", "Revert",
                way_node_swaps=[WayNodeSwap("111", "100", "200")],
            )

        update_call = mock_req.put.call_args_list[1]
        data = update_call[1]["data"]
        assert 'ref="1"' in data
        assert 'ref="3"' in data
        assert "Main St" in data
        assert "highway" in data


# ==============================================================================
# Combined tests
# ==============================================================================

class TestCombined:
    def test_substitution_scenario(self):
        """Undelete node + swap way refs in one operation."""
        # Node 100 deleted (410 + history)
        node_get = MagicMock(status_code=410)
        node_hist = _ok(_node_xml("100", "5", "51.0", "-1.0", visible=False))
        # Way 111 references new node 200
        way_resp = _ok(_way_xml("111", "3", ["1", "200", "3"]))
        create_resp = _ok("12345")
        undelete_resp = _ok("6")
        swap_resp = _ok("4")
        close_resp = _ok()

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=[node_get, node_hist, way_resp])
            mock_req.put = MagicMock(side_effect=[create_resp, undelete_resp, swap_resp, close_resp])

            result = revert_changeset(
                "token", "999", "Revert",
                node_undeletes=[NodeUndelete("100", 51.0, -1.0)],
                way_node_swaps=[WayNodeSwap("111", "100", "200")],
            )

        assert result.nodes_undeleted == ["100"]
        assert result.ways_updated == ["111"]

    def test_partial_success(self):
        """Node undeleted but one way already fixed → way skipped."""
        node_get = MagicMock(status_code=410)
        node_hist = _ok(_node_xml("100", "5", "51.0", "-1.0", visible=False))
        way_resp = _ok(_way_xml("111", "3", ["1", "100", "3"]))  # already has old_ref
        create_resp = _ok("12345")
        undelete_resp = _ok("6")
        close_resp = _ok()

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=[node_get, node_hist, way_resp])
            mock_req.put = MagicMock(side_effect=[create_resp, undelete_resp, close_resp])

            result = revert_changeset(
                "token", "999", "Revert",
                node_undeletes=[NodeUndelete("100", 51.0, -1.0)],
                way_node_swaps=[WayNodeSwap("111", "100", "200")],
            )

        assert result.nodes_undeleted == ["100"]
        assert result.ways_updated == []
        assert len(result.skipped) == 1

    def test_undeletes_before_swaps(self):
        """Undeletes are applied before way swaps (ordering)."""
        node_get = MagicMock(status_code=410)
        node_hist = _ok(_node_xml("100", "5", "51.0", "-1.0", visible=False))
        way_resp = _ok(_way_xml("111", "3", ["1", "200", "3"]))
        create_resp = _ok("12345")
        undelete_resp = _ok("6")
        swap_resp = _ok("4")
        close_resp = _ok()

        call_order = []

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(side_effect=[node_get, node_hist, way_resp])

            def track_put(*args, **kwargs):
                url = args[0]
                if "node/" in url and "changeset" not in url:
                    call_order.append("undelete")
                elif "way/" in url:
                    call_order.append("swap")
                return [create_resp, undelete_resp, swap_resp, close_resp][len(call_order)]

            # Use side_effect list to track order
            mock_req.put = MagicMock(side_effect=[create_resp, undelete_resp, swap_resp, close_resp])

            revert_changeset(
                "token", "999", "Revert",
                node_undeletes=[NodeUndelete("100", 51.0, -1.0)],
                way_node_swaps=[WayNodeSwap("111", "100", "200")],
            )

        # Verify ordering: create, undelete, swap, close
        put_urls = [c[0][0] for c in mock_req.put.call_args_list]
        node_idx = next(i for i, u in enumerate(put_urls) if "node/" in u)
        way_idx = next(i for i, u in enumerate(put_urls) if "way/" in u)
        assert node_idx < way_idx


# ==============================================================================
# Safety / error tests
# ==============================================================================

class TestSafetyErrors:
    def test_changeset_closed_on_error(self):
        """Changeset is always closed even when an update fails."""
        node_resp = _ok(_node_xml("42", "5", "51.1", "-1.1"))
        create_resp = _ok("12345")
        error_resp = MagicMock(status_code=409, ok=False)
        close_resp = _ok()

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(return_value=node_resp)
            mock_req.put = MagicMock(side_effect=[create_resp, error_resp, close_resp])

            with pytest.raises(ConflictError):
                revert_changeset(
                    "token", "999", "Revert",
                    node_moves=[NodeMove("42", 51.0, -1.0, 51.1, -1.1)],
                )

        # Changeset close must have been called
        close_call = mock_req.put.call_args_list[2]
        assert "close" in close_call[0][0]

    def test_401_raises_auth_error(self):
        """HTTP 401 → AuthError."""
        node_resp = _ok(_node_xml("42", "5", "51.1", "-1.1"))
        create_resp = MagicMock(status_code=401, ok=False)

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(return_value=node_resp)
            mock_req.put = MagicMock(return_value=create_resp)

            with pytest.raises(AuthError):
                revert_changeset(
                    "token", "999", "Revert",
                    node_moves=[NodeMove("42", 51.0, -1.0, 51.1, -1.1)],
                )

    def test_409_raises_conflict_error(self):
        """HTTP 409 on node update → ConflictError."""
        node_resp = _ok(_node_xml("42", "5", "51.1", "-1.1"))
        create_resp = _ok("12345")
        conflict_resp = MagicMock(status_code=409, ok=False)
        close_resp = _ok()

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(return_value=node_resp)
            mock_req.put = MagicMock(side_effect=[create_resp, conflict_resp, close_resp])

            with pytest.raises(ConflictError):
                revert_changeset(
                    "token", "999", "Revert",
                    node_moves=[NodeMove("42", 51.0, -1.0, 51.1, -1.1)],
                )

    def test_no_changeset_when_nothing_to_do(self):
        """No changeset should be created when everything is already reverted."""
        node_resp = _ok(_node_xml("42", "5", "51.0", "-1.0"))

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(return_value=node_resp)
            mock_req.put = MagicMock()

            with pytest.raises(AlreadyRevertedError):
                revert_changeset(
                    "token", "999", "Revert",
                    node_moves=[NodeMove("42", 51.0, -1.0, 51.1, -1.1)],
                )

        # put should never be called (no changeset create)
        mock_req.put.assert_not_called()


# ==============================================================================
# Helper tests
# ==============================================================================

class TestHelpers:
    def test_node_at_position_match(self):
        elem = ET.fromstring('<node id="1" lat="51.0" lon="-1.0"/>')
        assert _node_at_position(elem, 51.0, -1.0) is True

    def test_node_at_position_mismatch(self):
        elem = ET.fromstring('<node id="1" lat="51.1" lon="-1.0"/>')
        assert _node_at_position(elem, 51.0, -1.0) is False

    def test_way_has_node_ref_true(self):
        elem = ET.fromstring('<way id="1"><nd ref="10"/><nd ref="20"/><nd ref="30"/></way>')
        assert _way_has_node_ref(elem, "20") is True

    def test_way_has_node_ref_false(self):
        elem = ET.fromstring('<way id="1"><nd ref="10"/><nd ref="20"/><nd ref="30"/></way>')
        assert _way_has_node_ref(elem, "99") is False


# ==============================================================================
# Comment test
# ==============================================================================

class TestComment:
    def test_comment_posted_on_original_changeset(self):
        """revert_changeset posts comment on original changeset when requested."""
        node_resp = _ok(_node_xml("42", "5", "51.1", "-1.1"))
        create_resp = _ok("12345")
        update_resp = _ok("6")
        close_resp = _ok()
        comment_resp = _ok()

        with patch("revert.requests") as mock_req:
            mock_req.get = MagicMock(return_value=node_resp)
            mock_req.put = MagicMock(side_effect=[create_resp, update_resp, close_resp])
            mock_req.post = MagicMock(return_value=comment_resp)

            revert_changeset(
                "token", "999", "Revert",
                node_moves=[NodeMove("42", 51.0, -1.0, 51.1, -1.1)],
                changeset_comment="Reverted accidental drag",
            )

        mock_req.post.assert_called_once()
        assert "999/comment" in mock_req.post.call_args[0][0]
        assert mock_req.post.call_args[1]["data"] == {"text": "Reverted accidental drag"}
