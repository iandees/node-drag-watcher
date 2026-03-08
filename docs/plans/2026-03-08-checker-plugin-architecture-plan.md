# Checker Plugin Architecture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor the monolithic watcher.py into a plugin architecture with checkers (detect issues), notifiers (present to humans), and fixers (apply corrections), then add phone number formatting and website URL cleanup checkers.

**Architecture:** Three layers — checkers detect issues from adiff Actions, notifiers present issues and collect approval, fixers apply corrections to OSM. The adiff parser yields Action objects that all checkers consume. The orchestrator (watcher.py) wires everything together.

**Tech Stack:** Python 3.12, phonenumbers library (new), requests, slack-bolt, Pillow, pytest

---

### Task 1: Create package structure and base abstractions

**Files:**
- Create: `checkers/__init__.py`
- Create: `notifiers/__init__.py`

**Step 1: Create checkers package with Action, Issue, BaseChecker**

Create `checkers/__init__.py`:

```python
"""Checker plugin framework for detecting OSM data issues."""

import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Action:
    """A single element action from an augmented diff."""
    action_type: str           # "create", "modify", "delete"
    element_type: str          # "node", "way", "relation"
    element_id: str
    version: str
    changeset: str
    user: str
    tags_old: dict[str, str] = field(default_factory=dict)
    tags_new: dict[str, str] = field(default_factory=dict)
    # Node geometry
    coords_old: tuple[float, float] | None = None
    coords_new: tuple[float, float] | None = None
    # Way geometry
    nd_refs_old: list[str] | None = None
    nd_refs_new: list[str] | None = None
    node_coords_old: dict[str, tuple[float, float]] | None = None
    node_coords_new: dict[str, tuple[float, float]] | None = None


@dataclass
class Issue:
    """A detected issue with an OSM element."""
    element_type: str
    element_id: str
    element_version: str
    changeset: str
    user: str
    check_name: str
    summary: str
    tags_before: dict[str, str] = field(default_factory=dict)
    tags_after: dict[str, str] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


class BaseChecker(ABC):
    """Base class for all checkers."""

    @abstractmethod
    def check(self, action: Action) -> list[Issue]:
        """Check a single action for issues. Returns list of Issues found."""
        ...
```

**Step 2: Create notifiers package with BaseNotifier**

Create `notifiers/__init__.py`:

```python
"""Notifier framework for presenting issues to humans."""

from abc import ABC, abstractmethod
from checkers import Issue


class BaseNotifier(ABC):
    """Base class for notification channels."""

    @abstractmethod
    def notify(self, issues: list[Issue]) -> None:
        """Send notifications for detected issues."""
        ...

    @abstractmethod
    def listen(self) -> None:
        """Start listening for user responses (button clicks, replies, etc.)."""
        ...
```

**Step 3: Run tests to make sure nothing is broken**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All existing tests pass (new files don't affect anything yet)

**Step 4: Commit**

```bash
git add checkers/__init__.py notifiers/__init__.py
git commit -m "Add checker and notifier base abstractions"
```

---

### Task 2: Extract drag detection into checkers/drag.py

Move `_check_way_for_drag`, `haversine_distance`, `angle_at_node`, `_get_way_membership_changes`, `detect_node_drags`, `_detect_node_drags_tree`, `_detect_node_drags_file`, `_attach_way_membership_changes`, and `filter_drags` from `watcher.py` into `checkers/drag.py`. **Keep them as module-level functions for now** — the DragChecker class will wrap them but the core logic stays the same.

**Files:**
- Create: `checkers/drag.py`
- Modify: `watcher.py` — replace functions with imports from `checkers.drag`

**Step 1: Create checkers/drag.py**

Copy the following functions from `watcher.py` (lines 36-61, 63-67, 70-221, 224-357, 897-946) into `checkers/drag.py`:
- `haversine_distance` (lines 36-43)
- `angle_at_node` (lines 46-60)
- `_get_way_membership_changes` (lines 63-67)
- `_check_way_for_drag` (lines 70-221)
- `detect_node_drags` (lines 224-232)
- `_attach_way_membership_changes` (lines 235-244)
- `_detect_node_drags_tree` (lines 247-291)
- `_detect_node_drags_file` (lines 294-357)
- `filter_drags` (lines 897-946)

Add required imports at top:

```python
"""Node drag detection checker."""

import logging
import math
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)
```

Do NOT add a DragChecker class yet — that comes in a later task after the adiff parser is generalized.

**Step 2: Update watcher.py to import from checkers.drag**

Replace the moved functions with imports. At the top of `watcher.py`, add:

```python
from checkers.drag import (
    haversine_distance,
    angle_at_node,
    detect_node_drags,
    filter_drags,
)
```

Remove the original function definitions (lines 36-61, 63-67, 70-221, 224-357, 897-946) from `watcher.py`.

**Step 3: Run tests**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests pass — same functions, different file

**Step 4: Commit**

```bash
git add checkers/drag.py watcher.py
git commit -m "Extract drag detection into checkers/drag.py"
```

---

### Task 3: Extract image generation into checkers/drag.py

Move the drag image generation functions from `watcher.py` into `checkers/drag.py` since they're drag-specific.

**Files:**
- Modify: `checkers/drag.py` — add image generation functions
- Modify: `watcher.py` — import from checkers.drag

**Step 1: Move image functions to checkers/drag.py**

Move these functions from `watcher.py` to `checkers/drag.py`:
- `_lon_to_tile_x` (line 360)
- `_lat_to_tile_y` (line 365)
- `_latlon_to_pixel` (line 371)
- `_choose_zoom` (line 378)
- `generate_drag_image` (lines 388-505)

Add to `checkers/drag.py` imports:

```python
import io
import requests
from PIL import Image, ImageDraw
```

**Step 2: Update watcher.py imports**

Add `generate_drag_image` to the import from `checkers.drag`. Remove the moved functions from `watcher.py`.

**Step 3: Run tests**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests pass

**Step 4: Commit**

```bash
git add checkers/drag.py watcher.py
git commit -m "Move drag image generation to checkers/drag.py"
```

---

### Task 4: Extract Slack notification into notifiers/slack.py

Move all Slack-specific code from `watcher.py` into `notifiers/slack.py`.

**Files:**
- Create: `notifiers/slack.py`
- Modify: `watcher.py` — import from notifiers.slack

**Step 1: Create notifiers/slack.py**

Move these functions from `watcher.py`:
- `upload_slack_image` (lines 508-551)
- `_format_drag_text` (lines 554-583)
- `build_drag_blocks` (lines 586-642)
- `_upload_node_images` (lines 645-660)
- `_post_reverter_link` (lines 663-698)
- `_post_slack_message` (lines 701-723)
- `send_slack_interactive` (lines 726-737)
- `handle_revert_action` (lines 740-803)
- `_update_message_error` (lines 806-819)
- `start_socket_mode` (lines 822-835)
- `send_slack_summary` (lines 838-853)

Add imports:

```python
"""Slack notification for OSM watcher issues."""

import json
import logging
import urllib.parse
from collections.abc import Callable

import requests

import revert as revert_mod
from checkers.drag import generate_drag_image

log = logging.getLogger(__name__)
```

**Step 2: Update watcher.py**

Replace moved functions with imports:

```python
from notifiers.slack import (
    send_slack_summary,
    start_socket_mode,
    build_drag_blocks,
)
```

Remove the moved functions and their now-unused imports from `watcher.py`.

**Step 3: Run tests**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests pass. Some test files patch `watcher.xxx` — these need updating to patch `notifiers.slack.xxx` instead.

**Step 4: Fix test imports**

Update `tests/test_blocks.py`: change `from watcher import build_drag_blocks` to `from notifiers.slack import build_drag_blocks`.

Update `tests/test_slack.py`: change `from watcher import send_slack_summary, send_slack_interactive` to `from notifiers.slack import send_slack_summary, send_slack_interactive`. Update mock patches from `watcher.requests.post` to `notifiers.slack.requests.post`, and `watcher.send_slack_interactive` to `notifiers.slack.send_slack_interactive`, and `watcher.generate_drag_image` to `notifiers.slack.generate_drag_image`.

**Step 5: Run tests again**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests pass

**Step 6: Commit**

```bash
git add notifiers/slack.py watcher.py tests/test_blocks.py tests/test_slack.py
git commit -m "Extract Slack notification into notifiers/slack.py"
```

---

### Task 5: Add phonenumbers dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add phonenumbers to dependencies**

Run: `uv add phonenumbers`

This updates `pyproject.toml` and `uv.lock`.

**Step 2: Verify install**

Run: `uv run python -c "import phonenumbers; print(phonenumbers.__version__)"`
Expected: prints version number

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "Add phonenumbers dependency"
```

---

### Task 6: Create phone number checker with tests

**Files:**
- Create: `checkers/phone.py`
- Create: `tests/test_phone.py`

**Step 1: Write failing tests**

Create `tests/test_phone.py`:

```python
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

    def test_formats_us_number(self):
        action = _make_action({"phone": "2125551234"}, coords_new=(40.7, -74.0))
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].check_name == "phone_format"
        assert issues[0].tags_after["phone"] == "+1 212-555-1234"

    def test_formats_us_number_with_country_code(self):
        action = _make_action({"phone": "+12125551234"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].tags_after["phone"] == "+1 212-555-1234"

    def test_formats_uk_number(self):
        action = _make_action({"phone": "+442071234567"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert issues[0].tags_after["phone"] == "+44 20 7123 4567"

    def test_handles_semicolon_separated(self):
        action = _make_action({"phone": "+12125551234;+12125556789"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert ";" in issues[0].tags_after["phone"]

    def test_skips_unparseable(self):
        action = _make_action({"phone": "not a number"})
        assert self.checker.check(action) == []

    def test_checks_contact_phone(self):
        action = _make_action({"contact:phone": "+12125551234"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert "contact:phone" in issues[0].tags_after

    def test_checks_fax(self):
        action = _make_action({"fax": "+12125551234"})
        issues = self.checker.check(action)
        assert len(issues) == 1
        assert "fax" in issues[0].tags_after

    def test_ignores_delete_actions(self):
        action = _make_action({"phone": "2125551234"}, action_type="delete")
        assert self.checker.check(action) == []

    def test_ignores_non_phone_tags(self):
        action = _make_action({"name": "Test Place", "highway": "residential"})
        assert self.checker.check(action) == []

    def test_no_coords_tries_international_only(self):
        """Without coords, can only parse numbers with + prefix."""
        action = _make_action({"phone": "2125551234"}, coords_new=None)
        assert self.checker.check(action) == []

    def test_no_coords_parses_international(self):
        action = _make_action({"phone": "+12125551234"}, coords_new=None)
        issues = self.checker.check(action)
        assert len(issues) == 1

    def test_multiple_phone_tags(self):
        """Multiple phone tags on same element."""
        action = _make_action({
            "phone": "+12125551234",
            "fax": "+12125556789",
        })
        issues = self.checker.check(action)
        assert len(issues) == 2

    def test_issue_fields(self):
        action = _make_action({"phone": "+12125551234"})
        issues = self.checker.check(action)
        issue = issues[0]
        assert issue.element_type == "node"
        assert issue.element_id == "123"
        assert issue.changeset == "999"
        assert issue.user == "testuser"
        assert issue.tags_before == {"phone": "+12125551234"}
        assert issue.summary  # non-empty
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_phone.py -v --tb=short`
Expected: ImportError — `checkers.phone` doesn't exist yet

**Step 3: Implement checkers/phone.py**

```python
"""Phone number formatting checker.

Detects phone/fax tags that don't follow international format
and suggests the correctly formatted version.
"""

import logging
import re

import phonenumbers

from checkers import Action, Issue, BaseChecker

log = logging.getLogger(__name__)

# Tags that contain phone numbers
PHONE_TAG_PATTERN = re.compile(
    r'^(phone|fax|contact:phone|contact:fax)(:.+)?$'
)


def _infer_country_code(coords: tuple[float, float] | None) -> str | None:
    """Infer ISO country code from coordinates using phonenumbers geocoding."""
    if coords is None:
        return None
    try:
        from phonenumbers import geocoder
        # phonenumbers doesn't do reverse geocoding from coords,
        # so we use a simple lat/lon → country lookup
        # For now, use a basic approach based on common regions
        lat, lon = coords
        # This is a simplified mapping; for production we'd use
        # a proper reverse geocoder, but phonenumbers.parse with
        # region hint is good enough for most cases
        return _coords_to_country(lat, lon)
    except Exception:
        return None


def _coords_to_country(lat: float, lon: float) -> str | None:
    """Very rough lat/lon to country code mapping for phone number parsing.

    Covers major regions. Returns None for ambiguous areas.
    """
    # North America
    if 24 < lat < 50 and -130 < lon < -60:
        return "US"
    # UK/Ireland
    if 49 < lat < 61 and -11 < lon < 2:
        return "GB"
    # Western Europe
    if 42 < lat < 55 and 2 < lon < 15:
        return "DE"
    # Scandinavia
    if 55 < lat < 72 and 4 < lon < 32:
        return "SE"
    # France/Spain/Portugal
    if 36 < lat < 49 and -10 < lon < 4:
        return "FR"
    # Italy
    if 36 < lat < 47 and 6 < lon < 19:
        return "IT"
    # Australia
    if -45 < lat < -10 and 110 < lon < 155:
        return "AU"
    # Japan
    if 24 < lat < 46 and 123 < lon < 146:
        return "JP"
    # Brazil
    if -34 < lat < 6 and -74 < lon < -34:
        return "BR"
    return None


def _format_phone(raw: str, country: str | None) -> str | None:
    """Try to parse and format a single phone number.

    Returns formatted number or None if unparseable.
    """
    try:
        parsed = phonenumbers.parse(raw, None)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
            )
    except phonenumbers.NumberParseException:
        pass

    # Retry with country hint if available
    if country:
        try:
            parsed = phonenumbers.parse(raw, country)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
                )
        except phonenumbers.NumberParseException:
            pass

    return None


def _format_phone_value(value: str, country: str | None) -> str | None:
    """Format a phone tag value, handling semicolon-separated numbers.

    Returns formatted value or None if nothing changed.
    """
    parts = [p.strip() for p in value.split(";")]
    formatted_parts = []
    any_changed = False

    for part in parts:
        if not part:
            continue
        formatted = _format_phone(part, country)
        if formatted is None:
            # Can't parse this part, keep original
            formatted_parts.append(part)
        else:
            formatted_parts.append(formatted)
            if formatted != part:
                any_changed = True

    if not any_changed:
        return None

    return ";".join(formatted_parts)


class PhoneChecker(BaseChecker):
    """Detect phone/fax tags that aren't in international format."""

    def check(self, action: Action) -> list[Issue]:
        if action.action_type == "delete":
            return []

        country = _infer_country_code(action.coords_new)
        issues = []

        for tag_key, tag_value in action.tags_new.items():
            if not PHONE_TAG_PATTERN.match(tag_key):
                continue

            formatted = _format_phone_value(tag_value, country)
            if formatted is None:
                continue

            issues.append(Issue(
                element_type=action.element_type,
                element_id=action.element_id,
                element_version=action.version,
                changeset=action.changeset,
                user=action.user,
                check_name="phone_format",
                summary=f"{tag_key}: {tag_value} → {formatted}",
                tags_before={tag_key: tag_value},
                tags_after={tag_key: formatted},
            ))

        return issues
```

**Step 4: Run tests**

Run: `uv run python -m pytest tests/test_phone.py -v --tb=short`
Expected: All tests pass. Some tests may need adjustment based on exact phonenumbers formatting output — adjust test expectations to match actual library output.

**Step 5: Run all tests**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests pass

**Step 6: Commit**

```bash
git add checkers/phone.py tests/test_phone.py
git commit -m "Add phone number formatting checker"
```

---

### Task 7: Create website cleanup checker with tests

**Files:**
- Create: `checkers/website.py`
- Create: `tests/test_website.py`

**Step 1: Write failing tests**

Create `tests/test_website.py`:

```python
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
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_website.py -v --tb=short`
Expected: ImportError

**Step 3: Implement checkers/website.py**

```python
"""Website URL cleanup checker.

Detects website/url tags that need normalization:
- Add scheme if missing
- Lowercase domain
- Strip tracking parameters (utm_*, fbclid, gclid, etc.)
- Upgrade HTTP to HTTPS if site supports it
"""

import logging
import re
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import requests

from checkers import Action, Issue, BaseChecker

log = logging.getLogger(__name__)

WEBSITE_TAG_PATTERN = re.compile(
    r'^(website|url|contact:website)(:.+)?$'
)

# Query params to strip
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref",
}


def _normalize_url(raw: str) -> str | None:
    """Normalize a URL structurally (no network).

    Returns normalized URL or None if not a valid website URL.
    """
    stripped = raw.strip()

    # Skip non-website schemes
    if stripped.startswith(("mailto:", "tel:", "ftp:")):
        return None

    # Add scheme if missing
    if not stripped.startswith(("http://", "https://")):
        stripped = "https://" + stripped

    parsed = urlparse(stripped)

    # Lowercase domain
    netloc = parsed.netloc.lower()

    # Strip tracking params
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {
            k: v for k, v in params.items()
            if k not in TRACKING_PARAMS and not k.startswith("utm_")
        }
        query = urlencode(filtered, doseq=True) if filtered else ""
    else:
        query = ""

    # Strip trailing slash on bare domain
    path = parsed.path
    if path == "/":
        path = ""

    result = urlunparse((parsed.scheme, netloc, path, parsed.params, query, ""))
    return result


def _try_https_upgrade(url: str) -> str:
    """Try upgrading HTTP to HTTPS. Returns the best URL."""
    if not url.startswith("http://"):
        return url

    https_url = "https://" + url[7:]
    try:
        resp = requests.head(https_url, timeout=5, allow_redirects=True,
                             headers={"User-Agent": "node-drag-watcher/0.1"})
        if resp.status_code < 400:
            # Check if redirected to same domain
            final_parsed = urlparse(resp.url)
            original_parsed = urlparse(https_url)
            orig_domain = original_parsed.netloc.lower().lstrip("www.")
            final_domain = final_parsed.netloc.lower().lstrip("www.")
            if orig_domain == final_domain:
                return resp.url
            # Cross-domain redirect — keep original with HTTPS
            return https_url
        return url
    except Exception:
        return url


class WebsiteChecker(BaseChecker):
    """Detect website/url tags that need cleanup."""

    def check(self, action: Action) -> list[Issue]:
        if action.action_type == "delete":
            return []

        issues = []

        for tag_key, tag_value in action.tags_new.items():
            if not WEBSITE_TAG_PATTERN.match(tag_key):
                continue

            normalized = _normalize_url(tag_value)
            if normalized is None:
                continue

            # Try HTTPS upgrade
            final_url = _try_https_upgrade(normalized)

            if final_url == tag_value:
                continue

            issues.append(Issue(
                element_type=action.element_type,
                element_id=action.element_id,
                element_version=action.version,
                changeset=action.changeset,
                user=action.user,
                check_name="website_cleanup",
                summary=f"{tag_key}: {tag_value} → {final_url}",
                tags_before={tag_key: tag_value},
                tags_after={tag_key: final_url},
            ))

        return issues
```

**Step 4: Run tests**

Run: `uv run python -m pytest tests/test_website.py -v --tb=short`
Expected: All tests pass

**Step 5: Run all tests**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests pass

**Step 6: Commit**

```bash
git add checkers/website.py tests/test_website.py
git commit -m "Add website URL cleanup checker"
```

---

### Task 8: Create tag_fix.py module with tests

**Files:**
- Create: `tag_fix.py`
- Create: `tests/test_tag_fix.py`

**Step 1: Write failing tests**

Create `tests/test_tag_fix.py`:

```python
"""Tests for tag fix module — all HTTP mocked."""

from unittest.mock import patch, MagicMock
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

        with patch("tag_fix.requests") as mock_req:
            mock_req.get = MagicMock(return_value=_ok(current_xml))
            mock_req.put = MagicMock(side_effect=[
                _ok("777"),         # changeset create
                _ok("6"),           # node update
                _ok(),              # changeset close
            ])

            cs_id = fix_tags("token", [issue])

        assert cs_id == "777"
        # Verify the update PUT has corrected tag
        update_call = mock_req.put.call_args_list[1]
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

        with patch("tag_fix.requests") as mock_req:
            mock_req.get = MagicMock(return_value=_ok(current_xml))
            mock_req.put = MagicMock(side_effect=[
                _ok("777"),
                _ok("4"),
                _ok(),
            ])

            cs_id = fix_tags("token", [issue])

        assert cs_id == "777"
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_tag_fix.py -v --tb=short`
Expected: ImportError

**Step 3: Implement tag_fix.py**

```python
"""Apply tag corrections to OSM elements.

Fetches current element, verifies version matches, updates tags.
Used by phone and website checkers.
"""

import logging
import xml.etree.ElementTree as ET

import requests

from checkers import Issue
from revert import (
    _osm_headers, _check_response, _xml_escape,
    create_changeset, close_changeset,
    DEFAULT_OSM_API_BASE,
)

log = logging.getLogger(__name__)

_READ_HEADERS = {"User-Agent": "node-drag-watcher/0.1"}


class TagFixError(Exception):
    ...


class VersionConflictError(TagFixError):
    """Element version changed since issue was detected."""


def _fetch_element(element_type: str, element_id: str,
                   api_base: str = DEFAULT_OSM_API_BASE) -> ET.Element:
    """Fetch current state of an element."""
    resp = requests.get(
        f"{api_base}/{element_type}/{element_id}",
        timeout=15,
        headers=_READ_HEADERS,
    )
    _check_response(resp, f"fetch {element_type} {element_id}")
    root = ET.fromstring(resp.text)
    return root.find(element_type)


def _build_element_xml(elem: ET.Element, cs_id: str,
                       tag_updates: dict[str, str]) -> str:
    """Build XML for updating an element with corrected tags.

    Preserves all existing tags, geometry, and nd refs,
    only replacing tags that are in tag_updates.
    """
    element_type = elem.tag
    element_id = elem.get("id")
    version = elem.get("version")

    # Build tags: merge updates into existing
    tags = {}
    for tag in elem.findall("tag"):
        tags[tag.get("k")] = tag.get("v")
    tags.update(tag_updates)

    tags_xml = "".join(
        f'<tag k="{_xml_escape(k)}" v="{_xml_escape(v)}"/>'
        for k, v in tags.items()
    )

    # Preserve nd refs for ways
    nds_xml = ""
    if element_type == "way":
        nds_xml = "".join(
            f'<nd ref="{nd.get("ref")}"/>'
            for nd in elem.findall("nd")
        )

    # Preserve members for relations
    members_xml = ""
    if element_type == "relation":
        for member in elem.findall("member"):
            members_xml += (
                f'<member type="{member.get("type")}" '
                f'ref="{member.get("ref")}" '
                f'role="{_xml_escape(member.get("role", ""))}"/>'
            )

    # Node-specific attributes
    attrs = f'id="{element_id}" version="{version}" changeset="{cs_id}"'
    if element_type == "node":
        attrs += f' lat="{elem.get("lat")}" lon="{elem.get("lon")}"'

    return (
        f'<osm><{element_type} {attrs}>'
        f'{nds_xml}{members_xml}{tags_xml}'
        f'</{element_type}></osm>'
    )


def fix_tags(
    osm_token: str,
    issues: list[Issue],
    api_base: str = DEFAULT_OSM_API_BASE,
) -> str:
    """Apply tag corrections from issues to OSM.

    All issues should be for the same changeset.
    Returns the new changeset ID.
    """
    # Verify versions and collect updates
    updates: list[tuple[Issue, ET.Element]] = []

    for issue in issues:
        elem = _fetch_element(issue.element_type, issue.element_id, api_base)
        current_version = elem.get("version")
        if current_version != issue.element_version:
            raise VersionConflictError(
                f"{issue.element_type} {issue.element_id}: "
                f"version {issue.element_version} → {current_version}"
            )
        updates.append((issue, elem))

    # Create changeset
    comment = f"Fix tag formatting ({issues[0].check_name})"
    cs_id = create_changeset(osm_token, comment, api_base=api_base)

    try:
        for issue, elem in updates:
            xml = _build_element_xml(elem, cs_id, issue.tags_after)
            resp = requests.put(
                f"{api_base}/{issue.element_type}/{issue.element_id}",
                data=xml,
                headers=_osm_headers(osm_token),
                timeout=15,
            )
            _check_response(resp, f"update {issue.element_type} {issue.element_id}")
    finally:
        close_changeset(osm_token, cs_id, api_base=api_base)

    return cs_id
```

**Step 4: Run tests**

Run: `uv run python -m pytest tests/test_tag_fix.py -v --tb=short`
Expected: All tests pass

**Step 5: Run all tests**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests pass

**Step 6: Commit**

```bash
git add tag_fix.py tests/test_tag_fix.py
git commit -m "Add tag_fix module for applying tag corrections"
```

---

### Task 9: Generalize adiff parser to yield Actions

Refactor the adiff parsing in `watcher.py` to yield `Action` objects that all checkers can consume, instead of being drag-specific.

**Files:**
- Modify: `watcher.py` — new `parse_adiff_actions()` function
- Create: `tests/test_parser.py`

**Step 1: Write tests for the new parser**

Create `tests/test_parser.py`:

```python
"""Tests for adiff Action parser."""

import xml.etree.ElementTree as ET

from checkers import Action
from watcher import parse_adiff_actions


MODIFY_NODE_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <node id="42" version="1" lat="51.0" lon="-1.0" user="u1" changeset="100">
        <tag k="phone" v="123"/>
      </node>
    </old>
    <new>
      <node id="42" version="2" lat="51.1" lon="-1.1" user="u2" changeset="200">
        <tag k="phone" v="456"/>
      </node>
    </new>
  </action>
</osm>"""


CREATE_NODE_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="create">
    <new>
      <node id="99" version="1" lat="40.0" lon="-74.0" user="bob" changeset="300">
        <tag k="website" v="example.com"/>
      </node>
    </new>
  </action>
</osm>"""


MODIFY_WAY_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <way id="111" version="1" user="u1" changeset="100">
        <nd ref="1" lat="51.0" lon="-1.0"/>
        <nd ref="2" lat="51.1" lon="-1.1"/>
        <tag k="name" v="Main St"/>
      </way>
    </old>
    <new>
      <way id="111" version="2" user="u2" changeset="200">
        <nd ref="1" lat="51.0" lon="-1.0"/>
        <nd ref="2" lat="51.2" lon="-1.2"/>
        <tag k="name" v="Main St"/>
      </way>
    </new>
  </action>
</osm>"""


class TestParseAdiffActions:
    def test_modify_node(self):
        root = ET.fromstring(MODIFY_NODE_ADIFF)
        actions = list(parse_adiff_actions(root))
        assert len(actions) == 1
        a = actions[0]
        assert a.action_type == "modify"
        assert a.element_type == "node"
        assert a.element_id == "42"
        assert a.version == "2"
        assert a.changeset == "200"
        assert a.user == "u2"
        assert a.tags_old == {"phone": "123"}
        assert a.tags_new == {"phone": "456"}
        assert a.coords_old == (51.0, -1.0)
        assert a.coords_new == (51.1, -1.1)

    def test_create_node(self):
        root = ET.fromstring(CREATE_NODE_ADIFF)
        actions = list(parse_adiff_actions(root))
        assert len(actions) == 1
        a = actions[0]
        assert a.action_type == "create"
        assert a.element_type == "node"
        assert a.tags_new == {"website": "example.com"}
        assert a.tags_old == {}
        assert a.coords_old is None
        assert a.coords_new == (40.0, -74.0)

    def test_modify_way(self):
        root = ET.fromstring(MODIFY_WAY_ADIFF)
        actions = list(parse_adiff_actions(root))
        assert len(actions) == 1
        a = actions[0]
        assert a.action_type == "modify"
        assert a.element_type == "way"
        assert a.element_id == "111"
        assert a.nd_refs_old == ["1", "2"]
        assert a.nd_refs_new == ["1", "2"]
        assert a.node_coords_old == {"1": (51.0, -1.0), "2": (51.1, -1.1)}
        assert a.node_coords_new == {"1": (51.0, -1.0), "2": (51.2, -1.2)}
        assert a.tags_old == {"name": "Main St"}
        assert a.tags_new == {"name": "Main St"}
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_parser.py -v --tb=short`
Expected: ImportError — `parse_adiff_actions` doesn't exist

**Step 3: Implement parse_adiff_actions in watcher.py**

Add to `watcher.py`:

```python
from checkers import Action

def _extract_tags(elem: ET.Element) -> dict[str, str]:
    """Extract tags from an OSM element."""
    return {tag.get("k"): tag.get("v", "") for tag in elem.findall("tag")}


def _extract_node_action(action_type: str, old_elem, new_elem) -> Action:
    """Build an Action from a node action element."""
    primary = new_elem if new_elem is not None else old_elem
    return Action(
        action_type=action_type,
        element_type="node",
        element_id=primary.get("id"),
        version=primary.get("version", ""),
        changeset=primary.get("changeset", ""),
        user=primary.get("user", ""),
        tags_old=_extract_tags(old_elem) if old_elem is not None else {},
        tags_new=_extract_tags(new_elem) if new_elem is not None else {},
        coords_old=(float(old_elem.get("lat")), float(old_elem.get("lon")))
            if old_elem is not None and old_elem.get("lat") else None,
        coords_new=(float(new_elem.get("lat")), float(new_elem.get("lon")))
            if new_elem is not None and new_elem.get("lat") else None,
    )


def _extract_way_action(action_type: str, old_elem, new_elem) -> Action:
    """Build an Action from a way action element."""
    primary = new_elem if new_elem is not None else old_elem

    def _way_data(way):
        if way is None:
            return None, None, {}
        refs = [nd.get("ref") for nd in way.findall("nd")]
        coords = {}
        for nd in way.findall("nd"):
            lat, lon = nd.get("lat"), nd.get("lon")
            if lat and lon:
                coords[nd.get("ref")] = (float(lat), float(lon))
        return refs, coords if coords else None, _extract_tags(way)

    old_refs, old_coords, old_tags = _way_data(old_elem)
    new_refs, new_coords, new_tags = _way_data(new_elem)

    return Action(
        action_type=action_type,
        element_type="way",
        element_id=primary.get("id"),
        version=primary.get("version", ""),
        changeset=primary.get("changeset", ""),
        user=primary.get("user", ""),
        tags_old=old_tags,
        tags_new=new_tags,
        nd_refs_old=old_refs,
        nd_refs_new=new_refs,
        node_coords_old=old_coords,
        node_coords_new=new_coords,
    )


def _extract_relation_action(action_type: str, old_elem, new_elem) -> Action:
    """Build an Action from a relation action element."""
    primary = new_elem if new_elem is not None else old_elem
    return Action(
        action_type=action_type,
        element_type="relation",
        element_id=primary.get("id"),
        version=primary.get("version", ""),
        changeset=primary.get("changeset", ""),
        user=primary.get("user", ""),
        tags_old=_extract_tags(old_elem) if old_elem is not None else {},
        tags_new=_extract_tags(new_elem) if new_elem is not None else {},
    )


def parse_adiff_actions(root: ET.Element) -> list[Action]:
    """Parse an augmented diff XML tree into Action objects.

    Handles all element types (node, way, relation) and action types
    (create, modify, delete).
    """
    actions = []
    for action_elem in root.findall("action"):
        action_type = action_elem.get("type")
        old = action_elem.find("old")
        new = action_elem.find("new")

        old_child = None
        new_child = None

        # Determine element type from whichever side exists
        if new is not None:
            for elem_type in ("node", "way", "relation"):
                new_child = new.find(elem_type)
                if new_child is not None:
                    if old is not None:
                        old_child = old.find(elem_type)
                    break
        elif old is not None:
            for elem_type in ("node", "way", "relation"):
                old_child = old.find(elem_type)
                if old_child is not None:
                    break

        if new_child is None and old_child is None:
            continue

        elem_type = (new_child if new_child is not None else old_child).tag

        if elem_type == "node":
            actions.append(_extract_node_action(action_type, old_child, new_child))
        elif elem_type == "way":
            actions.append(_extract_way_action(action_type, old_child, new_child))
        elif elem_type == "relation":
            actions.append(_extract_relation_action(action_type, old_child, new_child))

    return actions
```

**Step 4: Run tests**

Run: `uv run python -m pytest tests/test_parser.py -v --tb=short`
Expected: All tests pass

**Step 5: Run all tests**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests pass

**Step 6: Commit**

```bash
git add watcher.py tests/test_parser.py
git commit -m "Add generalized adiff Action parser"
```

---

### Task 10: Wire checkers into the orchestrator

Update `process_adiff()` in `watcher.py` to run all checkers on parsed Actions, alongside the existing drag detection. The drag detection still uses its own specialized path for now (it needs the full XML for streaming parse and angle computation). Phone and website checkers use the Action-based path.

**Files:**
- Modify: `watcher.py`
- Modify: `notifiers/slack.py` — add formatting for phone/website issues, add fix button handlers

**Step 1: Update process_adiff to run tag checkers**

In `watcher.py`, update `process_adiff()`:

```python
from checkers.phone import PhoneChecker
from checkers.website import WebsiteChecker
from checkers import Issue

_tag_checkers = [PhoneChecker(), WebsiteChecker()]

def process_adiff(url: str, threshold_meters: float, bot_token: str | None = None,
                  channel_id: str | None = None, interactive: bool = False) -> list[dict]:
    """Fetch an adiff, detect drags and tag issues, and optionally alert."""
    path = fetch_adiff(url)
    try:
        drags = detect_node_drags(path, threshold_meters=threshold_meters)

        # Parse again for tag checkers (adiff files are small enough)
        root = ET.parse(path).getroot()
        actions = parse_adiff_actions(root)
    finally:
        os.unlink(path)

    drags = filter_drags(drags)
    for drag in drags:
        log.info(
            "Node drag: way %s node %s moved %.1fm (changeset %s by %s)",
            drag["way_id"], drag["node_id"], drag["distance_meters"],
            drag["changeset"], drag["user"],
        )

    # Run tag checkers
    tag_issues: list[Issue] = []
    for action in actions:
        for checker in _tag_checkers:
            tag_issues.extend(checker.check(action))

    for issue in tag_issues:
        log.info("Tag issue: %s %s — %s", issue.element_type, issue.element_id, issue.summary)

    if bot_token and channel_id:
        if drags:
            send_slack_summary(bot_token, channel_id, drags, interactive=interactive)
        if tag_issues:
            send_tag_issue_summary(bot_token, channel_id, tag_issues, interactive=interactive)

    return drags
```

**Step 2: Add tag issue Slack formatting to notifiers/slack.py**

Add to `notifiers/slack.py`:

```python
from checkers import Issue
import tag_fix as tag_fix_mod


def _format_tag_issue_text(issues: list[Issue], changeset: str, user: str) -> str:
    """Format mrkdwn text for tag issue alerts."""
    check_labels = {
        "phone_format": ":telephone_receiver: Phone formatting",
        "website_cleanup": ":globe_with_meridians: Website cleanup",
    }
    check_name = issues[0].check_name
    label = check_labels.get(check_name, check_name)

    lines = [
        f"{label} needed in "
        f"<https://osmcha.org/changesets/{changeset}|changeset {changeset}> "
        f"by {user}",
    ]

    for issue in issues:
        elem_link = f"<https://www.openstreetmap.org/{issue.element_type}/{issue.element_id}|{issue.element_type}/{issue.element_id}>"
        for tag_key in issue.tags_before:
            before = issue.tags_before[tag_key]
            after = issue.tags_after.get(tag_key, before)
            lines.append(f"• {elem_link}: `{tag_key}` {before} → {after}")

    return "\n".join(lines)


def build_tag_issue_blocks(issues: list[Issue], changeset: str, user: str) -> tuple[str, list[dict]]:
    """Build Block Kit blocks for tag issue alerts."""
    text = _format_tag_issue_text(issues, changeset, user)

    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
    ]

    value_dict = {
        "check_name": issues[0].check_name,
        "changeset": changeset,
        "issues": [
            {
                "element_type": i.element_type,
                "element_id": i.element_id,
                "element_version": i.element_version,
                "tags_before": i.tags_before,
                "tags_after": i.tags_after,
            }
            for i in issues
        ],
    }

    button_value = json.dumps(value_dict)
    n_elements = len(issues)
    check_name = issues[0].check_name
    action_label = "Format" if check_name == "phone_format" else "Clean up"

    blocks.append({
        "type": "actions",
        "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": f"{action_label}"},
            "style": "primary",
            "action_id": "fix_tags",
            "value": button_value,
            "confirm": {
                "title": {"type": "plain_text", "text": "Confirm Fix"},
                "text": {
                    "type": "mrkdwn",
                    "text": f"Fix {n_elements} element{'s' if n_elements != 1 else ''}?",
                },
                "confirm": {"type": "plain_text", "text": "Fix"},
                "deny": {"type": "plain_text", "text": "Cancel"},
            },
        }],
    })

    return text, blocks


def send_tag_issue_summary(bot_token: str, channel_id: str, issues: list[Issue],
                           interactive: bool = False) -> None:
    """Post tag issue alerts to Slack, grouped by changeset and check type."""
    # Group by (changeset, check_name)
    groups: dict[tuple[str, str], list[Issue]] = {}
    for issue in issues:
        key = (issue.changeset, issue.check_name)
        groups.setdefault(key, []).append(issue)

    for (changeset, check_name), group_issues in groups.items():
        user = group_issues[0].user
        if interactive:
            text, blocks = build_tag_issue_blocks(group_issues, changeset, user)
            _post_slack_message(bot_token, channel_id, text, blocks)
        else:
            text = _format_tag_issue_text(group_issues, changeset, user)
            _post_slack_message(bot_token, channel_id, text)


def handle_tag_fix_action(ack: Callable, body: dict, client: object, osm_token: str,
                          api_base: str = revert_mod.DEFAULT_OSM_API_BASE) -> None:
    """Slack Bolt action handler for fix_tags buttons."""
    ack()

    action = body["actions"][0]
    value = json.loads(action["value"])
    check_name = value["check_name"]

    user = body["user"]["username"]
    channel = body["channel"]["id"]
    ts = body["message"]["ts"]

    # Reconstruct Issue objects
    issues = [
        Issue(
            element_type=i["element_type"],
            element_id=i["element_id"],
            element_version=i["element_version"],
            changeset=value["changeset"],
            user="",
            check_name=check_name,
            summary="",
            tags_before=i["tags_before"],
            tags_after=i["tags_after"],
        )
        for i in value["issues"]
    ]

    try:
        cs_id = tag_fix_mod.fix_tags(osm_token, issues, api_base=api_base)

        original_blocks = body["message"].get("blocks", [])
        new_blocks = [b for b in original_blocks if b.get("type") != "actions"]
        new_blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": (
                    f":white_check_mark: Fixed by @{user} in "
                    f"<https://www.openstreetmap.org/changeset/{cs_id}|changeset {cs_id}>"
                ),
            }],
        })
        client.chat_update(channel=channel, ts=ts, blocks=new_blocks, text="Fixed")

    except tag_fix_mod.VersionConflictError as e:
        _update_message_error(body, client, f"Version conflict: {e}")

    except Exception as e:
        log.exception("Tag fix failed")
        _update_message_error(body, client, f"Fix failed: {e}")
```

**Step 3: Update start_socket_mode to register fix_tags handler**

In `notifiers/slack.py`, update `start_socket_mode`:

```python
def start_socket_mode(app_token: str, bot_token: str, osm_token: str) -> None:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = App(token=bot_token)

    @app.action("revert_node_drag")
    def _handle_revert(ack, body, client):
        handle_revert_action(ack, body, client, osm_token)

    @app.action("fix_tags")
    def _handle_fix(ack, body, client):
        handle_tag_fix_action(ack, body, client, osm_token)

    handler = SocketModeHandler(app, app_token)
    handler.connect()
    log.info("Socket Mode started for interactive buttons")
```

**Step 4: Update watcher.py imports**

Add `send_tag_issue_summary` to imports from `notifiers.slack`.

**Step 5: Run all tests**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests pass

**Step 6: Commit**

```bash
git add watcher.py notifiers/slack.py
git commit -m "Wire phone and website checkers into orchestrator"
```

---

### Task 11: Update Dockerfile

**Files:**
- Modify: `Dockerfile`

**Step 1: Update COPY to include new packages**

Update `Dockerfile`:

```dockerfile
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY watcher.py revert.py tag_fix.py ./
COPY checkers/ checkers/
COPY notifiers/ notifiers/

CMD ["/app/.venv/bin/python", "watcher.py"]
```

**Step 2: Commit**

```bash
git add Dockerfile
git commit -m "Update Dockerfile for new package structure"
```

---

### Task 12: Run full test suite and verify

**Step 1: Run all tests**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests pass

**Step 2: Smoke test with a real changeset**

Run: `uv run python watcher.py --changeset 179281034`
Expected: Detects the known node drag without errors (may not have phone/website issues in this changeset)

**Step 3: Verify imports are clean**

Run: `uv run python -c "from checkers.phone import PhoneChecker; from checkers.website import WebsiteChecker; from tag_fix import fix_tags; print('OK')"`
Expected: prints "OK"
