# Checker Plugin Architecture Design

## Problem

The node-drag-watcher app monitors the OSM adiff stream and detects accidental node drags. We want to add two new checks — phone number formatting and website URL cleanup — without growing watcher.py into an unmaintainable monolith. The app already has adiff downloading, Slack communication, and OSM API integration, so these new checks should piggyback on the existing infrastructure.

## Design Decisions

- **Slack approval before applying any fix** (not fully automated)
- **Same adiff stream** — single parse pass feeds all checkers
- **International phone support from the start** using `phonenumbers` library
- **Live HTTPS check with fallback** for website URLs
- **Same Slack channel** for all check types
- **No changeset discussion comment** for phone/website fixes
- **All element types** (nodes, ways, relations) for tag checks, but only created/modified elements
- **All tag variants** — `phone`, `contact:phone`, `fax`, `contact:fax`, `website`, `contact:website`, plus `*:phone:*`, `*:website:*` patterns

## Architecture

Three layers, each with one job:

1. **Checkers** — detect issues from adiff data
2. **Notifiers** — present issues to humans, collect approval
3. **Fixers** — apply corrections to OSM

### Core Abstractions

```python
@dataclass
class Action:
    action_type: str           # "create", "modify", "delete"
    element_type: str          # "node", "way", "relation"
    element_id: str
    version: str
    changeset: str
    user: str
    tags_old: dict[str, str]   # Empty for creates
    tags_new: dict[str, str]   # Empty for deletes
    coords_old: tuple[float, float] | None
    coords_new: tuple[float, float] | None
    nd_refs_old: list[str] | None
    nd_refs_new: list[str] | None
    node_coords_old: dict[str, tuple[float, float]] | None
    node_coords_new: dict[str, tuple[float, float]] | None

@dataclass
class Issue:
    element_type: str          # "node", "way", "relation"
    element_id: str
    element_version: str
    changeset: str
    user: str
    check_name: str            # "phone_format", "website_cleanup", "node_drag"
    summary: str               # Human-readable one-liner
    tags_before: dict[str, str]
    tags_after: dict[str, str]
    extra: dict                # Checker-specific data

class BaseChecker(ABC):
    @abstractmethod
    def check(self, action: Action) -> list[Issue]:
        """Check a single element action from the adiff."""
```

### Phone Number Checker

Uses `phonenumbers` library (Python port of Google's libphonenumber).

**Tags checked:** `phone`, `contact:phone`, `fax`, `contact:fax`, and any `phone:*`, `contact:phone:*` patterns.

**Logic:**
1. For each create/modify action, scan relevant tags
2. Parse with `phonenumbers.parse(value, None)` (require `+` prefix)
3. If parsing fails, infer country from object coordinates and re-parse
4. Format as `phonenumbers.format_number(num, PhoneNumberFormat.INTERNATIONAL)` (e.g. `+1 920-867-5309`)
5. If formatted differs from original, emit Issue with `tags_after` containing the correction
6. If parse fails entirely, skip

**Multi-value:** Split on `;`, format each individually, rejoin.

**Skip when:** already formatted correctly, can't parse, no coordinates for country inference.

### Website Cleanup Checker

**Tags checked:** `website`, `contact:website`, `url`, and `website:*`, `contact:website:*` patterns.

**Step 1 — Structural normalization (no network):**
- Prepend `https://` if no scheme
- Lowercase domain
- Strip tracking params: `utm_*`, `fbclid`, `gclid`, `ref`, `mc_cid`, `mc_eid`
- Strip trailing `/` on bare domains
- Decode unnecessary percent-encoding

**Step 2 — HTTPS upgrade (network, with fallback):**
- If `http://`, HEAD request to `https://` version (3-5s timeout)
- If 2xx/3xx to same domain, upgrade
- Failure/timeout: keep `http://`

**Step 3 — Follow redirects (network, with fallback):**
- If HEAD redirects to different URL on same domain, use final URL
- Don't follow cross-domain redirects
- Failure: keep structurally normalized URL

**Skip when:** already matches normalized output, not a website URL (`mailto:`, `tel:`).

### Drag Checker

Existing detection logic extracted from watcher.py. Operates on way-level actions using `node_coords_old`/`node_coords_new` to compute distances and angles. Includes the existing angle-based filtering to suppress false positives.

### Notifier Layer

```python
class BaseNotifier(ABC):
    @abstractmethod
    def notify(self, issues: list[Issue]) -> None:
        """Send notifications for detected issues."""

    @abstractmethod
    def listen(self) -> None:
        """Listen for user responses."""
```

**SlackNotifier:**
- Groups issues by changeset and check type
- Builds check-specific Slack blocks:
  - Drag: way name, distance, angle, image
  - Phone: tag name, original → formatted value
  - Website: original URL → cleaned URL
- One "Fix" button per group
- Button click calls the appropriate fixer, updates message with result

### Fixer Layer

**`tag_fix.py`** — for phone and website fixes:
1. Fetch current element, verify version matches
2. Create changeset with descriptive comment
3. PUT element with corrected tags (preserve all other tags and geometry)
4. Close changeset

**`revert.py`** — for drag fixes (existing, unchanged):
- `revert_changeset()` with node_ids/way_ids from `Issue.extra`

**Registry:**
```python
def apply_fix(issue: Issue, osm_token: str) -> str:
    if issue.check_name == "node_drag":
        result = revert_changeset(osm_token, issue.changeset, ...)
        return result.revert_changeset_id
    elif issue.check_name in ("phone_format", "website_cleanup"):
        return fix_tags(osm_token, issue)
```

### Orchestrator

watcher.py becomes a thin loop:

```python
checkers = [DragChecker(), PhoneChecker(), WebsiteChecker()]
notifier = SlackNotifier(bot_token, channel_id)

for action in parse_adiff(path):
    for checker in checkers:
        issues.extend(checker.check(action))

for issue_group in group_issues(issues):
    notifier.notify(issue_group)
```

## File Structure

```
watcher.py                 # Orchestrator: polling, adiff parsing, Action model
revert.py                  # Existing drag revert logic (unchanged)
tag_fix.py                 # Apply tag corrections to OSM
checkers/
    __init__.py            # BaseChecker, Issue, Action exports
    drag.py                # Node drag detection + angle filtering
    phone.py               # Phone number formatting
    website.py             # URL cleanup
notifiers/
    __init__.py            # BaseNotifier export
    slack.py               # Slack blocks, posting, button handling
tests/
    test_revert.py         # Existing (unchanged)
    test_drag.py           # Drag detection tests
    test_phone.py          # Phone checker tests
    test_website.py        # Website checker tests
    test_tag_fix.py        # Tag fix tests
    test_slack.py          # Slack formatting + button handler tests
    test_integration.py    # Existing (updated imports)
```

## Dependencies

**New:** `phonenumbers` (pure Python, no C deps)

**Existing:** `requests` handles website HEAD checks, `urllib.parse` from stdlib handles URL parsing.

## Environment Variables

No new env vars. Same tokens, same channel. All checks always active.
