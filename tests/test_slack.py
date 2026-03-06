from unittest.mock import patch, MagicMock, ANY
from watcher import send_slack_summary, send_slack_interactive


def test_single_node_single_way():
    """One node dragged on one way = one message."""
    drags = [{
        "way_id": "12345",
        "way_name": "Test Street",
        "node_id": "67890",
        "distance_meters": 55.3,
        "changeset": "99999",
        "user": "testuser",
        "old_angle": 180.0,
        "new_angle": 5.0,
    }]
    with patch("watcher.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        send_slack_summary("https://hooks.slack.com/test", drags)
        mock_post.assert_called_once()
        text = mock_post.call_args[1]["json"]["text"]
        assert "99999" in text
        assert "testuser" in text
        assert "67890" in text
        assert "55.3" in text
        assert "Test Street" in text
        assert "12345" in text


def test_single_node_multiple_ways():
    """One node dragged affecting two ways = one message with both ways listed."""
    drags = [
        {
            "way_id": "111", "way_name": "Main St", "node_id": "42",
            "distance_meters": 100.0, "changeset": "999", "user": "bob",
            "old_angle": 180.0, "new_angle": 3.0,
        },
        {
            "way_id": "222", "way_name": "Oak Ave", "node_id": "42",
            "distance_meters": 100.0, "changeset": "999", "user": "bob",
            "old_angle": None, "new_angle": None,
        },
    ]
    with patch("watcher.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        send_slack_summary("https://hooks.slack.com/test", drags)
        mock_post.assert_called_once()
        text = mock_post.call_args[1]["json"]["text"]
        assert "111" in text
        assert "222" in text
        assert "Main St" in text
        assert "Oak Ave" in text
        assert "ways" in text  # plural


def test_multiple_changesets():
    """Drags from different changesets = separate messages."""
    drags = [
        {
            "way_id": "111", "way_name": "", "node_id": "1",
            "distance_meters": 50.0, "changeset": "100", "user": "alice",
            "old_angle": 180.0, "new_angle": 5.0,
        },
        {
            "way_id": "222", "way_name": "", "node_id": "2",
            "distance_meters": 75.0, "changeset": "200", "user": "bob",
            "old_angle": 180.0, "new_angle": 3.0,
        },
    ]
    with patch("watcher.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        send_slack_summary("https://hooks.slack.com/test", drags)
        assert mock_post.call_count == 2


def test_substitution_node_links_to_new():
    """For old->new substitution, the link should point to the new node."""
    drags = [{
        "way_id": "111", "way_name": "", "node_id": "100->200",
        "distance_meters": 300.0, "changeset": "999", "user": "testuser",
        "old_angle": None, "new_angle": None,
    }]
    with patch("watcher.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        send_slack_summary("https://hooks.slack.com/test", drags)
        text = mock_post.call_args[1]["json"]["text"]
        assert "node/200" in text
        assert "100->200" in text


def _make_drag(**overrides):
    drag = {
        "way_id": "111",
        "way_name": "Main St",
        "node_id": "42",
        "distance_meters": 100.0,
        "changeset": "999",
        "user": "bob",
        "old_angle": 180.0,
        "new_angle": 3.0,
        "dragged_node_old": (51.0, -1.0),
        "dragged_node_new": (51.1, -1.1),
    }
    drag.update(overrides)
    return drag


def test_interactive_delegates_to_send_slack_interactive():
    """When interactive=True, send_slack_summary delegates to send_slack_interactive."""
    drags = [_make_drag()]
    with patch("watcher.send_slack_interactive") as mock_interactive:
        send_slack_summary(
            "https://hooks.slack.com/test", drags,
            bot_token="xoxb-test", channel_id="C123", interactive=True,
        )
        mock_interactive.assert_called_once_with("xoxb-test", "C123", drags)


def test_interactive_false_uses_webhook():
    """When interactive=False, send_slack_summary uses the webhook."""
    drags = [_make_drag()]
    with patch("watcher.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        send_slack_summary(
            "https://hooks.slack.com/test", drags,
            bot_token="xoxb-test", channel_id="C123", interactive=False,
        )
        mock_post.assert_called_once()


def test_send_slack_interactive_posts_blocks():
    """send_slack_interactive posts via chat.postMessage with blocks."""
    drags = [_make_drag()]
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"ok": True}
    resp.raise_for_status = MagicMock()

    with patch("watcher.requests.post", return_value=resp) as mock_post:
        with patch("watcher.generate_drag_image", return_value=None):
            send_slack_interactive("xoxb-test", "C123", drags)

    # Should have called chat.postMessage
    call_args = mock_post.call_args
    assert "chat.postMessage" in call_args[0][0]
    payload = call_args[1]["json"]
    assert payload["channel"] == "C123"
    assert "blocks" in payload
    assert any(b["type"] == "actions" for b in payload["blocks"])
