"""Location Tracker CLI - simple on/off interface."""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
from datetime import UTC
from pathlib import Path

log = logging.getLogger(__name__)

APP_DIR = Path.home() / ".local" / "share" / "location-tracker"
CONFIG_DIR = Path.home() / ".config" / "location-tracker"
CONFIG_FILE = CONFIG_DIR / "config.json"

IS_MACOS = sys.platform == "darwin"

PID_FILE = APP_DIR / ".tracker.pid"
PF_ANCHOR = "com.locationtracker"
PF_ANCHOR_FILE = f"/etc/pf.anchors/{PF_ANCHOR}"

DEFAULTS = {
    "email": "",
    "port": 7070,
    "poll_interval": 300,
    "hostname": "tracker.local",
    "data_file": str(APP_DIR / "location_history.db"),
    "cookies_file": str(APP_DIR / "cookies.enc"),
}


def _load_config():
    """Load config from env vars, then config file, then defaults."""
    config = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                stored = json.load(f)
                for k in DEFAULTS:
                    if k in stored:
                        config[k] = stored[k]
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to read config: %s", e)
    config["email"] = os.environ.get("LOCATION_TRACKER_EMAIL", config["email"])
    if os.environ.get("LOCATION_TRACKER_PORT"):
        config["port"] = int(os.environ["LOCATION_TRACKER_PORT"])
    return config


def _save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _cfg(key):
    """Get a config value by key."""
    return _load_config()[key]


# Module-level shortcuts (read once at import; overridden by config/env)
_c = _load_config()
PORT = _c["port"]
POLL_INTERVAL = _c["poll_interval"]
HOSTNAME = _c["hostname"]
CUSTOM_URL = f"http://{_c['hostname']}"
DATA_FILE = _c["data_file"]
COOKIES_FILE = _c["cookies_file"]
del _c


def _get_email():
    config = _load_config()
    if not config["email"]:
        log.error("No email configured.")
        log.error("Set it with: location-tracker config --email you@gmail.com")
        log.error("Or set LOCATION_TRACKER_EMAIL environment variable.")
        sys.exit(1)
    return config["email"]


def _read_pid():
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return None


def _is_running():
    pid = _read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        PID_FILE.unlink(missing_ok=True)
        return False
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "args="],
        capture_output=True,
        text=True,
    )
    if "_serve" in result.stdout:
        return True
    PID_FILE.unlink(missing_ok=True)
    return False


def _kill_port_holder():
    """Detect and kill any process holding our configured port."""
    result = subprocess.run(
        ["lsof", "-ti", f":{PORT}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return
    for pid_str in result.stdout.strip().split("\n"):
        pid = int(pid_str)
        log.warning("Port %d held by PID %d. Killing...", PORT, pid)
        try:
            os.kill(pid, signal.SIGTERM)
        except PermissionError:
            subprocess.run(["sudo", "kill", pid_str])
        except OSError:
            pass


def _start():
    if _is_running():
        log.info("Already running (pid %d). Dashboard: %s", _read_pid(), CUSTOM_URL)
        return

    APP_DIR.mkdir(parents=True, exist_ok=True)

    from cookie_store import migrate_plaintext_to_encrypted

    migrate_plaintext_to_encrypted()
    _kill_port_holder()

    if IS_MACOS:
        if not _dns_is_configured():
            _dns_add()
        _pf_setup()

    log_path = APP_DIR / "tracker.log"
    if log_path.exists() and log_path.stat().st_size > 10 * 1024 * 1024:
        log_path.rename(log_path.with_suffix(".log.old"))

    env = os.environ.copy()
    cmd = [sys.executable, __file__, "_serve"]
    proc = subprocess.Popen(
        cmd,
        env=env,
        start_new_session=True,
        stdout=open(log_path, "a"),
        stderr=subprocess.STDOUT,
    )
    PID_FILE.write_text(str(proc.pid))
    log.info("Started (pid %d).", proc.pid)
    log.info("Dashboard: %s", CUSTOM_URL)
    log.info("Stop with: location-tracker off")


def _stop():
    pid = _read_pid()
    if pid is None:
        # Check if something else is holding the port
        _kill_port_holder()
        log.info("Not running.")
        return
    try:
        os.kill(pid, signal.SIGTERM)
        log.info("Stopped (pid %d).", pid)
    except OSError:
        log.info("Process already gone.")
    PID_FILE.unlink(missing_ok=True)


def _serve():
    from dashboard import run_dashboard

    run_dashboard(
        data_file=DATA_FILE,
        cookies_file=COOKIES_FILE,
        email=_get_email(),
        port=PORT,
        poll_interval=POLL_INTERVAL,
    )


def _status():
    if _is_running():
        log.info("Running (pid %d). Dashboard: %s", _read_pid(), CUSTOM_URL)
    else:
        log.info("Not running.")


def _setup():
    """Full setup: install browser, configure DNS, authenticate, start, and open dashboard."""
    import webbrowser

    APP_DIR.mkdir(parents=True, exist_ok=True)
    log.info("--- Location Tracker Setup ---")
    log.info("")

    # Step 1: Ensure email is configured
    config = _load_config()
    if not config["email"]:
        email = input("Enter your Google account email: ").strip()
        if not email:
            log.error("Email is required.")
            return
        config["email"] = email
        _save_config(config)
    log.info("  Email: %s", config["email"])
    log.info("")

    # Step 2: Install Chromium
    log.info("[1/4] Installing Chromium browser...")
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
    )
    if result.returncode != 0:
        log.error("Failed to install Chromium. Check your internet connection.")
        return
    log.info("  Chromium installed.")
    log.info("")

    # Step 3: Configure DNS
    log.info("[2/4] Configuring custom hostname '%s'...", HOSTNAME)
    _dns_add()
    log.info("")

    # Step 4: Authenticate
    log.info("[3/4] Authenticating with Google...")
    log.info("")
    from get_cookies import generate_cookies_txt

    generate_cookies_txt()
    log.info("")

    # Step 5: Start tracker and open browser
    log.info("[4/4] Starting tracker...")
    _start()
    import time
    import urllib.request

    log.info("  Waiting for dashboard...")
    for _ in range(10):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(f"http://localhost:{PORT}/", timeout=1)  # noqa: S310
            break
        except Exception:  # noqa: S110
            pass

    webbrowser.open(CUSTOM_URL)
    log.info("")
    log.info("--- Setup Complete ---")
    log.info("  Dashboard: %s", CUSTOM_URL)


def _dns_is_configured():
    """Check if the hostname is already in /etc/hosts."""
    try:
        return HOSTNAME in Path("/etc/hosts").read_text().split()
    except PermissionError:
        return False


def _dns_add():
    """Add hostname to /etc/hosts."""
    hosts_entry = f"127.0.0.1\t{HOSTNAME}"
    if _dns_is_configured():
        log.info("  Hostname '%s' already in /etc/hosts.", HOSTNAME)
        return

    log.info("  Adding '%s' to /etc/hosts (requires sudo)...", hosts_entry)
    result = subprocess.run(
        ["sudo", "tee", "-a", "/etc/hosts"],
        input=hosts_entry + "\n",
        text=True,
        capture_output=True,
    )
    if result.returncode == 0:
        log.info("  Hostname configured.")
    else:
        log.error("  Failed to update /etc/hosts. You can add manually:")
        log.error("    echo '%s' | sudo tee -a /etc/hosts", hosts_entry)


def _dns_remove():
    """Remove hostname from /etc/hosts."""
    log.info("Removing '%s' from /etc/hosts (requires sudo)...", HOSTNAME)
    subprocess.run(
        ["sudo", "sed", "-i", "", f"/127.0.0.1.*{HOSTNAME}/d", "/etc/hosts"],
    )
    log.info("  Hostname removed.")


def _pf_is_configured():
    """Check if pf port forwarding anchor is active."""
    try:
        return PF_ANCHOR in Path("/etc/pf.conf").read_text()
    except (PermissionError, FileNotFoundError):
        return False


def _pf_setup():
    """Set up pfctl to forward port 80 -> app port on loopback."""
    if _pf_is_configured():
        log.info("  Port forwarding already configured.")
        subprocess.run(["sudo", "pfctl", "-ef", "/etc/pf.conf"], capture_output=True)
        return

    anchor_rule = f"rdr pass on lo0 inet proto tcp from any to 127.0.0.1 port 80 -> 127.0.0.1 port {PORT}\n"

    log.info("  Setting up port forwarding (80 -> %d, requires sudo)...", PORT)
    subprocess.run(
        ["sudo", "tee", PF_ANCHOR_FILE],
        input=anchor_rule,
        text=True,
        capture_output=True,
    )

    rdr_line = f'rdr-anchor "{PF_ANCHOR}"'
    load_line = f'load anchor "{PF_ANCHOR}" from "{PF_ANCHOR_FILE}"'
    subprocess.run(
        ["sudo", "tee", "-a", "/etc/pf.conf"],
        input=rdr_line + "\n" + load_line + "\n",
        text=True,
        capture_output=True,
    )

    subprocess.run(["sudo", "pfctl", "-ef", "/etc/pf.conf"], capture_output=True)
    log.info("  Port forwarding configured (80 -> %d).", PORT)


def _pf_remove():
    """Remove pfctl port forwarding."""
    log.info("Removing port forwarding (requires sudo)...")
    subprocess.run(["sudo", "rm", "-f", PF_ANCHOR_FILE])
    subprocess.run(["sudo", "sed", "-i", "", f"/{PF_ANCHOR}/d", "/etc/pf.conf"])
    subprocess.run(["sudo", "pfctl", "-ef", "/etc/pf.conf"], capture_output=True)
    log.info("  Port forwarding removed.")


def _test_cookies():
    """Verify cookies work with the location sharing API."""
    from cookie_store import decrypt_to_tempfile, has_encrypted_cookies, migrate_plaintext_to_encrypted

    migrate_plaintext_to_encrypted()

    if not has_encrypted_cookies(COOKIES_FILE):
        log.error("No cookies found. Run: location-tracker cookies")
        return

    tmp_path = decrypt_to_tempfile(COOKIES_FILE)
    if not tmp_path:
        log.error("Cannot decrypt cookies. Run: location-tracker cookies")
        return

    log.info("Testing cookies...")
    try:
        from locationsharinglib import Service

        email = _get_email()
        service = Service(cookies_file=tmp_path, authenticating_account=email)
        people = service.get_all_people()
        if people:
            log.info("  Cookies are valid. Found %d shared contact(s):", len(people))
            for person in people:
                name = person.full_name or person.nickname or "Unknown"
                log.info("    - %s", name)
        else:
            log.warning("  Cookies work but no shared contacts found.")
            log.warning("  Ensure someone is sharing their location with %s", email)
    except Exception as e:
        log.error("  Cookie validation failed: %s", e)
        log.error("  Re-run: location-tracker cookies")
    finally:
        os.unlink(tmp_path)


LAUNCHD_LABEL = "com.locationtracker.daemon"
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _install_launchd():
    """Install a launchd plist so the tracker starts on login (macOS only)."""
    if not IS_MACOS:
        log.error("launchd is macOS only. On Linux, use systemd.")
        return
    project_dir = Path(__file__).parent.resolve()
    python = sys.executable
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{project_dir / "main.py"}</string>
        <string>_serve</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{APP_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{APP_DIR / "tracker.log"}</string>
    <key>StandardErrorPath</key>
    <string>{APP_DIR / "tracker.log"}</string>
</dict>
</plist>
"""
    LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHD_PLIST.write_text(plist_content)
    subprocess.run(["launchctl", "load", str(LAUNCHD_PLIST)])
    log.info("Installed launchd service: %s", LAUNCHD_LABEL)
    log.info("Tracker will start automatically on login.")
    log.info("Dashboard: %s", CUSTOM_URL)


def _uninstall_launchd():
    """Remove the launchd plist."""
    if LAUNCHD_PLIST.exists():
        subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST)])
        LAUNCHD_PLIST.unlink()
        log.info("Removed launchd service.")
    else:
        log.info("No launchd service installed.")


def cli():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(
        prog="location-tracker",
        description="Track and visualize location. Use 'on' to start, 'off' to stop.",
        epilog=(
            "Examples:\n"
            "  location-tracker config --email you@gmail.com\n"
            "  location-tracker setup\n"
            "  location-tracker cookies\n"
            "  location-tracker on\n"
            "  location-tracker map --days 7\n"
            "  location-tracker install\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("on", help="Start tracking and launch dashboard")
    subparsers.add_parser("off", help="Stop tracking")
    subparsers.add_parser("status", help="Check if tracker is running")
    subparsers.add_parser("setup", help="First-time setup (install browser, configure DNS)")
    subparsers.add_parser("cookies", help="Launch browser to acquire Google cookies")
    subparsers.add_parser("test", help="Verify cookies work with Google API")

    config_parser = subparsers.add_parser("config", help="Configure settings")
    config_parser.add_argument("--email", help="Google account email for location sharing")
    config_parser.add_argument("--port", type=int, help="Dashboard port (default: 7070)")
    config_parser.add_argument("--hostname", help="Custom hostname (default: tracker.local)")
    config_parser.add_argument(
        "--poll-interval", type=int, dest="poll_interval", help="Default poll interval in seconds"
    )

    subparsers.add_parser("install", help="Install as persistent service (survives reboot)")
    subparsers.add_parser("uninstall", help="Remove the persistent service")

    subparsers.add_parser("dns", help="Set up custom hostname (http://tracker.local)")
    subparsers.add_parser("dns-remove", help="Remove custom hostname and port forwarding")

    map_parser = subparsers.add_parser("map", help="Generate a static map file")
    map_parser.add_argument("--days", type=int, default=None)
    map_parser.add_argument("--output", default="location_map.html")

    subparsers.add_parser("stats", help="Show tracking statistics")

    where_parser = subparsers.add_parser("where", help="Show latest location for a person")
    where_parser.add_argument("person", help="Person name (or partial match)")

    history_parser = subparsers.add_parser("history", help="Show recent location history")
    history_parser.add_argument("person", help="Person name (or partial match)")
    history_parser.add_argument("--days", type=int, default=1, help="Days of history (default: 1)")

    purge_parser = subparsers.add_parser("purge", help="Delete old location data")
    purge_parser.add_argument("days", type=int, help="Delete records older than N days")

    subparsers.add_parser("_serve", help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "on":
        _start()
    elif args.command == "off":
        _stop()
    elif args.command == "status":
        _status()
    elif args.command == "setup":
        _setup()
    elif args.command == "cookies":
        from get_cookies import generate_cookies_txt

        generate_cookies_txt()
    elif args.command == "test":
        _test_cookies()
    elif args.command == "config":
        config = _load_config()
        changed = False
        for key in ("email", "port", "hostname", "poll_interval"):
            val = getattr(args, key, None)
            if val is not None:
                config[key] = val
                changed = True
                log.info("  %s set to: %s", key, val)
        if changed:
            _save_config(config)
        else:
            log.info("Config file: %s", CONFIG_FILE)
            for k, v in config.items():
                log.info("  %s: %s", k, v or "(not set)")
    elif args.command == "install":
        _install_launchd()
    elif args.command == "uninstall":
        _uninstall_launchd()
    elif args.command == "dns":
        _dns_add()
        _pf_setup()
    elif args.command == "dns-remove":
        _dns_remove()
        _pf_remove()
    elif args.command == "map":
        from tracker import LocationTracker

        tracker = LocationTracker(COOKIES_FILE, _get_email(), DATA_FILE)
        tracker.generate_map(output_file=args.output, days=args.days)
    elif args.command == "stats":
        from tracker import LocationTracker

        tracker = LocationTracker(COOKIES_FILE, _get_email(), DATA_FILE)
        tracker.print_stats()
    elif args.command == "where":
        from db import LocationDB

        db = LocationDB(DATA_FILE)
        people = db.get_people()
        match = [p for p in people if args.person.lower() in p.lower()]
        if not match:
            log.error("No person matching '%s'. Known: %s", args.person, ", ".join(people))
        for person in match:
            loc = db.get_latest(person)
            if loc:
                log.info(
                    "%s: (%.4f, %.4f) | %s | %s",
                    person,
                    loc["latitude"],
                    loc["longitude"],
                    loc.get("address", ""),
                    loc["timestamp"],
                )
            else:
                log.info("%s: no data", person)
        db.close()
    elif args.command == "history":
        from datetime import datetime, timedelta

        from db import LocationDB

        db = LocationDB(DATA_FILE)
        people = db.get_people()
        match = [p for p in people if args.person.lower() in p.lower()]
        if not match:
            log.error("No person matching '%s'. Known: %s", args.person, ", ".join(people))
        for person in match:
            since = (datetime.now(UTC) - timedelta(days=args.days)).isoformat()
            locs = db.get_locations(person=person, since=since)
            log.info("--- %s (%d points, last %d day(s)) ---", person, len(locs), args.days)
            for loc in locs[-20:]:
                log.info(
                    "  %s | (%.4f, %.4f) | %s",
                    loc["timestamp"][:19],
                    loc["latitude"],
                    loc["longitude"],
                    loc.get("address", ""),
                )
            if len(locs) > 20:
                log.info("  ... and %d more (showing last 20)", len(locs) - 20)
        db.close()
    elif args.command == "purge":
        from db import LocationDB

        db = LocationDB(DATA_FILE)
        count = db.purge_older_than(args.days)
        log.info("Purged %d records older than %d days.", count, args.days)
        db.close()
    elif args.command == "_serve":
        _serve()


if __name__ == "__main__":
    cli()
