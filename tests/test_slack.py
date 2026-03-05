from unittest.mock import patch, MagicMock
from watcher import send_slack_alert


def test_send_slack_alert_posts_message():
    drag = {
        "way_id": "12345",
        "way_name": "Test Street",
        "node_id": "67890",
        "distance_meters": 55.3,
        "changeset": "99999",
        "user": "testuser",
    }
    with patch("watcher.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        send_slack_alert("https://hooks.slack.com/test", drag)
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert "Test Street" in payload["text"]
        assert "12345" in payload["text"]
        assert "67890" in payload["text"]
        assert "55.3" in payload["text"]
        assert "99999" in payload["text"]
        assert "testuser" in payload["text"]


def test_send_slack_alert_no_way_name():
    drag = {
        "way_id": "12345",
        "way_name": "",
        "node_id": "67890",
        "distance_meters": 55.3,
        "changeset": "99999",
        "user": "testuser",
    }
    with patch("watcher.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        send_slack_alert("https://hooks.slack.com/test", drag)
        mock_post.assert_called_once()
