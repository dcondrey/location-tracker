import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 4

MIGRATIONS = {
    0: """
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
    """,
    1: """
        CREATE TABLE IF NOT EXISTS health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            success INTEGER NOT NULL,
            error_type TEXT,
            error_message TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_health_timestamp ON health(timestamp);
    """,
    2: """
        CREATE TABLE IF NOT EXISTS known_places (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person TEXT NOT NULL,
            label TEXT,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            radius REAL NOT NULL DEFAULT 75.0,
            visit_count INTEGER NOT NULL DEFAULT 0,
            total_dwell_seconds REAL NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kp_person ON known_places(person);

        CREATE TABLE IF NOT EXISTS dwell_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person TEXT NOT NULL,
            place_id INTEGER REFERENCES known_places(id),
            arrived_at TEXT NOT NULL,
            departed_at TEXT,
            duration_seconds REAL,
            day_of_week INTEGER,
            hour_of_day INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_dwell_place ON dwell_observations(person, place_id);

        CREATE TABLE IF NOT EXISTS speed_zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            radius_m REAL DEFAULT 200,
            avg_speed_kmh REAL,
            observation_count INTEGER DEFAULT 1,
            last_updated TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_speed_zones_geo ON speed_zones(lat, lon);
    """,
    3: """
        CREATE TABLE IF NOT EXISTS geofences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person TEXT NOT NULL,
            label TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            radius_m REAL NOT NULL DEFAULT 200,
            on_enter INTEGER NOT NULL DEFAULT 1,
            on_exit INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_geofences_person ON geofences(person);

        CREATE TABLE IF NOT EXISTS geofence_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            geofence_id INTEGER NOT NULL REFERENCES geofences(id),
            person TEXT NOT NULL,
            event_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            acknowledged INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_gf_events_ts ON geofence_events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_gf_events_ack ON geofence_events(acknowledged);

        CREATE TABLE IF NOT EXISTS route_corridors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person TEXT NOT NULL,
            from_place_id INTEGER REFERENCES known_places(id),
            to_place_id INTEGER REFERENCES known_places(id),
            trip_count INTEGER NOT NULL DEFAULT 0,
            avg_duration_seconds REAL,
            avg_distance_m REAL,
            last_occurred TEXT,
            UNIQUE(person, from_place_id, to_place_id)
        );
        CREATE INDEX IF NOT EXISTS idx_routes_person ON route_corridors(person);

        CREATE TABLE IF NOT EXISTS route_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corridor_id INTEGER NOT NULL REFERENCES route_corridors(id),
            departed_at TEXT NOT NULL,
            arrived_at TEXT NOT NULL,
            duration_seconds REAL NOT NULL,
            distance_m REAL,
            day_of_week INTEGER,
            hour_of_day INTEGER,
            speed_profile TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_robs_corridor ON route_observations(corridor_id);
    """,
}


class LocationDB:
    def __init__(self, db_path="location_history.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._run_migrations()

    def _get_version(self):
        try:
            row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            return int(row["value"]) if row else 0
        except sqlite3.OperationalError:
            return 0

    def _set_version(self, version):
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(version),),
        )
        self.conn.commit()

    def _run_migrations(self):
        current = self._get_version()
        if current >= SCHEMA_VERSION:
            return
        for version in range(current, SCHEMA_VERSION):
            if version in MIGRATIONS:
                log.info("Running migration %d -> %d...", version, version + 1)
                self.conn.executescript(MIGRATIONS[version])
        self._set_version(SCHEMA_VERSION)
        log.info("Database at schema version %d.", SCHEMA_VERSION)

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

    def record_poll(self, success, error_type=None, error_message=None):
        self.conn.execute(
            "INSERT INTO health (timestamp, success, error_type, error_message) VALUES (?, ?, ?, ?)",
            (datetime.now(UTC).isoformat(), 1 if success else 0, error_type, error_message),
        )
        self.conn.commit()

    def get_health(self):
        last_success = self.conn.execute(
            "SELECT timestamp FROM health WHERE success = 1 ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        recent = self.conn.execute(
            "SELECT success, error_type, error_message, timestamp FROM health ORDER BY id DESC LIMIT 20"
        ).fetchall()
        recent_list = [dict(r) for r in recent]
        consecutive_failures = 0
        for r in recent_list:
            if r["success"]:
                break
            consecutive_failures += 1
        return {
            "last_success": last_success["timestamp"] if last_success else None,
            "consecutive_failures": consecutive_failures,
            "recent": recent_list[:5],
        }

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

    def add_geofence(self, person, label, latitude, longitude, radius_m=200, on_enter=True, on_exit=True):
        self.conn.execute(
            "INSERT INTO geofences "
            "(person, label, latitude, longitude, radius_m, on_enter, on_exit, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (person, label, latitude, longitude, radius_m, int(on_enter), int(on_exit), datetime.now(UTC).isoformat()),
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_geofences(self, person=None):
        if person:
            rows = self.conn.execute("SELECT * FROM geofences WHERE person=? AND active=1", (person,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM geofences WHERE active=1").fetchall()
        return [dict(r) for r in rows]

    def remove_geofence(self, geofence_id):
        self.conn.execute("UPDATE geofences SET active=0 WHERE id=?", (geofence_id,))
        self.conn.commit()

    def record_geofence_event(self, geofence_id, person, event_type, timestamp, latitude, longitude):
        self.conn.execute(
            "INSERT INTO geofence_events (geofence_id, person, event_type, timestamp, latitude, longitude) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (geofence_id, person, event_type, timestamp, latitude, longitude),
        )
        self.conn.commit()

    def get_geofence_events(self, unacknowledged_only=False, limit=50):
        query = "SELECT e.*, g.label FROM geofence_events e JOIN geofences g ON e.geofence_id=g.id"
        if unacknowledged_only:
            query += " WHERE e.acknowledged=0"
        query += " ORDER BY e.timestamp DESC LIMIT ?"
        return [dict(r) for r in self.conn.execute(query, (limit,)).fetchall()]

    def acknowledge_geofence_events(self):
        self.conn.execute("UPDATE geofence_events SET acknowledged=1 WHERE acknowledged=0")
        self.conn.commit()

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
