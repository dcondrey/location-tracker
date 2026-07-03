"""End-to-end browser test of the dashboard UI.

Runs fully offline: all non-localhost requests are blocked, so this also
guards that Leaflet is vendored locally (no CDN dependency). Skips cleanly
when Playwright's browser is not installed, so the default suite stays green;
a dedicated CI job installs Chromium and exercises it for real.
"""

import socket
import threading
import time

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from dashboard import create_app  # noqa: E402
from db import LocationDB  # noqa: E402


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from waitress.server import create_server

    tmp = tmp_path_factory.mktemp("e2e")
    db_path = str(tmp / "e2e.db")
    db = LocationDB(db_path)
    base = time.time()
    for n, person in enumerate(("Alice", "Bob")):
        for i in range(6):
            ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base - (6 - i) * 300))
            db.add_location(
                person,
                ts,
                32.70 + n * 0.05 + i * 0.004,
                -117.10 - i * 0.004,
                accuracy=12 if i == 5 else None,
                battery=80 if i == 5 else None,
                address=f"{person} {i}",
            )
    db.close()

    app = create_app(
        data_file=db_path,
        cookies_file=str(tmp / "none.enc"),
        email="t@t.com",
        poll_interval=300,
        start_poll=False,
    )
    port = _free_port()
    srv = create_server(app, host="127.0.0.1", port=port)
    # daemon thread is reaped at process exit; not calling srv.close() avoids a
    # benign "Bad file descriptor" race against the polling thread.
    threading.Thread(target=srv.run, daemon=True).start()
    time.sleep(0.6)
    yield f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def browser():
    try:
        pw = sync_playwright().start()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Playwright runtime unavailable: {e}")
    try:
        b = pw.chromium.launch(headless=True)
    except Exception as e:
        pw.stop()
        pytest.skip(f"Chromium not installed (run: playwright install chromium): {e}")
    yield b
    b.close()
    pw.stop()


@pytest.fixture
def page(server, browser):
    context = browser.new_context()
    # Block anything that is not our own origin: proves no external CDN is needed.
    context.route(
        "**/*",
        lambda route: (
            route.abort()
            if "127.0.0.1" not in route.request.url and "localhost" not in route.request.url
            else route.continue_()
        ),
    )
    p = context.new_page()
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p._collected_errors = errors
    p.goto(server + "/", wait_until="domcontentloaded", timeout=20000)
    p.wait_for_selector(".person-card", timeout=10000)
    p.wait_for_selector(".stat-card", timeout=10000)
    yield p
    context.close()


def test_dashboard_renders_offline(page):
    assert page.locator(".person-card").count() == 2
    assert page.locator(".stat-card").count() >= 4
    assert page.locator(".leaflet-container").count() == 1
    assert page.evaluate("typeof window.L !== 'undefined'")
    assert page._collected_errors == []


def test_view_modes_switch(page):
    page.click('.view-toggle button[data-view="heatmap"]')
    page.wait_for_timeout(400)
    assert page.locator(".leaflet-overlay-pane canvas").count() == 1
    page.click('.view-toggle button[data-view="points"]')
    page.wait_for_timeout(300)
    assert page.locator('.view-toggle button[data-view="points"].active').count() == 1
    page.click('.view-toggle button[data-view="path"]')
    page.wait_for_timeout(300)
    assert page._collected_errors == []


def test_keyboard_activation(page):
    pill = page.locator('.map-pill[data-layer="satellite"]')
    assert pill.get_attribute("role") == "button"
    assert pill.get_attribute("tabindex") == "0"
    pill.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    assert page.locator('.map-pill[data-layer="satellite"].active').count() == 1
    card = page.locator(".person-card").first
    assert card.get_attribute("role") == "button"
    card.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    assert page._collected_errors == []
