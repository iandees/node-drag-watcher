# Node Drag Watcher Design

## Purpose

Watch the OSM augmented diff stream and detect accidental node drags — when a user accidentally clicks and drags a single node on a way while trying to pan the map. Alert via Slack webhook.

## Data Source

- **Replication-aligned adiffs**: `https://adiffs.osmcha.org/replication/minute/<SEQNO>.adiff`
- **Changeset-aligned adiffs**: `https://adiffs.osmcha.org/changesets/<ID>.adiff`
- XML format with `<action type="modify">` blocks containing `<old>` and `<new>` elements
- Ways include inline node coordinates on `<nd>` elements (`<nd ref="..." lon="..." lat="..."/>`)
- Current replication state from `https://planet.openstreetmap.org/replication/minute/state.txt`

## Detection Algorithm

1. Parse the adiff XML with `xml.etree.ElementTree`
2. For each action containing a way, compare the `<nd>` elements between `<old>` and `<new>`:
   - Match nodes by `ref` attribute
   - Calculate distance between old and new lat/lon for each node
3. If exactly 1 node moved >= 10m (configurable) and the rest didn't move (or moved < 1m), flag as a node drag
4. Distance calculated using equirectangular approximation (accurate for short distances)

Node drags can occur alongside other edits in the same changeset. The way itself may or may not have been modified (version change, tag edits, etc).

## Slack Alert Content

- Way ID and name (if tagged)
- Node ID that was dragged
- Distance moved in meters
- Changeset ID and username
- Link to OSMCha: `https://osmcha.org/changesets/<id>`
- Link to node on OSM: `https://www.openstreetmap.org/node/<id>`

## Run Modes

### Continuous polling (default)
1. Read last processed sequence number from state file (or start from current)
2. Poll `planet.openstreetmap.org/replication/minute/state.txt` for latest sequence
3. Process unprocessed sequences (catch up if behind)
4. Fetch adiff, parse, detect, alert
5. Write processed sequence to state file
6. Sleep 60 seconds, repeat

### Single changeset (`--changeset <ID>`)
- Fetch `https://adiffs.osmcha.org/changesets/<ID>.adiff`
- Run detection, print results to stdout
- Post to Slack if webhook is configured
- Exit

## Configuration

- `SLACK_WEBHOOK_URL` — env var for Slack webhook
- `DRAG_THRESHOLD_METERS` — env var, default 10
- `STATE_FILE` — env var, default `state.txt` in working dir

## Project Structure

```
node-drag-watcher/
├── watcher.py          # Main script: CLI, polling loop, detection, Slack posting
├── pyproject.toml      # uv project config (dependency: requests)
└── .env.example        # Example environment variables
```

## Technology

- Python, managed with uv
- `xml.etree.ElementTree` for XML parsing
- `requests` for HTTP
- Single-file implementation
