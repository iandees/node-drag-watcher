from unittest.mock import patch, MagicMock
from watcher import send_slack_summary


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
