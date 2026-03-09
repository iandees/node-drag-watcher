"""Tests for phone number formatting checker."""

import pytest
from checkers import Action, Issue
from checkers.phone import PhoneChecker

# Tags to check
PHONE_TAGS = ["phone", "contact:phone", "fax", "contact:fax"]


def _make_action(tags_new, tags_old=None, action_type="create",
                 coords_new=(40.7128, -74.0060), **kwargs):
    return Action(
        action_type=action_type,
        element_type="node",
        element_id="123",
        version="1",
        changeset="999",
        user="testuser",
        tags_old=tags_old or {},
        tags_new=tags_new,
        coords_new=coords_new,
        **kwargs,
    )


class TestPhoneChecker:
    def setup_method(self):
        self.checker = PhoneChecker()

    def test_ignores_correctly_formatted(self):
        action = _make_action({"phone": "+1 212-555-1234"})
        assert self.checker.check(action) == []

    def test_formats_local_number_with_coords(self):
        """Local number (no country code) gets formatted with inferred country."""
        action = _make_action({"phone": "2125551234"}, coords_new=(40.7, -74.0))
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].check_name == "phone_format"
        assert issues[0].tags_after["phone"] == "+1 212-555-1234"

    def test_skips_number_with_country_code(self):
        """Number already has country code — just reformatting, skip it."""
        action = _make_action({"phone": "+12125551234"})
        assert self.checker.check(action) == []

    def test_skips_already_parseable_international(self):
        """Numbers with valid country codes are skipped even if spacing differs."""
        for number in ["+33670243409", "+7 985 1712209", "+39 3209229638"]:
            action = _make_action({"phone": number})
            assert self.checker.check(action) == [], f"Should skip {number}"

    def test_handles_semicolon_with_local_numbers(self):
        action = _make_action({"phone": "2125551234;2125556789"}, coords_new=(40.7, -74.0))
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert ";" in issues[0].tags_after["phone"]

    def test_skips_semicolon_with_country_codes(self):
        action = _make_action({"phone": "+12125551234;+12125556789"})
        assert self.checker.check(action) == []

    def test_skips_unparseable(self):
        action = _make_action({"phone": "not a number"})
        assert self.checker.check(action) == []

    def test_checks_contact_phone(self):
        action = _make_action({"contact:phone": "2125551234"}, coords_new=(40.7, -74.0))
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert "contact:phone" in issues[0].tags_after

    def test_checks_fax(self):
        action = _make_action({"fax": "2125551234"}, coords_new=(40.7, -74.0))
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert "fax" in issues[0].tags_after

    def test_ignores_delete_actions(self):
        action = _make_action({"phone": "2125551234"}, action_type="delete")
        assert self.checker.check(action) == []

    def test_ignores_non_phone_tags(self):
        action = _make_action({"name": "Test Place", "highway": "residential"})
        assert self.checker.check(action) == []

    def test_no_coords_skips_local_number(self):
        """Without coords, can't add country code to local number."""
        action = _make_action({"phone": "2125551234"}, coords_new=None)
        assert self.checker.check(action) == []

    def test_no_coords_skips_international(self):
        """Number with country code is skipped regardless of coords."""
        action = _make_action({"phone": "+12125551234"}, coords_new=None)
        assert self.checker.check(action) == []

    def test_multiple_local_phone_tags(self):
        """Multiple local phone tags on same element."""
        action = _make_action({
            "phone": "2125551234",
            "fax": "2125556789",
        }, coords_new=(40.7, -74.0))
        issues = self.checker.check(action)
        assert len(issues) == 2

    def test_issue_fields(self):
        action = _make_action({"phone": "2125551234"}, coords_new=(40.7, -74.0))
        issues = self.checker.check(action)
        issue = issues[0]
        assert issue.element_type == "node"
        assert issue.element_id == "123"
        assert issue.changeset == "999"
        assert issue.user == "testuser"
        assert issue.tags_before == {"phone": "2125551234"}
        assert issue.summary  # non-empty
