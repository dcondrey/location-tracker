import base64
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

KEYCHAIN_ACCOUNT = "location-tracker"
KEYCHAIN_SERVICE = "cookie-encryption-key"
ENCRYPTED_FILE = "cookies.enc"


def _keychain_get():
    """Retrieve the encryption key from macOS Keychain."""
    result = subprocess.run(
        ["security", "find-generic-password",
         "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip().encode()
    return None


def _keychain_set(key):
    """Store the encryption key in macOS Keychain."""
    subprocess.run(
        ["security", "add-generic-password",
         "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE,
         "-w", key.decode(), "-U"],
        capture_output=True,
    )


def _get_or_create_key():
    """Get existing key from Keychain or generate and store a new one."""
    key = _keychain_get()
    if key:
        return key
    key = Fernet.generate_key()
    _keychain_set(key)
    log.info("Generated new encryption key and stored in Keychain.")
    return key


def encrypt_cookies(plaintext_path, encrypted_path=ENCRYPTED_FILE):
    """Encrypt a plaintext cookies file and save to encrypted_path."""
    key = _get_or_create_key()
    fernet = Fernet(key)
    with open(plaintext_path, "rb") as f:
        data = f.read()
    encrypted = fernet.encrypt(data)
    with open(encrypted_path, "wb") as f:
        f.write(encrypted)
    os.unlink(plaintext_path)
    log.info("Cookies encrypted and stored in %s. Plaintext removed.", encrypted_path)


def decrypt_to_tempfile(encrypted_path=ENCRYPTED_FILE):
    """Decrypt cookies to a temporary file. Caller must delete when done."""
    if not Path(encrypted_path).exists():
        return None
    key = _keychain_get()
    if not key:
        log.error("No encryption key found in Keychain. Re-run: location-tracker cookies")
        return None
    fernet = Fernet(key)
    try:
        with open(encrypted_path, "rb") as f:
            encrypted = f.read()
        plaintext = fernet.decrypt(encrypted)
    except InvalidToken:
        log.error("Failed to decrypt cookies (key mismatch). Re-run: location-tracker cookies")
        return None
    fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="cookies_")
    with os.fdopen(fd, "wb") as f:
        f.write(plaintext)
    return tmp_path


def has_encrypted_cookies(encrypted_path=ENCRYPTED_FILE):
    """Check if encrypted cookies file exists."""
    return Path(encrypted_path).exists()


def has_plaintext_cookies(plaintext_path="cookies.txt"):
    """Check if legacy plaintext cookies exist (for migration)."""
    return Path(plaintext_path).exists()


def migrate_plaintext_to_encrypted(plaintext_path="cookies.txt", encrypted_path=ENCRYPTED_FILE):
    """Migrate existing plaintext cookies.txt to encrypted format."""
    if has_plaintext_cookies(plaintext_path) and not has_encrypted_cookies(encrypted_path):
        log.info("Migrating plaintext cookies to encrypted storage...")
        encrypt_cookies(plaintext_path, encrypted_path)
        return True
    return False
