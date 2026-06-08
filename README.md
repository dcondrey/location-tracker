# Location Tracker

A self-hosted location tracking dashboard that polls Google Maps location sharing and visualizes movement history on an interactive map. Runs as a background daemon with a real-time web interface.

## Features

- **Real-time tracking** -- Polls Google Maps shared locations with adaptive intervals (15s when driving, 10min when stationary)
- **Interactive dashboard** -- Dark-themed Leaflet map with path visualization, heatmaps, stop detection, and timeline scrubbing
- **Self-tracking** -- Track your own position via browser geolocation
- **Encrypted cookie storage** -- Google auth tokens encrypted at rest with Fernet; key stored in macOS Keychain
- **Auto cookie refresh** -- Headless browser automatically re-authenticates when cookies expire
- **SQLite storage** -- Location history stored in an indexed SQLite database with WAL mode
- **Mobile responsive** -- Bottom-sheet sidebar on phones, touch-friendly controls
- **Multiple export formats** -- JSON, CSV, and GeoJSON
- **Persistent daemon** -- Optional launchd integration to survive reboots
- **CLI query tools** -- Look up anyone's latest location or history from the terminal

## Quick Start

```bash
# Clone and set up
git clone https://github.com/dcondrey/location-tracker.git
cd location-tracker
./setup.sh

# Or manually:
uv sync
uv run location-tracker setup

# Configure your Google account
uv run location-tracker config --email you@gmail.com

# Authenticate with Google (opens browser)
uv run location-tracker cookies

# Start tracking
uv run location-tracker on
```

The dashboard will be available at **http://tracker** (or `http://localhost:7070` if DNS isn't configured).

## Prerequisites

- **Python 3.11+**
- **macOS** (uses Keychain for cookie encryption, launchd for persistence)
- **uv** package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
- A Google account with [location sharing](https://support.google.com/maps/answer/7326816) enabled

## Commands

### Core

| Command | Description |
|---------|-------------|
| `on` | Start the tracker daemon and web dashboard |
| `off` | Stop the tracker |
| `status` | Check if the tracker is running |
| `cookies` | Open browser to authenticate with Google |
| `test` | Verify cookies are valid and list shared contacts |

### Configuration

| Command | Description |
|---------|-------------|
| `config --email you@gmail.com` | Set the Google account email |
| `config` | Show current configuration |
| `setup` | First-time setup: install Chromium, configure DNS and port forwarding |
| `dns` | Set up `http://tracker` hostname |
| `dns --remove` | Remove hostname and port forwarding |
| `install` | Install as a launchd service (auto-start on login) |
| `install --remove` | Remove the launchd service |

### Data

| Command | Description |
|---------|-------------|
| `where <person>` | Show someone's latest known location |
| `history <person> --days 7` | Show recent location history |
| `stats` | Print tracking statistics (distance, stops, dwell time) |
| `map --days 7 --output map.html` | Generate a static HTML map |
| `purge <days>` | Delete location records older than N days |

## Dashboard

The web dashboard provides:

- **Map views** -- Road, Satellite, Hybrid, Terrain, and Dark map layers via Google/CARTO tiles
- **Visualization modes** -- Path view (color-coded routes with stop nodes), Heatmap, and Points
- **Time filtering** -- 24h, 3 days, 7 days, 30 days, 90 days, or all time
- **Timeline scrubber** -- Drag to view historical positions with date/time labels
- **Person cards** -- Click to focus; shows speed badge (Stationary/Walking/Driving/Highway)
- **Self-tracking** -- Enable browser geolocation to appear on the map yourself
- **Export** -- Download data as JSON, CSV, or GeoJSON
- **Toast notifications** -- Visual feedback for all actions

## How It Works

### Polling

The tracker polls Google Maps location sharing via [`locationsharinglib`](https://github.com/costastf/locationsharinglib). Polling frequency adapts to detected movement speed:

| Speed | Category | Poll Interval |
|-------|----------|--------------|
| > 60 km/h | Highway | 15 seconds |
| 10-60 km/h | Driving | 30 seconds |
| 1-10 km/h | Walking | 60 seconds |
| < 1 km/h | Stationary | 10 minutes |

The person being tracked receives no notification. This is a passive read of data they have already chosen to share.

### Security

- **Cookie encryption** -- Google auth cookies are encrypted with `cryptography.Fernet` before writing to disk. The encryption key is stored in macOS Keychain, never on the filesystem.
- **Localhost only** -- The Flask server binds to `127.0.0.1`; not accessible from the network.
- **XSS protection** -- All user-controlled data is HTML-escaped before rendering.
- **Input validation** -- Lat/lon bounds checking, type coercion, and error handling on all API endpoints.
- **Atomic writes** -- SQLite with WAL mode for concurrent safety.

### Data Storage

Location history is stored in a local SQLite database (`location_history.db`) with indexed columns for fast queries. Existing `location_history.json` files are automatically migrated on first run.

## Project Structure

```
location-tracker/
  main.py            # CLI entry point and daemon management
  tracker.py         # Location polling, stats, and map generation
  dashboard.py       # Flask web server and API endpoints
  db.py              # SQLite database layer
  cookie_store.py    # Encrypted cookie storage (Fernet + Keychain)
  get_cookies.py     # Browser-based Google authentication
  templates/
    index.html       # Dashboard HTML
  static/
    style.css        # Dashboard styles
    app.js           # Dashboard JavaScript
  setup.sh           # One-command install script
  build.sh           # PyInstaller standalone build
```

## License

MIT
