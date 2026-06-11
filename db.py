import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    accuracy REAL,
    battery INTEGER,
    charging INTEGER,
    address TEXT
);
CREATE INDEX IF NOT EXISTS idx_person ON locations(person);
CREATE INDEX IF NOT EXISTS idx_timestamp ON locations(timestamp);
CREATE INDEX IF NOT EXISTS idx_person_timestamp ON locations(person, timestamp);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class LocationDB:
    def __init__(self, db_path="location_history.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(SCHEMA)
        row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            self.conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            self.conn.commit()

    def add_location(
        self, person, timestamp, latitude, longitude, accuracy=None, battery=None, charging=None, address=None
    ):
        self.conn.execute(
            "INSERT INTO locations (person, timestamp, latitude, longitude, "
            "accuracy, battery, charging, address) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                person,
                timestamp,
                latitude,
                longitude,
                accuracy,
                battery,
                1 if charging else 0 if charging is not None else None,
                address,
            ),
        )
        self.conn.commit()

    def get_people(self):
        rows = self.conn.execute("SELECT DISTINCT person FROM locations ORDER BY person").fetchall()
        return [r["person"] for r in rows]

    def get_locations(self, person=None, since=None):
        query = "SELECT * FROM locations WHERE 1=1"
        params = []
        if person:
            query += " AND person = ?"
            params.append(person)
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        query += " ORDER BY timestamp"
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def get_history_dict(self, since=None):
        locations = self.get_locations(since=since)
        result = {}
        for loc in locations:
            person = loc.pop("person")
            loc.pop("id", None)
            if loc["charging"] is not None:
                loc["charging"] = bool(loc["charging"])
            if person not in result:
                result[person] = []
            result[person].append(loc)
        return result

    def get_total_points(self):
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM locations").fetchone()
        return row["cnt"]

    def get_latest(self, person):
        row = self.conn.execute(
            "SELECT * FROM locations WHERE person = ? ORDER BY timestamp DESC LIMIT 1", (person,)
        ).fetchone()
        return dict(row) if row else None

    def purge_older_than(self, days):
        from datetime import timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        result = self.conn.execute("DELETE FROM locations WHERE timestamp < ?", (cutoff,))
        self.conn.commit()
        count = result.rowcount
        if count > 0:
            self.conn.execute("VACUUM")
            log.info("Purged %d records older than %d days.", count, days)
        return count

    def close(self):
        self.conn.close()

    def import_from_json(self, json_path):
        json_path = Path(json_path)
        if not json_path.exists():
            return 0
        with open(json_path) as f:
            history = json.load(f)
        count = 0
        for person, locations in history.items():
            for loc in locations:
                self.conn.execute(
                    "INSERT INTO locations (person, timestamp, latitude, longitude, "
                    "accuracy, battery, charging, address) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        person,
                        loc["timestamp"],
                        loc["latitude"],
                        loc["longitude"],
                        loc.get("accuracy"),
                        loc.get("battery"),
                        1 if loc.get("charging") else 0 if loc.get("charging") is not None else None,
                        loc.get("address"),
                    ),
                )
                count += 1
        self.conn.commit()
        log.info("Imported %d location records from %s", count, json_path)
        return count
