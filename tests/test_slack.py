from unittest.mock import patch, MagicMock
from notifiers.slack import send_slack_summary, send_slack_interactive


def _mock_post_ok():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"ok": True, "ts": "111.222"}
    resp.raise_for_status = MagicMock()
    return resp


def test_single_node_single_way():
    """One node dragged on one way = one summary + one reverter link."""
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
    with patch("notifiers.slack.requests.post", return_value=_mock_post_ok()) as mock_post:
        send_slack_summary("xoxb-test", "C123", drags)
        # First call is the summary, second is the reverter link
        text = mock_post.call_args_list[0][1]["json"]["text"]
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
    with patch("notifiers.slack.requests.post", return_value=_mock_post_ok()) as mock_post:
        send_slack_summary("xoxb-test", "C123", drags)
        text = mock_post.call_args_list[0][1]["json"]["text"]
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
    with patch("notifiers.slack.requests.post", return_value=_mock_post_ok()) as mock_post:
        send_slack_summary("xoxb-test", "C123", drags)
        # 2 summaries + 2 reverter links = 4 calls
        assert mock_post.call_count == 4


def test_substitution_node_links_to_new():
    """For substitution, the link should point to the node ID."""
    drags = [{
        "way_id": "111", "way_name": "", "node_id": "200",
        "is_substitution": True,
        "distance_meters": 300.0, "changeset": "999", "user": "testuser",
        "old_angle": None, "new_angle": None,
    }]
    with patch("notifiers.slack.requests.post", return_value=_mock_post_ok()) as mock_post:
        send_slack_summary("xoxb-test", "C123", drags)
        text = mock_post.call_args_list[0][1]["json"]["text"]
        assert "node/200" in text


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
    with patch("notifiers.slack.send_slack_interactive") as mock_interactive:
        send_slack_summary("xoxb-test", "C123", drags, interactive=True)
        mock_interactive.assert_called_once_with("xoxb-test", "C123", drags)


def test_non_interactive_posts_summary_and_reverter():
    """Non-interactive mode posts summary then reverter link as thread."""
    drags = [_make_drag()]
    with patch("notifiers.slack.requests.post", return_value=_mock_post_ok()) as mock_post:
        send_slack_summary("xoxb-test", "C123", drags)
        # First call: summary, second call: reverter link
        assert mock_post.call_count == 2
        assert "chat.postMessage" in mock_post.call_args_list[0][0][0]
        assert "chat.postMessage" in mock_post.call_args_list[1][0][0]
        # Reverter link should be threaded
        assert mock_post.call_args_list[1][1]["json"]["thread_ts"] == "111.222"


def test_reverter_link_contains_query_params():
    """Reverter link includes changesets, query-filter, comment, discussion."""
    drags = [_make_drag()]
    with patch("notifiers.slack.requests.post", return_value=_mock_post_ok()) as mock_post:
        send_slack_summary("xoxb-test", "C123", drags)
        reverter_text = mock_post.call_args_list[1][1]["json"]["text"]
        assert "revert.monicz.dev" in reverter_text
        assert "changesets=999" in reverter_text
        assert "n42" in reverter_text
        assert "w111" in reverter_text


def test_send_slack_interactive_posts_blocks_and_reverter():
    """send_slack_interactive posts blocks then reverter link."""
    drags = [_make_drag()]

    with patch("notifiers.slack.requests.post", return_value=_mock_post_ok()) as mock_post:
        with patch("notifiers.slack.generate_drag_image", return_value=None):
            send_slack_interactive("xoxb-test", "C123", drags)

    # First call: summary with blocks, second: reverter link
    first_call = mock_post.call_args_list[0]
    assert "chat.postMessage" in first_call[0][0]
    payload = first_call[1]["json"]
    assert payload["channel"] == "C123"
    assert "blocks" in payload
    assert any(b["type"] == "actions" for b in payload["blocks"])
