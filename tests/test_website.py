"""Tests for website URL cleanup checker."""

import pytest
from unittest.mock import patch, MagicMock

from checkers import Action, Issue
from checkers.website import WebsiteChecker, _normalize_url, _try_https_upgrade


def _make_action(tags_new, tags_old=None, action_type="create", **kwargs):
    return Action(
        action_type=action_type,
        element_type="node",
        element_id="123",
        version="1",
        changeset="999",
        user="testuser",
        tags_old=tags_old or {},
        tags_new=tags_new,
        **kwargs,
    )


class TestNormalizeUrl:
    def test_adds_https_scheme(self):
        assert _normalize_url("example.com") == "https://example.com"

    def test_lowercases_domain(self):
        assert _normalize_url("https://EXAMPLE.COM") == "https://example.com"

    def test_strips_utm_params(self):
        result = _normalize_url("https://example.com/page?utm_source=twitter&utm_medium=social")
        assert result == "https://example.com/page"

    def test_strips_fbclid(self):
        result = _normalize_url("https://example.com/page?fbclid=abc123")
        assert result == "https://example.com/page"

    def test_strips_gclid(self):
        result = _normalize_url("https://example.com/page?gclid=abc123")
        assert result == "https://example.com/page"

    def test_preserves_non_tracking_params(self):
        result = _normalize_url("https://example.com/page?id=42&utm_source=twitter")
        assert "id=42" in result
        assert "utm_source" not in result

    def test_fixes_doubled_scheme(self):
        assert _normalize_url("http://Https://optic2000.com") == "https://optic2000.com"
        assert _normalize_url("http://https://example.com") == "https://example.com"
        assert _normalize_url("https://http://example.com") == "http://example.com"
        assert _normalize_url("http://http://example.com") == "http://example.com"

    def test_fixes_truncated_scheme(self):
        assert _normalize_url("ttps://bankonbuffalo.bank") == "https://bankonbuffalo.bank"
        assert _normalize_url("ttp://example.com") == "https://example.com"
        assert _normalize_url("htp://example.com") == "https://example.com"

    def test_strips_trailing_slash_bare_domain(self):
        assert _normalize_url("https://example.com/") == "https://example.com"

    def test_keeps_trailing_slash_with_path(self):
        assert _normalize_url("https://example.com/page/") == "https://example.com/page/"

    def test_keeps_http_scheme(self):
        result = _normalize_url("http://example.com")
        assert result == "http://example.com"

    def test_ignores_mailto(self):
        assert _normalize_url("mailto:test@example.com") is None

    def test_ignores_tel(self):
        assert _normalize_url("tel:+1234567890") is None


class TestTryHttpsUpgrade:
    def test_upgrades_http_to_https(self):
        mock_resp = MagicMock(status_code=200, url="https://example.com")
        with patch("checkers.website.requests.head", return_value=mock_resp):
            result = _try_https_upgrade("http://example.com")
            assert result == "https://example.com"

    def test_keeps_http_on_failure(self):
        with patch("checkers.website.requests.head", side_effect=Exception("timeout")):
            result = _try_https_upgrade("http://example.com")
            assert result == "http://example.com"

    def test_noop_for_already_https(self):
        result = _try_https_upgrade("https://example.com")
        assert result == "https://example.com"

    def test_follows_same_domain_redirect(self):
        mock_resp = MagicMock(status_code=200, url="https://www.example.com/")
        with patch("checkers.website.requests.head", return_value=mock_resp):
            result = _try_https_upgrade("http://example.com")
            assert result == "https://www.example.com/"


class TestWebsiteChecker:
    def setup_method(self):
        self.checker = WebsiteChecker()

    def test_ignores_correct_url(self):
        action = _make_action({"website": "https://example.com"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u):
            assert self.checker.check(action) == []

    def test_formats_bare_domain(self):
        action = _make_action({"website": "example.com"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u):
            issues = self.checker.check(action)
            assert len(issues) == 1
            assert issues[0].tags_after["website"] == "https://example.com"

    def test_strips_tracking_params(self):
        action = _make_action({"website": "https://example.com?utm_source=x"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u):
            issues = self.checker.check(action)
            assert len(issues) == 1
            assert "utm_source" not in issues[0].tags_after["website"]

    def test_checks_contact_website(self):
        action = _make_action({"contact:website": "example.com"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u):
            issues = self.checker.check(action)
            assert len(issues) == 1
            assert "contact:website" in issues[0].tags_after

    def test_checks_url_tag(self):
        action = _make_action({"url": "example.com"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u):
            issues = self.checker.check(action)
            assert len(issues) == 1

    def test_skips_trailing_slash_only(self):
        """Removing trailing slash alone is too minor."""
        action = _make_action({"website": "https://www.qmpizza.com/"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u):
            assert self.checker.check(action) == []

    def test_keeps_http_to_https_upgrade(self):
        """HTTP→HTTPS is significant even if it's a small text change."""
        action = _make_action({"website": "http://example.com"})
        with patch("checkers.website._try_https_upgrade", return_value="https://example.com"):
            issues = self.checker.check(action)
            assert len(issues) == 1
            assert issues[0].tags_after["website"] == "https://example.com"

    def test_ignores_delete_actions(self):
        action = _make_action({"website": "example.com"}, action_type="delete")
        assert self.checker.check(action) == []

    def test_ignores_non_website_tags(self):
        action = _make_action({"name": "Test", "phone": "+1234"})
        assert self.checker.check(action) == []

    def test_issue_fields(self):
        action = _make_action({"website": "example.com"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u):
            issues = self.checker.check(action)
            issue = issues[0]
            assert issue.check_name == "website_cleanup"
            assert issue.element_type == "node"
            assert issue.element_id == "123"
            assert issue.changeset == "999"
            assert issue.tags_before == {"website": "example.com"}
