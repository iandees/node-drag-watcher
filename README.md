# node-drag-watcher

Watch the [OpenStreetMap augmented diff stream](https://adiffs.osmcha.org/) for accidental node drags and alert via Slack.

A "node drag" is when a mapper accidentally clicks a node and drags it while trying to pan the map. This typically manifests as a single node on a way moving a significant distance while the other nodes on the way stay put.

## Detection

### Node drags

Two drag patterns are detected:

1. **Classic drag** — A node keeps its ID but its coordinates move significantly while neighboring nodes on the same way don't move.
2. **Node substitution** — A node on a way is replaced by a different node far away (happens when the editor merges two nodes after a drag).

### Tag cleanup

Checkers run on every element in the diff stream and flag tags that need fixing:

- **Website cleanup** — Adds missing scheme, lowercases domain, strips tracking parameters (utm_*, fbclid, gclid, y_source, etc.), fixes doubled/truncated schemes, expands known URL shorteners (bit.ly, tinyurl.com, acortar.link, etc.), upgrades HTTP to HTTPS when possible. URLs copied from Google Maps (detected via `utm_source=gmb` and similar) trigger a changeset comment reminding the mapper not to copy from Google.
- **Phone formatting** — Flags phone numbers missing a country code.

## Architecture

Plugin-based checker system. The main entry point (`watcher.py`) polls the OSM adiff stream, parses the augmented diff XML, runs checkers on each element, filters results, and notifies via Slack.

### Key files

| File | Purpose |
|---|---|
| `watcher.py` | CLI entry point, polling loop, adiff XML parsing, orchestration |
| `checkers/__init__.py` | Base classes: `Action`, `Issue`, `BaseChecker` |
| `checkers/drag.py` | Node drag detection (classic drags + node substitutions), angle-based filtering |
| `checkers/website.py` | URL normalization: scheme fixes, domain lowercasing, tracking param stripping, shortener expansion, HTTPS upgrade |
| `checkers/phone.py` | Phone number formatting using `phonenumbers` library |
| `tag_fix.py` | Apply tag corrections back to OSM via API |
| `revert.py` | Changeset revert logic, OSM API interaction |
| `notifiers/slack.py` | Slack messaging, image upload, interactive buttons |

### Data flow

```
OSM augmented diff XML
  → watcher.py: iter_adiff_actions_from_file() streams Action objects
  → checkers run on each Action (WebsiteChecker, PhoneChecker)
  → detect_drags_from_actions() finds drag patterns in ways
  → filter_drags() removes false positives via angle analysis
  → notifiers/slack.py sends alerts
```

## Usage

### Test with a specific changeset

```bash
uv run python watcher.py --changeset 179281034
```

### Run continuously

```bash
export SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
uv run python watcher.py
```

### Docker

```bash
docker run -d --restart=unless-stopped \
  -e SLACK_WEBHOOK_URL=https://hooks.slack.com/services/... \
  -v node-drag-state:/app/state \
  ghcr.io/iandees/node-drag-watcher:latest
```

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `SLACK_WEBHOOK_URL` | (none) | Slack incoming webhook URL |
| `DRAG_THRESHOLD_METERS` | `10` | Minimum distance (meters) to flag as a drag |
| `STATE_FILE` | `/app/state/state.txt` | Path to persist last processed sequence number |

## Development

```bash
uv sync
uv run pytest tests/ -v
```

## License

MIT
