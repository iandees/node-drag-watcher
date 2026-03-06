"""Tests for the polling loop behavior."""

import os
import tempfile
from unittest.mock import patch, MagicMock

import requests

from watcher import run_polling, read_state, write_state


def make_http_error(status_code):
    """Create a requests.HTTPError with a given status code."""
    response = MagicMock()
    response.status_code = status_code
    error = requests.HTTPError(response=response)
    return error


def test_polling_stops_at_404_and_does_not_advance_state():
    """When adiff service returns 404, stop processing and don't advance past it."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("100")
        state_file = f.name

    call_count = 0

    def fake_process(url, threshold, webhook_url=None, bot_token=None, channel_id=None):
        nonlocal call_count
        call_count += 1
        if "103" in url:
            raise make_http_error(404)

    loop_count = 0

    def fake_sleep(seconds):
        nonlocal loop_count
        loop_count += 1
        if loop_count >= 1:
            raise KeyboardInterrupt("stop test")

    try:
        with (
            patch("watcher.get_latest_sequence", return_value=105),
            patch("watcher.process_adiff", side_effect=fake_process),
            patch("watcher.time.sleep", side_effect=fake_sleep),
        ):
            try:
                run_polling(None, 10, state_file)
            except KeyboardInterrupt:
                pass

        # Should have processed 101, 102, then stopped at 103 (404)
        assert call_count == 3
        # State should be at 102 (last successful), not 103 or beyond
        assert read_state(state_file) == 102
    finally:
        os.unlink(state_file)


def test_polling_continues_past_non_404_errors():
    """Non-404 HTTP errors should log warning but continue processing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("100")
        state_file = f.name

    call_count = 0

    def fake_process(url, threshold, webhook_url=None, bot_token=None, channel_id=None):
        nonlocal call_count
        call_count += 1
        if "102" in url:
            raise make_http_error(500)

    loop_count = 0

    def fake_sleep(seconds):
        nonlocal loop_count
        loop_count += 1
        if loop_count >= 1:
            raise KeyboardInterrupt("stop test")

    try:
        with (
            patch("watcher.get_latest_sequence", return_value=103),
            patch("watcher.process_adiff", side_effect=fake_process),
            patch("watcher.time.sleep", side_effect=fake_sleep),
        ):
            try:
                run_polling(None, 10, state_file)
            except KeyboardInterrupt:
                pass

        # Should have processed all 3 (101, 102, 103)
        assert call_count == 3
        # State should advance past the 500 error
        assert read_state(state_file) == 103
    finally:
        os.unlink(state_file)


def test_read_write_state():
    """State file round-trips correctly."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        state_file = f.name

    try:
        assert read_state(state_file + ".nonexistent") is None
        write_state(state_file, 12345)
        assert read_state(state_file) == 12345
        write_state(state_file, 99999)
        assert read_state(state_file) == 99999
    finally:
        os.unlink(state_file)
