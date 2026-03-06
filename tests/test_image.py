import io
from unittest.mock import patch, MagicMock

from PIL import Image

from watcher import (
    generate_drag_image,
    upload_slack_image,
    _lon_to_tile_x,
    _lat_to_tile_y,
    _latlon_to_pixel,
    _choose_zoom,
)


def _make_drag():
    return {
        "way_id": "12345",
        "way_name": "Test Street",
        "node_id": "2",
        "distance_meters": 55.0,
        "changeset": "99999",
        "user": "testuser",
        "old_way_coords": [
            (40.0000, -74.0000),
            (40.0010, -74.0010),
            (40.0020, -74.0020),
            (40.0030, -74.0030),
        ],
        "new_way_coords": [
            (40.0000, -74.0000),
            (40.0015, -74.0010),
            (40.0020, -74.0020),
            (40.0030, -74.0030),
        ],
        "dragged_node_old": (40.0010, -74.0010),
        "dragged_node_new": (40.0015, -74.0010),
    }


def _make_tile_png():
    """Create a minimal 256x256 PNG tile."""
    img = Image.new("RGB", (256, 256), (200, 200, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_tile_math_basics():
    # Zoom 0: entire world is one tile
    assert _lon_to_tile_x(-180, 0) == 0.0
    assert _lon_to_tile_x(0, 0) == 0.5
    assert abs(_lat_to_tile_y(0, 0) - 0.5) < 0.01


def test_choose_zoom():
    # Small bbox should get high zoom
    zoom = _choose_zoom(40.0, -74.01, 40.01, -74.0)
    assert zoom >= 14

    # Large bbox should get low zoom
    zoom = _choose_zoom(-60.0, -180.0, 60.0, 180.0)
    assert zoom <= 3


def test_generate_drag_image_produces_png():
    """generate_drag_image should produce valid PNG bytes with mocked tiles."""
    drag = _make_drag()
    tile_png = _make_tile_png()

    mock_resp = MagicMock()
    mock_resp.content = tile_png
    mock_resp.raise_for_status = MagicMock()

    with patch("watcher.requests.get", return_value=mock_resp):
        result = generate_drag_image(drag)

    assert result is not None
    # Verify it's a valid PNG
    img = Image.open(io.BytesIO(result))
    assert img.format == "PNG"
    assert img.size[0] > 0 and img.size[1] > 0


def test_generate_drag_image_missing_coords():
    """generate_drag_image returns None when geometry is missing."""
    drag = {"way_id": "123", "node_id": "1"}
    assert generate_drag_image(drag) is None


def test_generate_drag_image_tile_failure():
    """Image generation should still succeed even if tile fetches fail."""
    drag = _make_drag()

    with patch("watcher.requests.get", side_effect=Exception("network error")):
        result = generate_drag_image(drag)

    # Should still return an image (with blank tiles)
    assert result is not None
    img = Image.open(io.BytesIO(result))
    assert img.format == "PNG"


def test_upload_slack_image():
    """Verify the 3-step Slack upload flow."""
    mock_get = MagicMock()
    mock_get.return_value.json.return_value = {
        "ok": True,
        "upload_url": "https://files.slack.com/upload/v1/test",
        "file_id": "F123",
    }
    mock_get.return_value.raise_for_status = MagicMock()

    mock_post = MagicMock()
    mock_post.return_value.raise_for_status = MagicMock()
    mock_post.return_value.json.return_value = {"ok": True}

    with patch("watcher.requests.get", mock_get), \
         patch("watcher.requests.post", mock_post):
        upload_slack_image("xoxb-test", "C123", b"fakepng", "test.png")

    # Step 1: getUploadURLExternal
    mock_get.assert_called_once()
    call_args = mock_get.call_args
    assert "files.getUploadURLExternal" in call_args[0][0]
    assert call_args[1]["headers"]["Authorization"] == "Bearer xoxb-test"

    # Step 2 + 3: upload + completeUploadExternal
    assert mock_post.call_count == 2
    # Step 2: POST to upload URL
    assert mock_post.call_args_list[0][0][0] == "https://files.slack.com/upload/v1/test"
    # Step 3: completeUploadExternal
    assert "completeUploadExternal" in mock_post.call_args_list[1][0][0]


def test_send_slack_summary_no_bot_token():
    """Image upload is skipped when bot_token is None."""
    from watcher import send_slack_summary

    drags = [{
        "way_id": "12345", "way_name": "Test St", "node_id": "2",
        "distance_meters": 55.0, "changeset": "99999", "user": "testuser",
        "old_angle": 180.0, "new_angle": 5.0,
        "old_way_coords": [(40.0, -74.0), (40.001, -74.001)],
        "new_way_coords": [(40.0, -74.0), (40.002, -74.001)],
        "dragged_node_old": (40.001, -74.001),
        "dragged_node_new": (40.002, -74.001),
    }]

    with patch("watcher.requests.post") as mock_post, \
         patch("watcher.generate_drag_image") as mock_gen, \
         patch("watcher.upload_slack_image") as mock_upload:
        mock_post.return_value = MagicMock(status_code=200)
        send_slack_summary("https://hooks.slack.com/test", drags)

        # Text message sent
        mock_post.assert_called_once()
        # No image generation or upload
        mock_gen.assert_not_called()
        mock_upload.assert_not_called()


def test_send_slack_summary_with_bot_token():
    """Image is generated and uploaded when bot_token and channel_id are set."""
    from watcher import send_slack_summary

    drags = [{
        "way_id": "12345", "way_name": "Test St", "node_id": "2",
        "distance_meters": 55.0, "changeset": "99999", "user": "testuser",
        "old_angle": 180.0, "new_angle": 5.0,
        "old_way_coords": [(40.0, -74.0), (40.001, -74.001)],
        "new_way_coords": [(40.0, -74.0), (40.002, -74.001)],
        "dragged_node_old": (40.001, -74.001),
        "dragged_node_new": (40.002, -74.001),
    }]

    with patch("watcher.requests.post") as mock_post, \
         patch("watcher.generate_drag_image", return_value=b"fakepng") as mock_gen, \
         patch("watcher.upload_slack_image") as mock_upload:
        mock_post.return_value = MagicMock(status_code=200)
        send_slack_summary("https://hooks.slack.com/test", drags,
                          bot_token="xoxb-test", channel_id="C123")

        mock_gen.assert_called_once()
        mock_upload.assert_called_once_with("xoxb-test", "C123", b"fakepng",
                                            "drag_way12345_node2.png")
