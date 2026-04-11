"""Tests for tag key typo checker."""

import pytest
from checkers import Action
from checkers.tag_typo import TagTypoChecker


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


class TestTagTypoChecker:
    def setup_method(self):
        self.checker = TagTypoChecker()

    # --- Misspelling detection ---

    def test_detects_misspelled_key(self):
        action = _make_action({"kayer": "5"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].check_name == "tag_typo"
        assert issues[0].tags_before == {"kayer": "5"}
        assert issues[0].tags_after == {"layer": "5"}
        assert issues[0].extra == {"old_key": "kayer", "new_key": "layer"}

    def test_detects_building_typo(self):
        action = _make_action({"biulding": "yes"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].tags_after == {"building": "yes"}

    def test_detects_highway_typo(self):
        action = _make_action({"hihgway": "residential"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].tags_after == {"highway": "residential"}

    def test_detects_amenity_typo(self):
        action = _make_action({"ameniy": "restaurant"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].tags_after == {"amenity": "restaurant"}

    # --- Capitalization detection ---

    def test_fixes_capitalized_key(self):
        action = _make_action({"Description": "A nice place"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].tags_before == {"Description": "A nice place"}
        assert issues[0].tags_after == {"description": "A nice place"}

    def test_fixes_all_caps_key(self):
        action = _make_action({"BUILDING": "yes"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].tags_after == {"building": "yes"}

    def test_fixes_spaces_to_underscores(self):
        action = _make_action({"Opening Hours": "Mo-Fr 09:00-17:00"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].tags_after == {"opening_hours": "Mo-Fr 09:00-17:00"}

    def test_fixes_mixed_case_with_spaces(self):
        action = _make_action({"opening Hours": "24/7"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].tags_after == {"opening_hours": "24/7"}

    # --- No false positives ---

    def test_ignores_correct_key(self):
        action = _make_action({"building": "yes"})
        assert self.checker.check(action) == []

    def test_ignores_unknown_key(self):
        action = _make_action({"some_custom_tag": "value"})
        assert self.checker.check(action) == []

    def test_ignores_unchanged_tag(self):
        """Tags that weren't modified in this edit should be skipped."""
        action = _make_action(
            tags_new={"kayer": "5", "name": "Test"},
            tags_old={"kayer": "5"},
        )
        issues = self.checker.check(action)
        assert len(issues) == 0

    def test_ignores_delete_action(self):
        action = _make_action({"kayer": "5"}, action_type="delete")
        assert self.checker.check(action) == []

    def test_skips_if_correct_key_already_exists(self):
        """Don't rename kayer->layer if layer already exists on the element."""
        action = _make_action({"kayer": "5", "layer": "3"})
        issues = self.checker.check(action)
        # Should not suggest renaming kayer since layer already present
        assert not any(
            i.extra.get("new_key") == "layer" for i in issues
        )

    def test_skips_capitalization_if_correct_key_exists(self):
        """Don't rename BUILDING->building if building already exists."""
        action = _make_action({"BUILDING": "yes", "building": "house"})
        issues = self.checker.check(action)
        assert not any(
            i.extra.get("new_key") == "building" for i in issues
        )

    # --- Summary format ---

    def test_summary_format(self):
        action = _make_action({"kayer": "5"})
        issues = self.checker.check(action)
        assert issues[0].summary == "kayer=5 → layer=5"

    # --- Multiple issues ---

    def test_multiple_typos_in_one_action(self):
        action = _make_action({"kayer": "5", "biulding": "yes"})
        issues = self.checker.check(action)
        assert len(issues) == 2
        keys = {i.extra["new_key"] for i in issues}
        assert keys == {"layer", "building"}

    # --- Misspelling takes precedence over capitalization ---

    def test_misspelling_dict_checked_first(self):
        """If a key is in the misspellings dict, use that correction."""
        action = _make_action({"webiste": "http://example.com"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].extra["new_key"] == "website"
