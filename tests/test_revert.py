import json
from unittest.mock import patch, MagicMock, call

from watcher import revert_node, comment_on_changeset, handle_revert_action


class TestRevertNode:
    def test_creates_changeset_updates_node_and_closes(self):
        node_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<osm><node id="42" version="5" lat="51.1" lon="-1.1">'
            '<tag k="name" v="Test"/>'
            '</node></osm>'
        )

        responses = [
            # PUT changeset/create
            MagicMock(status_code=200, text="12345"),
            # GET node/42
            MagicMock(status_code=200, text=node_xml),
            # PUT node/42
            MagicMock(status_code=200, text="6"),
            # PUT changeset/close
            MagicMock(status_code=200),
        ]
        for r in responses:
            r.raise_for_status = MagicMock()

        with patch("watcher.requests") as mock_requests:
            mock_requests.put = MagicMock(side_effect=[responses[0], responses[2], responses[3]])
            mock_requests.get = MagicMock(return_value=responses[1])

            cs_id = revert_node("token123", "42", 51.0, -1.0, "999")

        assert cs_id == "12345"

        # Verify changeset create was called
        create_call = mock_requests.put.call_args_list[0]
        assert "changeset/create" in create_call[0][0]
        assert "999" in create_call[1]["data"]

        # Verify node update preserves tags
        update_call = mock_requests.put.call_args_list[1]
        assert "node/42" in update_call[0][0]
        assert 'lat="51.0"' in update_call[1]["data"]
        assert 'lon="-1.0"' in update_call[1]["data"]
        assert 'name' in update_call[1]["data"]
        assert 'Test' in update_call[1]["data"]

        # Verify changeset close
        close_call = mock_requests.put.call_args_list[2]
        assert "changeset/12345/close" in close_call[0][0]

    def test_closes_changeset_on_error(self):
        """Changeset must be closed even when node update fails."""
        create_resp = MagicMock(status_code=200, text="12345")
        create_resp.raise_for_status = MagicMock()

        node_resp = MagicMock(status_code=200, text='<osm><node id="42" version="5" lat="51.1" lon="-1.1"/></osm>')
        node_resp.raise_for_status = MagicMock()

        error_resp = MagicMock(status_code=409)
        error_resp.raise_for_status = MagicMock(side_effect=Exception("Conflict"))

        close_resp = MagicMock(status_code=200)
        close_resp.raise_for_status = MagicMock()

        with patch("watcher.requests") as mock_requests:
            mock_requests.put = MagicMock(side_effect=[create_resp, error_resp, close_resp])
            mock_requests.get = MagicMock(return_value=node_resp)

            try:
                revert_node("token123", "42", 51.0, -1.0, "999")
            except Exception:
                pass

        # Changeset close should still be called
        close_call = mock_requests.put.call_args_list[2]
        assert "changeset/12345/close" in close_call[0][0]


class TestCommentOnChangeset:
    def test_posts_comment(self):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()

        with patch("watcher.requests.post", return_value=resp) as mock_post:
            comment_on_changeset("token123", "999", "Test comment")

        mock_post.assert_called_once()
        assert "999/comment" in mock_post.call_args[0][0]
        assert mock_post.call_args[1]["data"] == {"text": "Test comment"}


class TestHandleRevertAction:
    def _make_body(self, node_id="42", old_lat=51.0, old_lon=-1.0, changeset="999"):
        return {
            "actions": [{
                "value": json.dumps({
                    "node_id": node_id,
                    "old_lat": old_lat,
                    "old_lon": old_lon,
                    "changeset": changeset,
                }),
            }],
            "user": {"username": "testuser"},
            "channel": {"id": "C123"},
            "message": {
                "ts": "1234567890.123456",
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "test"}},
                    {"type": "actions", "elements": []},
                ],
            },
        }

    @patch("watcher.comment_on_changeset")
    @patch("watcher.revert_node", return_value="55555")
    def test_successful_revert(self, mock_revert, mock_comment):
        ack = MagicMock()
        client = MagicMock()
        body = self._make_body()

        handle_revert_action(ack, body, client, "osm_token")

        ack.assert_called_once()
        mock_revert.assert_called_once_with("osm_token", "42", 51.0, -1.0, "999")
        mock_comment.assert_called_once()

        # Message should be updated with confirmation, no actions blocks
        update_call = client.chat_update.call_args
        blocks = update_call[1]["blocks"]
        types = [b["type"] for b in blocks]
        assert "actions" not in types
        assert "context" in types
        assert "55555" in blocks[-1]["elements"][0]["text"]
        assert "testuser" in blocks[-1]["elements"][0]["text"]

    @patch("watcher.revert_node")
    def test_conflict_error(self, mock_revert):
        import requests as real_requests

        error_resp = MagicMock(status_code=409)
        mock_revert.side_effect = real_requests.HTTPError(response=error_resp)

        ack = MagicMock()
        client = MagicMock()
        body = self._make_body()

        handle_revert_action(ack, body, client, "osm_token")

        ack.assert_called_once()
        update_call = client.chat_update.call_args
        blocks = update_call[1]["blocks"]
        assert "manual review" in blocks[-1]["elements"][0]["text"]

    @patch("watcher.revert_node")
    def test_auth_error(self, mock_revert):
        import requests as real_requests

        error_resp = MagicMock(status_code=403)
        mock_revert.side_effect = real_requests.HTTPError(response=error_resp)

        ack = MagicMock()
        client = MagicMock()
        body = self._make_body()

        handle_revert_action(ack, body, client, "osm_token")

        update_call = client.chat_update.call_args
        blocks = update_call[1]["blocks"]
        assert "OSM auth failed" in blocks[-1]["elements"][0]["text"]

    @patch("watcher.revert_node")
    def test_double_click_safe(self, mock_revert):
        """After revert, buttons are removed from the message."""
        mock_revert.return_value = "55555"

        ack = MagicMock()
        client = MagicMock()
        body = self._make_body()

        with patch("watcher.comment_on_changeset"):
            handle_revert_action(ack, body, client, "osm_token")

        update_call = client.chat_update.call_args
        blocks = update_call[1]["blocks"]
        assert not any(b["type"] == "actions" for b in blocks)
