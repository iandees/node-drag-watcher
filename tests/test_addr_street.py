"""Tests for addr:street abbreviation expansion checker."""

import pytest
from checkers import Action
from checkers.addr_street import AddrStreetChecker, _expand_street


def _make_action(tags_new, tags_old=None, action_type="create"):
    return Action(
        action_type=action_type,
        element_type="node",
        element_id="13728646301",
        version="1",
        changeset="999",
        user="testuser",
        tags_old=tags_old or {},
        tags_new=tags_new,
    )


class TestExpandStreet:
    # --- Suffix expansions ---

    def test_expand_st_as_suffix(self):
        assert _expand_street("Tyler ST") == "Tyler Street"

    def test_expand_ave(self):
        assert _expand_street("5th AVE") == "5th Avenue"

    def test_expand_blvd(self):
        assert _expand_street("Sunset BLVD") == "Sunset Boulevard"

    def test_expand_dr(self):
        assert _expand_street("Oak DR") == "Oak Drive"

    def test_expand_ln(self):
        assert _expand_street("Elm LN") == "Elm Lane"

    def test_expand_rd(self):
        assert _expand_street("Country RD") == "Country Road"

    def test_expand_ct(self):
        assert _expand_street("Rose CT") == "Rose Court"

    def test_expand_pkwy(self):
        assert _expand_street("Garden PKWY") == "Garden Parkway"

    # --- ST as first word is NOT expanded (could be Saint) ---

    def test_st_first_word_not_expanded(self):
        assert _expand_street("St Louis Road") is None

    def test_st_suffix_with_direction(self):
        assert _expand_street("Tyler ST NE") == "Tyler Street Northeast"

    # --- Compound directions (always expand) ---

    def test_expand_trailing_ne(self):
        assert _expand_street("Main Street NE") == "Main Street Northeast"

    def test_expand_trailing_sw(self):
        assert _expand_street("1st Avenue SW") == "1st Avenue Southwest"

    def test_expand_leading_nw(self):
        assert _expand_street("NW Park Avenue") == "Northwest Park Avenue"

    def test_expand_compound_direction_middle(self):
        assert _expand_street("Old NE Highway") == "Old Northeast Highway"

    # --- Single-letter directions (only first or last position) ---

    def test_expand_leading_n(self):
        assert _expand_street("N Main Street") == "North Main Street"

    def test_expand_trailing_w(self):
        assert _expand_street("Main Street W") == "Main Street West"

    def test_single_direction_middle_not_expanded(self):
        """Single-letter direction in the middle is NOT expanded."""
        assert _expand_street("Martin N King Boulevard") is None

    # --- Multiple expansions ---

    def test_expand_suffix_and_direction(self):
        assert _expand_street("Tyler ST NE") == "Tyler Street Northeast"

    def test_expand_direction_and_suffix(self):
        assert _expand_street("N Main ST") == "North Main Street"

    # --- No expansion needed ---

    def test_already_expanded(self):
        assert _expand_street("Tyler Street Northeast") is None

    def test_single_word_no_expansion(self):
        assert _expand_street("Broadway") is None

    def test_no_abbreviations_present(self):
        assert _expand_street("Main Street") is None

    # --- Case handling ---

    def test_lowercase_abbreviation(self):
        assert _expand_street("Tyler st") == "Tyler Street"

    def test_mixed_case(self):
        assert _expand_street("Tyler St Ne") == "Tyler Street Northeast"


class TestAddrStreetChecker:
    def setup_method(self):
        self.checker = AddrStreetChecker()

    def test_detects_abbreviation(self):
        action = _make_action({"addr:street": "Tyler ST NE", "shop": "convenience"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].check_name == "addr_street_abbrev"
        assert issues[0].tags_before == {"addr:street": "Tyler ST NE"}
        assert issues[0].tags_after == {"addr:street": "Tyler Street Northeast"}
        assert issues[0].summary == "Expand abbreviated street name"

    def test_no_issue_when_already_expanded(self):
        action = _make_action({"addr:street": "Tyler Street Northeast"})
        issues = self.checker.check(action)
        assert len(issues) == 0

    def test_skips_unchanged_tag(self):
        action = _make_action(
            {"addr:street": "Tyler ST NE"},
            tags_old={"addr:street": "Tyler ST NE"},
        )
        issues = self.checker.check(action)
        assert len(issues) == 0

    def test_skips_delete_action(self):
        action = _make_action(
            {"addr:street": "Tyler ST NE"},
            action_type="delete",
        )
        issues = self.checker.check(action)
        assert len(issues) == 0

    def test_skips_no_addr_street(self):
        action = _make_action({"shop": "convenience"})
        issues = self.checker.check(action)
        assert len(issues) == 0

    def test_skips_single_word_street(self):
        action = _make_action({"addr:street": "Broadway"})
        issues = self.checker.check(action)
        assert len(issues) == 0

    def test_st_first_word_not_flagged(self):
        """St as first word (Saint) should not be expanded."""
        action = _make_action({"addr:street": "St Louis Road"})
        issues = self.checker.check(action)
        assert len(issues) == 0

    def test_issue_fields(self):
        action = _make_action({"addr:street": "Oak DR"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.element_type == "node"
        assert issue.element_id == "13728646301"
        assert issue.element_version == "1"
        assert issue.changeset == "999"
        assert issue.user == "testuser"
