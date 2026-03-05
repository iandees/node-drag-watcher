# Node Drag Watcher Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Python tool that watches the OSM augmented diff stream and alerts via Slack when a single node on a way is accidentally dragged.

**Architecture:** Single-file Python script (`watcher.py`) with two modes: continuous polling of minute-by-minute replication diffs, and single-changeset mode for testing. Detection compares old/new node positions on ways in the adiff XML. Alerts go to a Slack webhook.

**Tech Stack:** Python 3.12+, uv, requests, xml.etree.ElementTree

---

### Task 1: Initialize uv project

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.env.example`
- Create: `.gitignore`

**Step 1: Create pyproject.toml**

```toml
[project]
name = "node-drag-watcher"
version = "0.1.0"
description = "Watch OSM augmented diffs for accidental node drags"
requires-python = ">=3.12"
dependencies = [
    "requests>=2.31",
]

[project.scripts]
node-drag-watcher = "watcher:main"
```

**Step 2: Create .python-version**

```
3.12
```

**Step 3: Create .env.example**

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
DRAG_THRESHOLD_METERS=10
STATE_FILE=state.txt
```

**Step 4: Create .gitignore**

```
__pycache__/
*.pyc
.venv/
.env
state.txt
```

**Step 5: Initialize uv and sync**

Run: `uv sync`
Expected: Creates `.venv/` and installs requests

**Step 6: Commit**

```bash
git add pyproject.toml .python-version .env.example .gitignore uv.lock
git commit -m "Initialize uv project with requests dependency"
```

---

### Task 2: Write detection logic with tests

**Files:**
- Create: `watcher.py`
- Create: `tests/test_detection.py`

**Step 1: Write test fixtures and detection tests**

Create `tests/test_detection.py`:

```python
import xml.etree.ElementTree as ET
import math
import pytest

from watcher import detect_node_drags, haversine_distance

# Minimal adiff XML: one way where exactly 1 of 4 nodes moved significantly
SINGLE_DRAG_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.0010" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
        <tag k="highway" v="residential"/>
        <tag k="name" v="Test Street"/>
      </way>
    </old>
    <new>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.0015" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
        <tag k="highway" v="residential"/>
        <tag k="name" v="Test Street"/>
      </way>
    </new>
  </action>
</osm>"""

# Adiff where multiple nodes moved (not a drag)
MULTI_NODE_MOVE_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.0010" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
      </way>
    </old>
    <new>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0005" lon="-74.0005"/>
        <nd ref="2" lat="40.0015" lon="-74.0015"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
      </way>
    </new>
  </action>
</osm>"""

# Adiff where 1 node moved but less than threshold
SMALL_MOVE_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.00001" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
      </way>
    </old>
    <new>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.00002" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
      </way>
    </new>
  </action>
</osm>"""

# Adiff with no ways (only node modifications)
NO_WAYS_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="modify">
    <old>
      <node id="999" version="1" lat="40.0" lon="-74.0"/>
    </old>
    <new>
      <node id="999" version="2" lat="40.1" lon="-74.1" timestamp="2025-01-01T00:00:00Z" uid="100" user="testuser" changeset="99999"/>
    </new>
  </action>
</osm>"""

# Adiff with a drag alongside other edits in the same changeset
DRAG_WITH_OTHER_EDITS_ADIFF = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <action type="create">
    <new>
      <node id="888" version="1" lat="41.0" lon="-75.0" timestamp="2025-01-01T00:00:00Z" uid="100" user="testuser" changeset="99999"/>
    </new>
  </action>
  <action type="modify">
    <old>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.0010" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
        <tag k="highway" v="residential"/>
      </way>
    </old>
    <new>
      <way id="12345" version="5" user="testuser" uid="100" timestamp="2025-01-01T00:00:00Z" changeset="99999">
        <nd ref="1" lat="40.0000" lon="-74.0000"/>
        <nd ref="2" lat="40.0015" lon="-74.0010"/>
        <nd ref="3" lat="40.0020" lon="-74.0020"/>
        <nd ref="4" lat="40.0030" lon="-74.0030"/>
        <tag k="highway" v="residential"/>
      </way>
    </new>
  </action>
</osm>"""


def test_haversine_distance():
    # Known distance: ~111km per degree of latitude at equator
    d = haversine_distance(0.0, 0.0, 1.0, 0.0)
    assert 110_000 < d < 112_000

    # Same point = 0
    d = haversine_distance(40.0, -74.0, 40.0, -74.0)
    assert d == 0.0

    # Small distance (~55m)
    d = haversine_distance(40.0000, -74.0000, 40.0005, -74.0000)
    assert 50 < d < 60


def test_detects_single_node_drag():
    root = ET.fromstring(SINGLE_DRAG_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 1
    drag = drags[0]
    assert drag["way_id"] == "12345"
    assert drag["node_id"] == "2"
    assert drag["distance_meters"] > 10
    assert drag["changeset"] == "99999"
    assert drag["user"] == "testuser"
    assert drag["way_name"] == "Test Street"


def test_ignores_multi_node_move():
    root = ET.fromstring(MULTI_NODE_MOVE_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 0


def test_ignores_small_move():
    root = ET.fromstring(SMALL_MOVE_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 0


def test_no_ways_returns_empty():
    root = ET.fromstring(NO_WAYS_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 0


def test_drag_detected_alongside_other_edits():
    root = ET.fromstring(DRAG_WITH_OTHER_EDITS_ADIFF)
    drags = detect_node_drags(root, threshold_meters=10)
    assert len(drags) == 1
    assert drags[0]["node_id"] == "2"
```

**Step 2: Write minimal watcher.py with detection logic**

Create `watcher.py`:

```python
"""Watch OSM augmented diffs for accidental node drags."""

import math
import xml.etree.ElementTree as ET


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance in meters between two lat/lon points."""
    R = 6_371_000  # Earth radius in meters
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def detect_node_drags(root, threshold_meters=10):
    """Detect single-node drags in an augmented diff XML tree.

    Returns a list of dicts with info about each detected drag.
    """
    drags = []

    for action in root.findall("action"):
        if action.get("type") != "modify":
            continue

        old = action.find("old")
        new = action.find("new")
        if old is None or new is None:
            continue

        old_way = old.find("way")
        new_way = new.find("way")
        if old_way is None or new_way is None:
            continue

        old_nds = {
            nd.get("ref"): (float(nd.get("lat")), float(nd.get("lon")))
            for nd in old_way.findall("nd")
        }
        new_nds = {
            nd.get("ref"): (float(nd.get("lat")), float(nd.get("lon")))
            for nd in new_way.findall("nd")
        }

        # Only check nodes present in both old and new
        common_refs = set(old_nds) & set(new_nds)
        if len(common_refs) < 3:
            continue

        moved = []
        for ref in common_refs:
            old_lat, old_lon = old_nds[ref]
            new_lat, new_lon = new_nds[ref]
            dist = haversine_distance(old_lat, old_lon, new_lat, new_lon)
            if dist >= threshold_meters:
                moved.append((ref, dist))

        if len(moved) == 1:
            node_ref, distance = moved[0]
            way_name = ""
            for tag in new_way.findall("tag"):
                if tag.get("k") == "name":
                    way_name = tag.get("v", "")
                    break

            # Get changeset/user from the new way element
            changeset = new_way.get("changeset", "")
            user = new_way.get("user", "")

            drags.append({
                "way_id": new_way.get("id"),
                "way_name": way_name,
                "node_id": node_ref,
                "distance_meters": round(distance, 1),
                "changeset": changeset,
                "user": user,
            })

    return drags
```

**Step 3: Run tests**

Run: `uv run pytest tests/test_detection.py -v`
Expected: All 6 tests pass

**Step 4: Commit**

```bash
git add watcher.py tests/test_detection.py
git commit -m "Add node drag detection logic with tests"
```

---

### Task 3: Add Slack notification

**Files:**
- Modify: `watcher.py`
- Create: `tests/test_slack.py`

**Step 1: Write Slack notification test**

Create `tests/test_slack.py`:

```python
from unittest.mock import patch, MagicMock
from watcher import send_slack_alert


def test_send_slack_alert_posts_message():
    drag = {
        "way_id": "12345",
        "way_name": "Test Street",
        "node_id": "67890",
        "distance_meters": 55.3,
        "changeset": "99999",
        "user": "testuser",
    }
    with patch("watcher.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        send_slack_alert("https://hooks.slack.com/test", drag)
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert "Test Street" in payload["text"]
        assert "12345" in payload["text"]
        assert "67890" in payload["text"]
        assert "55.3" in payload["text"]
        assert "99999" in payload["text"]
        assert "testuser" in payload["text"]


def test_send_slack_alert_no_way_name():
    drag = {
        "way_id": "12345",
        "way_name": "",
        "node_id": "67890",
        "distance_meters": 55.3,
        "changeset": "99999",
        "user": "testuser",
    }
    with patch("watcher.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        send_slack_alert("https://hooks.slack.com/test", drag)
        mock_post.assert_called_once()
```

**Step 2: Add send_slack_alert to watcher.py**

Add to `watcher.py` after the imports:

```python
import requests
```

Add after `detect_node_drags`:

```python
def send_slack_alert(webhook_url, drag):
    """Post a node drag alert to Slack."""
    way_label = f"way {drag['way_id']}"
    if drag["way_name"]:
        way_label += f" ({drag['way_name']})"

    text = (
        f":warning: Possible node drag detected\n"
        f"*{way_label}*: node {drag['node_id']} moved {drag['distance_meters']}m\n"
        f"User: {drag['user']} | "
        f"<https://osmcha.org/changesets/{drag['changeset']}|Changeset {drag['changeset']}> | "
        f"<https://www.openstreetmap.org/node/{drag['node_id']}|Node {drag['node_id']}>"
    )
    requests.post(webhook_url, json={"text": text}, timeout=10)
```

**Step 3: Run tests**

Run: `uv run pytest tests/ -v`
Expected: All 8 tests pass

**Step 4: Commit**

```bash
git add watcher.py tests/test_slack.py
git commit -m "Add Slack webhook notification for node drags"
```

---

### Task 4: Add CLI and polling loop

**Files:**
- Modify: `watcher.py`

**Step 1: Add CLI argument parsing and main loop**

Add to `watcher.py`:

```python
import argparse
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

ADIFF_BASE = "https://adiffs.osmcha.org"
REPLICATION_STATE_URL = "https://planet.openstreetmap.org/replication/minute/state.txt"


def fetch_adiff(url):
    """Fetch and parse an augmented diff XML from a URL."""
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return ET.fromstring(resp.content)


def get_latest_sequence():
    """Get the latest replication sequence number from OSM."""
    resp = requests.get(REPLICATION_STATE_URL, timeout=10, allow_redirects=True)
    resp.raise_for_status()
    for line in resp.text.splitlines():
        if line.startswith("sequenceNumber="):
            return int(line.split("=")[1])
    raise ValueError("Could not parse sequence number from state.txt")


def read_state(state_file):
    """Read the last processed sequence number from state file."""
    try:
        with open(state_file) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def write_state(state_file, seq):
    """Write the last processed sequence number to state file."""
    with open(state_file, "w") as f:
        f.write(str(seq))


def process_adiff(url, threshold_meters, webhook_url=None):
    """Fetch an adiff, detect drags, and optionally alert."""
    root = fetch_adiff(url)
    drags = detect_node_drags(root, threshold_meters=threshold_meters)
    for drag in drags:
        log.info(
            "Node drag: way %s node %s moved %.1fm (changeset %s by %s)",
            drag["way_id"], drag["node_id"], drag["distance_meters"],
            drag["changeset"], drag["user"],
        )
        if webhook_url:
            send_slack_alert(webhook_url, drag)
    return drags


def run_polling(webhook_url, threshold_meters, state_file):
    """Continuously poll for new replication diffs and process them."""
    seq = read_state(state_file)
    if seq is None:
        seq = get_latest_sequence()
        log.info("No state file found, starting from sequence %d", seq)
        write_state(state_file, seq)

    while True:
        try:
            latest = get_latest_sequence()
            if latest <= seq:
                log.debug("No new diffs (at %d)", seq)
                time.sleep(60)
                continue

            for s in range(seq + 1, latest + 1):
                url = f"{ADIFF_BASE}/replication/minute/{s}.adiff"
                log.info("Processing sequence %d", s)
                try:
                    process_adiff(url, threshold_meters, webhook_url)
                except requests.HTTPError as e:
                    log.warning("Failed to fetch sequence %d: %s", s, e)
                write_state(state_file, s)

            seq = latest
        except Exception:
            log.exception("Error in polling loop")

        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="Watch OSM diffs for node drags")
    parser.add_argument(
        "--changeset",
        type=int,
        help="Process a single changeset ID and exit",
    )
    args = parser.parse_args()

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    threshold = float(os.environ.get("DRAG_THRESHOLD_METERS", "10"))
    state_file = os.environ.get("STATE_FILE", "state.txt")

    if args.changeset:
        url = f"{ADIFF_BASE}/changesets/{args.changeset}.adiff"
        log.info("Processing changeset %d", args.changeset)
        drags = process_adiff(url, threshold, webhook_url)
        if not drags:
            log.info("No node drags detected")
        sys.exit(0)

    if not webhook_url:
        log.warning("SLACK_WEBHOOK_URL not set, will only log detections")

    run_polling(webhook_url, threshold, state_file)


if __name__ == "__main__":
    main()
```

**Step 2: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All tests pass

**Step 3: Manual smoke test with a known changeset**

Run: `uv run python watcher.py --changeset 161348203`
Expected: Logs detected node drags (or "No node drags detected") and exits

**Step 4: Commit**

```bash
git add watcher.py
git commit -m "Add CLI with --changeset mode and continuous polling loop"
```

---

### Task 5: End-to-end smoke test

**Step 1: Test --changeset mode with a real changeset**

Run: `uv run python watcher.py --changeset 161348203`
Expected: Should detect and log any node drags in that changeset

**Step 2: Test polling mode briefly (Ctrl+C to stop)**

Run: `DRAG_THRESHOLD_METERS=10 uv run python watcher.py`
Expected: Starts polling, logs sequence numbers being processed, Ctrl+C to stop

**Step 3: Verify state file was written**

Run: `cat state.txt`
Expected: Contains a sequence number

**Step 4: Final commit if any fixes needed**

Only commit if changes were made during smoke testing.
