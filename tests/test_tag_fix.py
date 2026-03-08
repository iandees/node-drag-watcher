"""Tests for tag fix module — all HTTP mocked."""

from unittest.mock import patch, MagicMock, call
import pytest

from checkers import Issue
from tag_fix import fix_tags, TagFixError, VersionConflictError


def _ok(text="", status=200):
    r = MagicMock(status_code=status, ok=True, text=text)
    r.raise_for_status = MagicMock()
    return r


def _make_issue(element_type="node", element_id="123", element_version="5",
                tags_before=None, tags_after=None, **kwargs):
    return Issue(
        element_type=element_type,
        element_id=element_id,
        element_version=element_version,
        changeset="999",
        user="testuser",
        check_name="phone_format",
        summary="phone: 2125551234 → +1 212-555-1234",
        tags_before=tags_before or {"phone": "2125551234"},
        tags_after=tags_after or {"phone": "+1 212-555-1234"},
        **kwargs,
    )


class TestFixTags:
    def test_happy_path(self):
        """Fetch current element, verify version, update tag."""
        current_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<osm><node id="123" version="5" lat="40.7" lon="-74.0">'
            '<tag k="phone" v="2125551234"/>'
            '<tag k="name" v="Test Place"/>'
            '</node></osm>'
        )
        issue = _make_issue()

        with patch("tag_fix.requests") as mock_req, \
             patch("tag_fix.create_changeset", return_value="777") as mock_create, \
             patch("tag_fix.close_changeset") as mock_close:
            mock_req.get = MagicMock(return_value=_ok(current_xml))
            mock_req.put = MagicMock(return_value=_ok("6"))

            cs_id = fix_tags("token", [issue])

        assert cs_id == "777"
        mock_create.assert_called_once()
        mock_close.assert_called_once()
        # Verify the update PUT has corrected tag
        update_call = mock_req.put.call_args
        data = update_call[1]["data"]
        assert "+1 212-555-1234" in data
        assert "Test Place" in data  # other tags preserved

    def test_version_mismatch_skips(self):
        """Element edited since detection → skip."""
        current_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<osm><node id="123" version="6" lat="40.7" lon="-74.0">'
            '<tag k="phone" v="2125551234"/>'
            '</node></osm>'
        )
        issue = _make_issue(element_version="5")

        with patch("tag_fix.requests") as mock_req:
            mock_req.get = MagicMock(return_value=_ok(current_xml))

            with pytest.raises(VersionConflictError):
                fix_tags("token", [issue])

    def test_way_element(self):
        """Works for ways too."""
        current_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<osm><way id="456" version="3">'
            '<nd ref="1"/><nd ref="2"/><nd ref="3"/>'
            '<tag k="phone" v="2125551234"/>'
            '</way></osm>'
        )
        issue = _make_issue(element_type="way", element_id="456", element_version="3")

        with patch("tag_fix.requests") as mock_req, \
             patch("tag_fix.create_changeset", return_value="777"), \
             patch("tag_fix.close_changeset"):
            mock_req.get = MagicMock(return_value=_ok(current_xml))
            mock_req.put = MagicMock(return_value=_ok("4"))

            cs_id = fix_tags("token", [issue])

        assert cs_id == "777"
