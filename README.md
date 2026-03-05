# node-drag-watcher

Watch the [OpenStreetMap augmented diff stream](https://adiffs.osmcha.org/) for accidental node drags and alert via Slack.

A "node drag" is when a mapper accidentally clicks a node and drags it while trying to pan the map. This typically manifests as a single node on a way moving a significant distance while the other nodes on the way stay put.

## Detection

Two patterns are detected:

1. **Classic drag** — A node keeps its ID but its coordinates move significantly while neighboring nodes on the same way don't move.
2. **Node substitution** — A node on a way is replaced by a different node far away (happens when the editor merges two nodes after a drag).

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
