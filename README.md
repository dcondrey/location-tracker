# Location Tracker

A self-hosted location tracking dashboard that polls Google Maps location sharing and visualizes movement history on an interactive map. Runs as a background daemon with a real-time web interface.

## Features

- **Real-time tracking** -- Polls Google Maps shared locations with adaptive intervals (15s when driving, 10min when stationary)
- **Interactive dashboard** -- Dark-themed Leaflet map with path visualization, heatmaps, stop detection, and timeline scrubbing
- **Self-tracking** -- Track your own position via browser geolocation
- **Encrypted cookie storage** -- Google auth tokens encrypted at rest with Fernet, key stored in macOS Keychain
- **Auto cookie refresh** -- Headless browser automatically re-authenticates when cookies expire
- **SQLite storage** -- Location history stored in an indexed SQLite database with WAL mode
- **Mobile responsive** -- Bottom-sheet sidebar on phones, touch-friendly controls
- **Multiple export formats** -- JSON, CSV, and GeoJSON
- **Persistent daemon** -- Optional launchd integration to survive reboots
- **CLI query tools** -- Look up anyone's latest location or history from the terminal

## Install

### From PyPI

```bash
pipx install location-tracker
# or
uv tool install location-tracker
```

### From source

```bash
git clone https://github.com/dcondrey/location-tracker.git
cd location-tracker
./setup.sh
```

### Manual setup

```bash
uv sync
uv run location-tracker setup
```

## Getting Started

```bash
# 1. Set your Google account email
location-tracker config --email you@gmail.com

# 2. One command does everything: install browser, configure DNS, authenticate, start, and open dashboard
location-tracker setup
```

That's it. The setup command installs Chromium, configures `tracker.local` in `/etc/hosts`, opens a browser for Google sign-in, encrypts the cookies, starts the daemon, and opens the dashboard automatically.

The dashboard runs at **http://tracker.local** (port 80 via `sudo`). If the hostname doesn't resolve, use `http://localhost`.

## Prerequisites

- **Python 3.11+**
- **macOS** (uses Keychain for cookie encryption, launchd for persistence)
- **uv** package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
- A Google account with [location sharing](https://support.google.com/maps/answer/7326816) enabled

## Commands

### Tracking

| Command | Description |
|---------|-------------|
| `on` | Start the tracker daemon and web dashboard |
| `off` | Stop the tracker |
| `status` | Check if the tracker is running |

### Authentication

| Command | Description |
|---------|-------------|
| `cookies` | Open browser to authenticate with Google |
| `test` | Verify cookies are valid and list shared contacts |

When cookies expire, the tracker automatically attempts a headless browser refresh using the saved browser profile. If that fails (e.g. Google requires re-login), it logs an error and you re-run `location-tracker cookies`.

### Configuration

| Command | Description |
|---------|-------------|
| `config --email you@gmail.com` | Set the Google account email |
| `config` | Show current configuration |
| `setup` | Full setup: install browser, DNS, authenticate, start, and open dashboard |

### Service Management

| Command | Description |
|---------|-------------|
| `install` | Install as a launchd service (auto-start on login) |
| `uninstall` | Remove the launchd service |
| `dns` | Manually set up `http://tracker.local` hostname |
| `dns-remove` | Remove custom hostname |

### Querying Data

| Command | Description |
|---------|-------------|
| `where <person>` | Show someone's latest known location |
| `history <person> --days 7` | Show recent location history (last 20 entries) |
| `stats` | Print tracking statistics (distance, stops, dwell time) |
| `map --days 7 --output map.html` | Generate a static HTML map |
| `purge <days>` | Delete location records older than N days |

## Dashboard

The web dashboard at `http://tracker.local` provides:

- **Map layers** -- Road, Satellite, Hybrid, Terrain, and Dark via Google/CARTO tiles
- **Visualization modes** -- Path (color-coded routes with stop nodes), Heatmap, and Points
- **Time filtering** -- 24h, 3 days, 7 days, 30 days, 90 days, or all time
- **Timeline scrubber** -- Drag to view historical positions; shows date/time labels
- **Person cards** -- Click to focus; shows speed badge (Stationary/Walking/Driving/Highway)
- **Self-tracking** -- Enable browser geolocation to appear on the map
- **Export** -- Download data as JSON, CSV, or GeoJSON
- **Toast notifications** -- Visual feedback for all actions
- **Mobile layout** -- Bottom-sheet sidebar on screens under 640px

## How It Works

### Adaptive Polling

The tracker polls Google Maps location sharing via [`locationsharinglib`](https://github.com/costastf/locationsharinglib). Polling frequency adapts to detected movement:

| Speed | Category | Poll Interval |
|-------|----------|--------------|
| > 60 km/h | Highway | 15 seconds |
| 10-60 km/h | Driving | 30 seconds |
| 1-10 km/h | Walking | 60 seconds |
| < 1 km/h | Stationary | 10 minutes |

This gives you detailed path traces when someone is moving, and conserves resources when they're not. The tracked person receives no notification; this is a passive read of data they've chosen to share.

### Cookie Lifecycle

1. **Capture**: `location-tracker cookies` opens a Chromium browser to Google sign-in. Cookies are detected automatically when login completes (supports MFA, 15-minute timeout).
2. **Encryption**: Cookies are encrypted with `cryptography.Fernet` and saved as `cookies.enc`. The encryption key is stored in macOS Keychain, never on the filesystem.
3. **Usage**: On each poll, cookies are decrypted to a temporary file, passed to the API, then the temp file is deleted.
4. **Expiry**: When Google rejects the cookies, the tracker attempts an automatic headless refresh using the persistent browser profile. If the Google session is still valid, fresh cookies are captured without user interaction. If not, the tracker logs an error and continues polling at the default interval until you re-run `location-tracker cookies`.

### Security

- **Encrypted at rest** -- Auth cookies encrypted with Fernet; key in macOS Keychain
- **Localhost only** -- Flask binds to `127.0.0.1`; not accessible from the network
- **XSS protection** -- All user-controlled data HTML-escaped before rendering
- **Input validation** -- Lat/lon bounds checking on all coordinate inputs
- **Atomic storage** -- SQLite with WAL mode for concurrent read/write safety
- **No plaintext secrets** -- Plaintext `cookies.txt` auto-migrated and deleted on first run

### Data Storage

Location history is stored in a local SQLite database (`location_history.db`) with indexed columns for person, timestamp, and compound queries. Existing `location_history.json` files are automatically migrated on first run.

Use `location-tracker purge <days>` to enforce a retention policy.

## Project Structure

```
location-tracker/
  main.py            # CLI entry point and daemon management
  tracker.py         # Location polling, stats, and static map generation
  dashboard.py       # Flask web server and API endpoints
  db.py              # SQLite database layer
  cookie_store.py    # Encrypted cookie storage (Fernet + Keychain)
  get_cookies.py     # Browser-based Google authentication
  templates/
    index.html       # Dashboard HTML
  static/
    style.css        # Dashboard styles (dark theme, mobile responsive)
    app.js           # Dashboard JavaScript (Leaflet map, real-time updates)
  tests/
    test_db.py       # Database layer tests
    test_tracker.py  # Tracker logic tests
    test_dashboard.py # Dashboard function tests
  setup.sh           # One-command install script
  build.sh           # PyInstaller standalone build
```

## API Endpoints

The dashboard exposes these local API endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/locations?days=7` | Location history with per-person speed info |
| GET | `/api/stats` | Tracking statistics per person |
| GET | `/api/poll-status` | Current polling interval and speed category |
| GET | `/api/export?format=json` | Export all data (json, csv, geojson) |
| POST | `/api/self-location` | Submit browser geolocation |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, code style, and areas where help is wanted.

## License

MIT
