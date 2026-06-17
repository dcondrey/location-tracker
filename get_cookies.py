import logging
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)

BROWSER_PROFILE = str(Path.home() / ".local" / "share" / "location-tracker" / "browser_profile")

# Multiple cookie patterns Google may use for authenticated sessions.
# Checking several guards against Google renaming any single cookie.
AUTH_COOKIE_SETS = [
    {"SID", "HSID", "SSID"},
    {"__Secure-1PSID", "__Secure-1PSIDTS"},
    {"__Secure-3PSID", "__Secure-3PSIDTS"},
]

LOGIN_URL = "https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fwww.google.com%2Fmaps"

TIMEOUT_SECONDS = 900  # 15 minutes

# Manual stealth patches to reduce automation detection signals.
STEALTH_SCRIPTS = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    window.chrome = {
        runtime: {
            onMessage: { addListener: () => {}, removeListener: () => {} },
            sendMessage: () => {},
            connect: () => ({ onMessage: { addListener: () => {} } })
        },
        loadTimes: () => ({}),
        csi: () => ({})
    };

    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );

    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5]
    });

    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en']
    });

    delete navigator.__proto__.webdriver;
}
"""


def _ensure_playwright_browser():
    """Install Playwright Chromium if not already present."""
    try:
        from playwright._impl._driver import compute_driver_executable

        driver = compute_driver_executable()
        result = subprocess.run([str(driver), "install", "--dry-run", "chromium"], capture_output=True, text=True)
        if result.returncode == 0 and "chromium" not in result.stdout.lower():
            return
    except Exception:  # noqa: S110
        pass

    log.info("Installing Chromium browser (first run only)...")
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    log.info("Chromium installed.")


def _has_auth_cookies(cookies):
    """Check if any known auth cookie pattern is present."""
    google_cookie_names = {c["name"] for c in cookies if "google" in c.get("domain", "")}
    for cookie_set in AUTH_COOKIE_SETS:
        if cookie_set.issubset(google_cookie_names):
            return True
    return False


def _write_cookies_file(cookies, path="cookies.txt"):
    """Write Google cookies in Netscape format."""
    google_cookies = [c for c in cookies if "google" in c.get("domain", "")]
    with open(path, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in google_cookies:
            domain = c.get("domain", "")
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path_val = c.get("path", "/")
            secure = "TRUE" if c.get("secure", False) else "FALSE"
            expires = int(c.get("expires", 0))
            if expires == -1:
                expires = 0
            name = c.get("name", "")
            value = c.get("value", "")
            f.write(f"{domain}\t{flag}\t{path_val}\t{secure}\t{expires}\t{name}\t{value}\n")
    return len(google_cookies)


def _validate_cookies(cookies_file="cookies.txt"):
    """Verify extracted cookies work with the location sharing API."""
    try:
        from locationsharinglib import Service

        Service(cookies_file=cookies_file, authenticating_account="test@test.com")
        return True
    except Exception:
        return False


def generate_cookies_txt():
    _ensure_playwright_browser()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=BROWSER_PROFILE,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )

        page = context.pages[0] if context.pages else context.new_page()
        context.add_init_script(STEALTH_SCRIPTS)

        log.info("")
        log.info("  A browser window will open to Google sign-in.")
        log.info("  Log in to the Google account that has location sharing.")
        log.info("  Cookies will be captured automatically once login completes.")
        log.info("")

        page.goto(LOGIN_URL)

        start = time.time()
        detected = False
        last_status = ""

        while time.time() - start < TIMEOUT_SECONDS:
            cookies = context.cookies()
            elapsed = int(time.time() - start)

            try:
                url = page.url
            except Exception:
                url = ""

            if "accounts.google.com" in url:
                status = "  Waiting for login..."
            elif "myaccount.google.com" in url or "google.com/maps" in url:
                status = "  Login detected, capturing cookies..."
            else:
                status = f"  Waiting... ({elapsed}s)"

            if status != last_status:
                log.info(status)
                last_status = status

            if _has_auth_cookies(cookies):
                detected = True
                break

            time.sleep(2)

        if not detected:
            cookies = context.cookies()
            if _has_auth_cookies(cookies):
                detected = True

        if not detected:
            log.warning("  Timed out waiting for Google login.")
            log.warning("  Please try again with: location-tracker cookies")
            context.close()
            return

        log.info("  Finalizing cookies...")
        time.sleep(3)
        cookies = context.cookies()

        count = _write_cookies_file(cookies)

        if not _validate_cookies():
            log.warning("  Saved %d cookies but validation failed.", count)
            log.warning("  Encrypting anyway. Try: location-tracker test")
        else:
            log.info("  Cookies validated successfully.")

        from cookie_store import encrypt_cookies

        encrypt_cookies("cookies.txt")
        log.info("  Saved %d cookies (encrypted).", count)
        log.info("")
        log.info("  Start tracking with: location-tracker on")

        context.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    generate_cookies_txt()
