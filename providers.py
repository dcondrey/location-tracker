"""Location provider abstraction layer.

Decouples the tracker from any specific location sharing service.
To add a new provider, subclass LocationProvider and implement get_locations().
"""

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

log = logging.getLogger(__name__)


@dataclass
class PersonLocation:
    """Normalized location data from any provider."""

    person_id: str
    latitude: float
    longitude: float
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    accuracy: float | None = None
    battery: int | None = None
    charging: bool | None = None
    address: str = "Unknown Address"


class LocationProvider(ABC):
    """Base class for location data providers."""

    name: str = "unknown"

    @abstractmethod
    def get_locations(self) -> list[PersonLocation]:
        """Fetch current locations for all tracked people."""

    def needs_auth(self) -> bool:
        """Return True if credentials are missing or expired."""
        return False

    def auth_instructions(self) -> str:
        """Return user-facing instructions for authenticating."""
        return "Authentication required."


class GoogleLocationProvider(LocationProvider):
    """Provider for Google Maps location sharing via locationsharinglib."""

    name = "google"

    def __init__(self, cookies_file: str, email: str):
        self.cookies_file = cookies_file
        self.email = email

    def get_locations(self) -> list[PersonLocation]:
        from locationsharinglib import InvalidCookies, InvalidData, Service

        from cookie_store import decrypt_to_tempfile, has_encrypted_cookies

        if has_encrypted_cookies(self.cookies_file):
            tmp_path = decrypt_to_tempfile(self.cookies_file)
            if not tmp_path:
                raise ProviderAuthError("Cannot decrypt cookies.")
            try:
                service = Service(cookies_file=tmp_path, authenticating_account=self.email)
            finally:
                os.unlink(tmp_path)
        else:
            service = Service(cookies_file=self.cookies_file, authenticating_account=self.email)

        try:
            people = service.get_all_people()
        except InvalidCookies as e:
            raise ProviderAuthError(str(e)) from e
        except InvalidData as e:
            raise ProviderError(str(e)) from e

        results = []
        for person in people:
            results.append(
                PersonLocation(
                    person_id=person.full_name or person.email or "Unknown",
                    latitude=person.latitude,
                    longitude=person.longitude,
                    accuracy=getattr(person, "accuracy", None),
                    battery=getattr(person, "battery_level", None),
                    charging=getattr(person, "charging", None),
                    address=getattr(person, "address", None) or "Unknown Address",
                )
            )
        return results

    def needs_auth(self) -> bool:
        from cookie_store import has_encrypted_cookies

        return not has_encrypted_cookies(self.cookies_file)

    def auth_instructions(self) -> str:
        return "Run: location-tracker cookies"

    def try_refresh(self) -> bool:
        """Attempt headless cookie refresh using persistent browser profile."""
        try:
            from playwright.sync_api import sync_playwright

            from cookie_store import encrypt_cookies
            from get_cookies import STEALTH_SCRIPTS, _has_auth_cookies, _write_cookies_file

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir="./browser_profile",
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context.add_init_script(STEALTH_SCRIPTS)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://www.google.com/maps", wait_until="networkidle", timeout=30000)
                cookies = context.cookies()
                context.close()

                if _has_auth_cookies(cookies):
                    _write_cookies_file(cookies)
                    encrypt_cookies("cookies.txt", self.cookies_file)
                    return True
            return False
        except Exception as e:
            log.warning("Cookie refresh failed: %s", e)
            return False


class ProviderAuthError(Exception):
    """Raised when provider credentials are missing or expired."""


class ProviderError(Exception):
    """Raised for transient provider errors (network, API)."""
