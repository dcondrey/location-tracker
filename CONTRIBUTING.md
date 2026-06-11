# Contributing to Location Tracker

Thanks for your interest in contributing! This project is open to contributions of all kinds: bug reports, feature requests, documentation improvements, and code changes.

## Getting Started

1. Fork the repo and clone your fork
2. Run `./setup.sh` or manually: `uv sync && uv run location-tracker setup`
3. Configure: `uv run location-tracker config --email you@gmail.com`
4. Authenticate: `uv run location-tracker cookies`
5. Start developing: `uv run location-tracker on`

## Development Setup

```bash
# Install dependencies
uv sync

# Run the dashboard in foreground (for development)
uv run python main.py _serve

# Run import check
uv run python -c "import main, tracker, dashboard, db, cookie_store"
```

## Project Structure

| File | Purpose |
|------|---------|
| `main.py` | CLI entry point, daemon management, setup commands |
| `tracker.py` | Location polling, statistics, static map generation |
| `dashboard.py` | Flask server, API endpoints |
| `db.py` | SQLite database layer |
| `cookie_store.py` | Encrypted cookie storage (Fernet + macOS Keychain) |
| `get_cookies.py` | Browser-based Google authentication |
| `templates/index.html` | Dashboard HTML |
| `static/style.css` | Dashboard styles |
| `static/app.js` | Dashboard JavaScript (Leaflet map, UI logic) |

## How to Contribute

### Bug Reports

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your OS and Python version

### Feature Requests

Open an issue describing:
- The problem you're trying to solve
- Your proposed solution
- Any alternatives you considered

### Pull Requests

1. Create a branch from `main`
2. Make your changes
3. Verify imports: `uv run python -c "import main, tracker, dashboard, db"`
4. Test the dashboard manually: `uv run python main.py _serve`
5. Open a PR with a clear description of what changed and why

### Code Style

- Follow existing patterns in each file
- Use `log.info/warning/error` for logging, not `print()`
- Add input validation at system boundaries (API endpoints, CLI args)
- Use `datetime.now(timezone.utc)` for all timestamps
- Escape user-controlled data before rendering in HTML (`esc()` in app.js)

### Areas Where Help is Wanted

Check the [issues labeled `help wanted`](https://github.com/dcondrey/location-tracker/labels/help%20wanted) for specific tasks. Some general areas:

- **Cross-platform support** -- Linux (Secret Service for keychain), Windows
- **Test suite** -- Unit tests for db.py, tracker.py, cookie_store.py
- **Notifications** -- Push notifications when someone arrives/leaves a geofence
- **Data visualization** -- Speed graphs, time-at-location charts, weekly summaries
- **Multi-provider support** -- Apple Find My, Samsung SmartThings, Life360
- **Performance** -- WebSocket push updates instead of 30s polling on the frontend

## Code of Conduct

Be respectful. This is a personal project shared with the community. Treat others the way you'd want to be treated.
