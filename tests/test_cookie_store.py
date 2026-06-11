import pytest
import tempfile
from location_tracker.cookie_store import CookieStore

@pytest.fixture
def cookie_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        cookie_store = CookieStore(f"{tmpdir}/cookies.json")
        yield cookie_store

def test_cookie_store_encrypt_decrypt(cookie_store):
    cookie_store.encrypt("test_cookie")
    decrypted_cookie = cookie_store.decrypt()
    assert decrypted_cookie == "test_cookie"

def test_cookie_store_missing_keychain_key(cookie_store):
    with pytest.raises(Exception):
        cookie_store.encrypt("test_cookie")

def test_cookie_store_corrupted_file(cookie_store):
    with open(cookie_store.cookie_file, "w") as f:
        f.write("corrupted_data")
    with pytest.raises(Exception):
        cookie_store.decrypt()