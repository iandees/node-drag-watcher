"""Tests for website URL cleanup checker."""

import pytest
from unittest.mock import patch, MagicMock

from checkers import Action, Issue
from checkers.website import WebsiteChecker, _normalize_url, _is_trivial_url_change, _try_https_upgrade, _try_expand_shortener


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

    def test_strips_y_source(self):
        result = _normalize_url("https://example.com/page?y_source=abc123")
        assert result == "https://example.com/page"

    def test_strips_fbclid(self):
        result = _normalize_url("https://example.com/page?fbclid=abc123")
        assert result == "https://example.com/page"

    def test_strips_gclid(self):
        result = _normalize_url("https://example.com/page?gclid=abc123")
        assert result == "https://example.com/page"

    def test_unwraps_google_redirect(self):
        result = _normalize_url(
            "https://www.google.com/url?sa=t&source=web&rct=j&url=https://locations.raisingcanes.com/il/chicago/3700-north-clark-street"
        )
        assert result == "https://locations.raisingcanes.com/il/chicago/3700-north-clark-street"

    def test_unwraps_google_country_redirect(self):
        result = _normalize_url(
            "https://www.google.co.uk/url?sa=t&url=https://example.com/page"
        )
        assert result == "https://example.com/page"

    def test_unwraps_facebook_redirect(self):
        result = _normalize_url(
            "https://l.facebook.com/l.php?u=https://example.com/shop&h=abc123"
        )
        assert result == "https://example.com/shop"

    def test_unwraps_vk_redirect(self):
        result = _normalize_url(
            "https://away.vk.com/away.php?to=https://example.com"
        )
        assert result == "https://example.com"

    def test_unwraps_youtube_redirect(self):
        result = _normalize_url(
            "https://www.youtube.com/redirect?q=https://example.com&event=video_description"
        )
        assert result == "https://example.com"

    def test_preserves_non_tracking_params(self):
        result = _normalize_url("https://example.com/page?id=42&utm_source=twitter")
        assert "id=42" in result
        assert "utm_source" not in result

    def test_strips_junk_before_url(self):
        """Stray text before a valid embedded URL."""
        assert _normalize_url("h https://erea-nelson-mandela-lille.59.ac-lille.fr/") == "https://erea-nelson-mandela-lille.59.ac-lille.fr"
        assert _normalize_url("x http://example.com/path") == "http://example.com/path"

    def test_fixes_doubled_scheme(self):
        assert _normalize_url("http://Https://optic2000.com") == "https://optic2000.com"
        assert _normalize_url("http://https://example.com") == "https://example.com"
        assert _normalize_url("https://http://example.com") == "http://example.com"
        assert _normalize_url("http://http://example.com") == "http://example.com"

    def test_fixes_truncated_scheme(self):
        assert _normalize_url("ttps://bankonbuffalo.bank") == "https://bankonbuffalo.bank"
        assert _normalize_url("ttp://example.com") == "https://example.com"
        assert _normalize_url("htp://example.com") == "https://example.com"
        assert _normalize_url("htps://example.com") == "https://example.com"

    def test_fixes_single_slash_scheme(self):
        assert _normalize_url("https:/inatbar.rs") == "https://inatbar.rs"
        assert _normalize_url("http:/example.com") == "http://example.com"
        assert _normalize_url("HTTPS:/example.com/path") == "https://example.com/path"

    def test_fixes_missing_slashes(self):
        """Scheme with colon but no slashes."""
        assert _normalize_url("https:example.com") == "https://example.com"
        assert _normalize_url("http:example.com") == "http://example.com"

    def test_fixes_backslashes(self):
        """Backslashes instead of forward slashes."""
        assert _normalize_url("https:\\\\example.com") == "https://example.com"
        assert _normalize_url("http:\\example.com") == "http://example.com"

    def test_fixes_semicolon_scheme(self):
        """Semicolon instead of colon."""
        assert _normalize_url("https;//example.com") == "https://example.com"
        assert _normalize_url("http;//example.com") == "http://example.com"

    def test_fixes_extra_slashes(self):
        """Triple slash after scheme."""
        assert _normalize_url("https:///example.com") == "https://example.com"
        assert _normalize_url("http:///example.com") == "http://example.com"

    def test_fixes_space_after_scheme(self):
        """Space between scheme separator and domain."""
        assert _normalize_url("https:// example.com") == "https://example.com"
        assert _normalize_url("http:// example.com") == "http://example.com"

    def test_fixes_doubled_leading_letter(self):
        """Extra h at the start."""
        assert _normalize_url("hhttps://example.com") == "https://example.com"
        assert _normalize_url("hhttp://example.com") == "http://example.com"

    def test_fixes_extra_s(self):
        """httpss:// typo."""
        assert _normalize_url("httpss://example.com") == "https://example.com"

    def test_fixes_missing_colon_with_slashes(self):
        """Scheme slashes but no colon."""
        assert _normalize_url("https///example.com") == "https://example.com"

    def test_fixes_capitalized_scheme(self):
        assert _normalize_url("Http://www.heyhorst.de") == "http://www.heyhorst.de"
        assert _normalize_url("HTTP://example.com") == "http://example.com"
        assert _normalize_url("Https://example.com") == "https://example.com"

    def test_fixes_bare_scheme_separator(self):
        """URLs starting with :// or // should get https:// prefix."""
        assert _normalize_url("://www.theatrecreanova.be") == "https://www.theatrecreanova.be"
        assert _normalize_url("//sites.google.com/view/cooper-bar/cafe") == "https://sites.google.com/view/cooper-bar/cafe"

    def test_strips_gclid_and_gad_params(self):
        url = "https://www.example.com/page?gclid=abc&gad_source=1&gad_campaignid=123&gclsrc=aw.ds"
        assert _normalize_url(url) == "https://www.example.com/page"

    def test_strips_campaignid_and_otppartnerid(self):
        url = "https://www.example.com/page?otppartnerid=9308&campaignid=pw_123"
        assert _normalize_url(url) == "https://www.example.com/page"

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

    def test_preserves_fragment(self):
        assert _normalize_url("https://online.fliphtml5.com/odpet/xrkb/#p=1") == "https://online.fliphtml5.com/odpet/xrkb/#p=1"


class TestIsTrivialUrlChange:
    def test_trailing_slash_only(self):
        assert _is_trivial_url_change("https://example.com/", "https://example.com") is True

    def test_slash_before_query_params(self):
        assert _is_trivial_url_change(
            "https://oudgeervliet.nl/?page_id=212",
            "https://oudgeervliet.nl?page_id=212",
        ) is True

    def test_real_path_difference_is_not_trivial(self):
        assert _is_trivial_url_change(
            "https://example.com/old",
            "https://example.com/new",
        ) is False

    def test_query_param_difference_is_not_trivial(self):
        assert _is_trivial_url_change(
            "https://example.com/?a=1",
            "https://example.com/?b=2",
        ) is False


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


class TestTryExpandShortener:
    def test_expands_known_shortener(self):
        mock_resp = MagicMock(status_code=200, url="https://example.com/real-page")
        with patch("checkers.website.requests.head", return_value=mock_resp):
            result = _try_expand_shortener("https://bit.ly/abc123")
            assert result == "https://example.com/real-page"

    def test_ignores_non_shortener(self):
        result = _try_expand_shortener("https://example.com/page")
        assert result == "https://example.com/page"

    def test_returns_original_on_error(self):
        with patch("checkers.website.requests.head", side_effect=Exception("timeout")):
            result = _try_expand_shortener("https://bit.ly/abc123")
            assert result == "https://bit.ly/abc123"

    def test_returns_original_on_404(self):
        mock_resp = MagicMock(status_code=404, url="https://bit.ly/abc123")
        with patch("checkers.website.requests.head", return_value=mock_resp):
            result = _try_expand_shortener("https://bit.ly/abc123")
            assert result == "https://bit.ly/abc123"


class TestWebsiteChecker:
    def setup_method(self):
        self.checker = WebsiteChecker()

    def test_ignores_correct_url(self):
        action = _make_action({"website": "https://example.com"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u), \
             patch("checkers.website._try_expand_shortener", side_effect=lambda u: u):
            assert self.checker.check(action) == []

    def test_formats_bare_domain(self):
        action = _make_action({"website": "example.com"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u), \
             patch("checkers.website._try_expand_shortener", side_effect=lambda u: u):
            issues = self.checker.check(action)
            assert len(issues) == 1
            assert issues[0].tags_after["website"] == "https://example.com"

    def test_strips_tracking_params(self):
        action = _make_action({"website": "https://example.com?utm_source=x"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u), \
             patch("checkers.website._try_expand_shortener", side_effect=lambda u: u):
            issues = self.checker.check(action)
            assert len(issues) == 1
            assert "utm_source" not in issues[0].tags_after["website"]

    def test_checks_contact_website(self):
        action = _make_action({"contact:website": "example.com"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u), \
             patch("checkers.website._try_expand_shortener", side_effect=lambda u: u):
            issues = self.checker.check(action)
            assert len(issues) == 1
            assert "contact:website" in issues[0].tags_after

    def test_checks_url_tag(self):
        action = _make_action({"url": "example.com"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u), \
             patch("checkers.website._try_expand_shortener", side_effect=lambda u: u):
            issues = self.checker.check(action)
            assert len(issues) == 1

    def test_skips_trailing_slash_only(self):
        """Removing trailing slash alone is too minor."""
        action = _make_action({"website": "https://www.qmpizza.com/"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u), \
             patch("checkers.website._try_expand_shortener", side_effect=lambda u: u):
            assert self.checker.check(action) == []

    def test_keeps_http_to_https_upgrade(self):
        """HTTP→HTTPS is significant even if it's a small text change."""
        action = _make_action({"website": "http://example.com"})
        with patch("checkers.website._try_https_upgrade", return_value="https://example.com"), \
             patch("checkers.website._try_expand_shortener", side_effect=lambda u: u):
            issues = self.checker.check(action)
            assert len(issues) == 1
            assert issues[0].tags_after["website"] == "https://example.com"

    def test_ignores_delete_actions(self):
        action = _make_action({"website": "example.com"}, action_type="delete")
        assert self.checker.check(action) == []

    def test_ignores_non_website_tags(self):
        action = _make_action({"name": "Test", "phone": "+1234"})
        assert self.checker.check(action) == []

    def test_flags_google_copy_gmb(self):
        action = _make_action({"website": "https://example.com?utm_source=gmb&utm_medium=organic"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u), \
             patch("checkers.website._try_expand_shortener", side_effect=lambda u: u):
            issues = self.checker.check(action)
            assert len(issues) == 1
            assert issues[0].extra.get("google_copy") is True

    def test_flags_google_copy_yxt_goog(self):
        action = _make_action({"website": "https://example.com?utm_source=yxt-goog&utm_medium=local"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u), \
             patch("checkers.website._try_expand_shortener", side_effect=lambda u: u):
            issues = self.checker.check(action)
            assert len(issues) == 1
            assert issues[0].extra.get("google_copy") is True

    def test_no_google_copy_flag_for_normal_utm(self):
        action = _make_action({"website": "https://example.com?utm_source=twitter"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u), \
             patch("checkers.website._try_expand_shortener", side_effect=lambda u: u):
            issues = self.checker.check(action)
            assert len(issues) == 1
            assert issues[0].extra.get("google_copy") is None

    def test_issue_fields(self):
        action = _make_action({"website": "example.com"})
        with patch("checkers.website._try_https_upgrade", side_effect=lambda u: u), \
             patch("checkers.website._try_expand_shortener", side_effect=lambda u: u):
            issues = self.checker.check(action)
            issue = issues[0]
            assert issue.check_name == "website_cleanup"
            assert issue.element_type == "node"
            assert issue.element_id == "123"
            assert issue.changeset == "999"
            assert issue.tags_before == {"website": "example.com"}
