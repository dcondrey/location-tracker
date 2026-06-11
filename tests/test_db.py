import pytest
import sqlite3
import tempfile
from location_tracker.db import DB

@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = f"{tmpdir}/test.db"
        db = DB(db_path)
        yield db
        db.close()

def test_db_create_table(db):
    db.create_table()
    cursor = db.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='locations';")
    assert cursor.fetchone() is not None

def test_db_insert(db):
    db.create_table()
    db.insert_location("test_user", 37.7749, -122.4194)
    cursor = db.conn.cursor()
    cursor.execute("SELECT * FROM locations WHERE user='test_user';")
    assert cursor.fetchone() is not None

def test_db_get_locations(db):
    db.create_table()
    db.insert_location("test_user", 37.7749, -122.4194)
    locations = db.get_locations("test_user")
    assert len(locations) == 1

def test_db_purge(db):
    db.create_table()
    db.insert_location("test_user", 37.7749, -122.4194)
    db.purge(0)
    cursor = db.conn.cursor()
    cursor.execute("SELECT * FROM locations WHERE user='test_user';")
    assert cursor.fetchone() is None

def test_db_migration(db):
    db.create_table()
    db.migrate()
    cursor = db.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='locations';")
    assert cursor.fetchone() is not None

def test_db_edge_cases(db):
    db.create_table()
    db.insert_location("test_user", 37.7749, -122.4194)
    db.insert_location("test_user", 37.7749, -122.4194)
    locations = db.get_locations("test_user")
    assert len(locations) == 2
    db.purge(0)
    locations = db.get_locations("test_user")
    assert len(locations) == 0