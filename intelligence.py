"""Learning engine for adaptive polling.

Clusters stops into known places, learns dwell patterns and departure times,
detects speed zones (turns/intersections), and predicts optimal poll intervals.
All predictions require multiple observations and use exponential decay weighting.
"""

import logging
import math
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

# Clustering
PLACE_MERGE_RADIUS_M = 100
MIN_VISITS_FOR_PLACE = 2

# Decay
OBSERVATION_RETENTION_DAYS = 90
RECENCY_HALF_LIFE_DAYS = 14

# Predictions
MIN_OBSERVATIONS = 3
TIME_WINDOW_HOURS = 2
DAY_WINDOW = 1

# Polling
MIN_POLL_INTERVAL = 4
MAX_POLL_INTERVAL = 600


def _haversine_m(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


def _bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y)) % 360


def _recency_weight(timestamp_str):
    try:
        ts = datetime.fromisoformat(timestamp_str)
        age_days = (datetime.now(UTC) - ts).total_seconds() / 86400
    except (ValueError, TypeError):
        return 0.5
    return 2 ** (-age_days / RECENCY_HALF_LIFE_DAYS)


def _target_spacing(speed_kmh):
    if speed_kmh < 1:
        return 0
    if speed_kmh < 8:
        return 20
    if speed_kmh < 35:
        return 50
    if speed_kmh < 70:
        return 100
    return 150


def _battery_multiplier(battery, charging):
    if battery is None or charging:
        return 1.0
    if battery > 30:
        return 1.0
    if battery > 15:
        return 1.5
    if battery > 5:
        return 2.5
    return 5.0


class Intelligence:
    def __init__(self, conn):
        self.conn = conn

    def find_nearest_place(self, person, lat, lon):
        rows = self.conn.execute("SELECT * FROM known_places WHERE person = ?", (person,)).fetchall()
        best, best_dist = None, PLACE_MERGE_RADIUS_M
        for row in rows:
            d = _haversine_m(lat, lon, row["latitude"], row["longitude"])
            if d < best_dist:
                best_dist = d
                best = dict(row)
        return best

    def cluster_stop(self, person, lat, lon, arrived_at, departed_at=None):
        now = datetime.now(UTC).isoformat()
        existing = self.find_nearest_place(person, lat, lon)

        if existing:
            pid = existing["id"]
            n = existing["visit_count"]
            new_lat = (existing["latitude"] * n + lat) / (n + 1)
            new_lon = (existing["longitude"] * n + lon) / (n + 1)
            dist = _haversine_m(lat, lon, existing["latitude"], existing["longitude"])
            new_radius = max(existing["radius"], dist + 25)
            dwell = 0.0
            if departed_at:
                try:
                    t1 = datetime.fromisoformat(arrived_at)
                    t2 = datetime.fromisoformat(departed_at)
                    dwell = (t2 - t1).total_seconds()
                except (ValueError, TypeError):
                    pass
            self.conn.execute(
                "UPDATE known_places SET latitude=?, longitude=?, radius=?, "
                "visit_count=visit_count+1, total_dwell_seconds=total_dwell_seconds+?, "
                "last_seen=? WHERE id=?",
                (new_lat, new_lon, new_radius, dwell, now, pid),
            )
            self.conn.commit()
            return pid

        self.conn.execute(
            "INSERT INTO known_places (person, latitude, longitude, radius, "
            "visit_count, total_dwell_seconds, first_seen, last_seen) "
            "VALUES (?, ?, ?, 75.0, 1, 0, ?, ?)",
            (person, lat, lon, now, now),
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def record_arrival(self, person, place_id, arrived_at):
        try:
            ts = datetime.fromisoformat(arrived_at)
            dow = ts.weekday()
            hour = ts.hour
        except (ValueError, TypeError):
            dow, hour = 0, 0
        self.conn.execute(
            "INSERT INTO dwell_observations (person, place_id, arrived_at, day_of_week, hour_of_day) "
            "VALUES (?, ?, ?, ?, ?)",
            (person, place_id, arrived_at, dow, hour),
        )
        self.conn.commit()

    def record_departure(self, person, place_id, departed_at):
        row = self.conn.execute(
            "SELECT id, arrived_at FROM dwell_observations "
            "WHERE person=? AND place_id=? AND departed_at IS NULL "
            "ORDER BY arrived_at DESC LIMIT 1",
            (person, place_id),
        ).fetchone()
        if not row:
            return
        try:
            t1 = datetime.fromisoformat(row["arrived_at"])
            t2 = datetime.fromisoformat(departed_at)
            duration = (t2 - t1).total_seconds()
        except (ValueError, TypeError):
            duration = 0
        self.conn.execute(
            "UPDATE dwell_observations SET departed_at=?, duration_seconds=? WHERE id=?",
            (departed_at, duration, row["id"]),
        )
        self.conn.execute(
            "UPDATE known_places SET total_dwell_seconds=total_dwell_seconds+?, last_seen=? WHERE id=?",
            (duration, departed_at, place_id),
        )
        self.conn.commit()

    def predict_dwell_remaining(self, person, place_id, current_dwell_secs, day_of_week, hour):
        rows = self.conn.execute(
            "SELECT duration_seconds, departed_at FROM dwell_observations "
            "WHERE person=? AND place_id=? AND duration_seconds IS NOT NULL "
            "AND abs(day_of_week - ?) <= ? "
            "ORDER BY departed_at DESC LIMIT 30",
            (person, place_id, day_of_week, DAY_WINDOW),
        ).fetchall()

        if len(rows) < MIN_OBSERVATIONS:
            rows = self.conn.execute(
                "SELECT duration_seconds, departed_at FROM dwell_observations "
                "WHERE person=? AND place_id=? AND duration_seconds IS NOT NULL "
                "ORDER BY departed_at DESC LIMIT 30",
                (person, place_id),
            ).fetchall()

        if len(rows) < MIN_OBSERVATIONS:
            return None

        weighted_durations = []
        for r in rows:
            w = _recency_weight(r["departed_at"])
            weighted_durations.append((r["duration_seconds"], w))

        total_weight = sum(w for _, w in weighted_durations)
        if total_weight == 0:
            return None
        predicted = sum(d * w for d, w in weighted_durations) / total_weight
        return max(0, predicted - current_dwell_secs)

    def departure_probability(self, person, lat, lon, now):
        place = self.find_nearest_place(person, lat, lon)
        if not place:
            return 0.0

        dow = now.weekday()
        current_hour = now.hour + now.minute / 60.0
        window_start = current_hour
        window_end = current_hour + 10 / 60

        rows = self.conn.execute(
            "SELECT departed_at FROM dwell_observations "
            "WHERE person=? AND place_id=? AND departed_at IS NOT NULL "
            "AND abs(day_of_week - ?) <= ? "
            "ORDER BY departed_at DESC LIMIT 30",
            (person, place["id"], dow, DAY_WINDOW),
        ).fetchall()

        if len(rows) < MIN_OBSERVATIONS:
            return 0.0

        in_window = 0
        for r in rows:
            try:
                dep = datetime.fromisoformat(r["departed_at"])
                dep_hour = dep.hour + dep.minute / 60.0
                if window_start <= dep_hour <= window_end:
                    in_window += 1
            except (ValueError, TypeError):
                continue

        return in_window / len(rows)

    def record_speed_zone(self, lat, lon, speed_kmh):
        now = datetime.now(UTC).isoformat()
        rows = self.conn.execute(
            "SELECT id, avg_speed_kmh, observation_count FROM speed_zones "
            "WHERE abs(lat - ?) < 0.002 AND abs(lon - ?) < 0.003",
            (lat, lon),
        ).fetchall()

        existing = dict(rows[0]) if rows else None

        if existing:
            n = existing["observation_count"]
            new_avg = (existing["avg_speed_kmh"] * n + speed_kmh) / (n + 1)
            self.conn.execute(
                "UPDATE speed_zones SET avg_speed_kmh=?, observation_count=observation_count+1, "
                "last_updated=? WHERE id=?",
                (new_avg, now, existing["id"]),
            )
        else:
            self.conn.execute(
                "INSERT INTO speed_zones (lat, lon, avg_speed_kmh, observation_count, last_updated) "
                "VALUES (?, ?, ?, 1, ?)",
                (lat, lon, speed_kmh, now),
            )
        self.conn.commit()

    def near_speed_zone(self, points):
        if len(points) < 2:
            return False
        curr = points[-1]
        prev = points[-2]
        brng = _bearing(prev["latitude"], prev["longitude"], curr["latitude"], curr["longitude"])

        rows = self.conn.execute(
            "SELECT lat, lon FROM speed_zones "
            "WHERE abs(lat - ?) < 0.005 AND abs(lon - ?) < 0.007 AND observation_count >= ?",
            (curr["latitude"], curr["longitude"], MIN_OBSERVATIONS),
        ).fetchall()

        for zone in rows:
            d = _haversine_m(curr["latitude"], curr["longitude"], zone["lat"], zone["lon"])
            if d > 500:
                continue
            brng_to_zone = _bearing(curr["latitude"], curr["longitude"], zone["lat"], zone["lon"])
            angle_diff = abs(brng - brng_to_zone) % 360
            if angle_diff > 180:
                angle_diff = 360 - angle_diff
            if angle_diff < 60:
                return True
        return False

    def learn_speed_zones_from_trip(self, points):
        if len(points) < 3:
            return
        speeds = []
        for i in range(1, len(points)):
            d = _haversine_m(
                points[i - 1]["latitude"],
                points[i - 1]["longitude"],
                points[i]["latitude"],
                points[i]["longitude"],
            )
            try:
                t1 = datetime.fromisoformat(points[i - 1]["timestamp"])
                t2 = datetime.fromisoformat(points[i]["timestamp"])
                dt = (t2 - t1).total_seconds()
            except (ValueError, TypeError):
                speeds.append(0)
                continue
            speeds.append((d / dt * 3.6) if dt > 0 else 0)

        for i in range(1, len(speeds)):
            if speeds[i - 1] > 30 and speeds[i] < 15:
                self.record_speed_zone(points[i]["latitude"], points[i]["longitude"], speeds[i])

    def check_geofences(self, db, person, lat, lon, timestamp, person_fence_state):
        """Check all active geofences for a person. Returns list of triggered events."""
        fences = db.get_geofences(person)
        events = []
        for fence in fences:
            dist = _haversine_m(lat, lon, fence["latitude"], fence["longitude"])
            inside = dist <= fence["radius_m"]
            prev_inside = person_fence_state.get(fence["id"])

            if prev_inside is not None and prev_inside != inside:
                event_type = "enter" if inside else "exit"
                if (event_type == "enter" and fence["on_enter"]) or (event_type == "exit" and fence["on_exit"]):
                    db.record_geofence_event(fence["id"], person, event_type, timestamp, lat, lon)
                    events.append({"fence": fence["label"], "type": event_type, "person": person})
                    log.info("Geofence %s: %s %s %s", event_type, person, fence["label"], timestamp)

            person_fence_state[fence["id"]] = inside
        return events

    def record_route(self, person, from_place_id, to_place_id, departed_at, arrived_at, points):
        """Record a completed trip between two known places."""
        try:
            t1 = datetime.fromisoformat(departed_at)
            t2 = datetime.fromisoformat(arrived_at)
            duration = (t2 - t1).total_seconds()
        except (ValueError, TypeError):
            return

        total_dist = 0
        speeds = []
        for i in range(1, len(points)):
            d = _haversine_m(
                points[i - 1]["latitude"],
                points[i - 1]["longitude"],
                points[i]["latitude"],
                points[i]["longitude"],
            )
            total_dist += d
            try:
                pt1 = datetime.fromisoformat(points[i - 1]["timestamp"])
                pt2 = datetime.fromisoformat(points[i]["timestamp"])
                dt = (pt2 - pt1).total_seconds()
                speeds.append(round((d / dt * 3.6) if dt > 0 else 0, 1))
            except (ValueError, TypeError):
                speeds.append(0)

        now = datetime.now(UTC).isoformat()
        dow = t1.weekday()
        hour = t1.hour

        row = self.conn.execute(
            "SELECT id, trip_count, avg_duration_seconds, avg_distance_m FROM route_corridors "
            "WHERE person=? AND from_place_id=? AND to_place_id=?",
            (person, from_place_id, to_place_id),
        ).fetchone()

        if row:
            cid = row["id"]
            n = row["trip_count"]
            new_dur = (row["avg_duration_seconds"] * n + duration) / (n + 1)
            new_dist = ((row["avg_distance_m"] or 0) * n + total_dist) / (n + 1)
            self.conn.execute(
                "UPDATE route_corridors SET trip_count=?, avg_duration_seconds=?, "
                "avg_distance_m=?, last_occurred=? WHERE id=?",
                (n + 1, new_dur, new_dist, now, cid),
            )
        else:
            self.conn.execute(
                "INSERT INTO route_corridors "
                "(person, from_place_id, to_place_id, trip_count, "
                "avg_duration_seconds, avg_distance_m, last_occurred) "
                "VALUES (?, ?, ?, 1, ?, ?, ?)",
                (person, from_place_id, to_place_id, duration, total_dist, now),
            )
            cid = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        import json as _json

        self.conn.execute(
            "INSERT INTO route_observations "
            "(corridor_id, departed_at, arrived_at, duration_seconds, "
            "distance_m, day_of_week, hour_of_day, speed_profile) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, departed_at, arrived_at, duration, total_dist, dow, hour, _json.dumps(speeds[-20:])),
        )
        self.conn.commit()

    def predict_route_duration(self, person, from_place_id, to_place_id, day_of_week, hour):
        """Predict trip duration for a known route using time-of-day weighted history."""
        rows = self.conn.execute(
            "SELECT ro.duration_seconds, ro.arrived_at FROM route_observations ro "
            "JOIN route_corridors rc ON ro.corridor_id=rc.id "
            "WHERE rc.person=? AND rc.from_place_id=? AND rc.to_place_id=? "
            "ORDER BY ro.arrived_at DESC LIMIT 20",
            (person, from_place_id, to_place_id),
        ).fetchall()

        if len(rows) < MIN_OBSERVATIONS:
            return None

        weighted = []
        for r in rows:
            w = _recency_weight(r["arrived_at"])
            weighted.append((r["duration_seconds"], w))

        total_w = sum(w for _, w in weighted)
        if total_w == 0:
            return None
        return sum(d * w for d, w in weighted) / total_w

    def get_likely_destination(self, person, from_place_id):
        """Predict most likely destination when departing from a place."""
        rows = self.conn.execute(
            "SELECT to_place_id, trip_count, last_occurred FROM route_corridors "
            "WHERE person=? AND from_place_id=? ORDER BY trip_count DESC LIMIT 5",
            (person, from_place_id),
        ).fetchall()

        if not rows:
            return None

        best, best_score = None, 0
        for r in rows:
            score = r["trip_count"] * _recency_weight(r["last_occurred"])
            if score > best_score:
                best_score = score
                best = r["to_place_id"]
        return best

    def decay_old_observations(self):
        cutoff = (datetime.now(UTC) - timedelta(days=OBSERVATION_RETENTION_DAYS)).isoformat()
        self.conn.execute("DELETE FROM dwell_observations WHERE departed_at < ?", (cutoff,))
        self.conn.execute("DELETE FROM speed_zones WHERE last_updated < ?", (cutoff,))
        self.conn.commit()

    def backfill_from_locations(self, person, locations):
        if not locations or len(locations) < 2:
            return

        in_stop = True
        stop_points = [locations[0]]

        for i in range(1, len(locations)):
            curr = locations[i]
            prev = locations[i - 1]
            d = _haversine_m(prev["latitude"], prev["longitude"], curr["latitude"], curr["longitude"])

            if d < 25:
                if in_stop:
                    stop_points.append(curr)
                else:
                    in_stop = True
                    stop_points = [curr]
            else:
                if in_stop and len(stop_points) >= MIN_VISITS_FOR_PLACE:
                    avg_lat = sum(p["latitude"] for p in stop_points) / len(stop_points)
                    avg_lon = sum(p["longitude"] for p in stop_points) / len(stop_points)
                    place_id = self.cluster_stop(
                        person,
                        avg_lat,
                        avg_lon,
                        stop_points[0]["timestamp"],
                        stop_points[-1]["timestamp"],
                    )
                    self.record_arrival(person, place_id, stop_points[0]["timestamp"])
                    self.record_departure(person, place_id, stop_points[-1]["timestamp"])
                in_stop = False
                stop_points = []

        if in_stop and len(stop_points) >= MIN_VISITS_FOR_PLACE:
            avg_lat = sum(p["latitude"] for p in stop_points) / len(stop_points)
            avg_lon = sum(p["longitude"] for p in stop_points) / len(stop_points)
            place_id = self.cluster_stop(
                person,
                avg_lat,
                avg_lon,
                stop_points[0]["timestamp"],
                stop_points[-1]["timestamp"],
            )
            self.record_arrival(person, place_id, stop_points[0]["timestamp"])

        log.info("Backfilled %s: %d locations processed.", person, len(locations))


def compute_poll_interval(intelligence, person, lat, lon, speed_kmh, trend, stationary_secs, battery, charging):
    """Compute optimal poll interval using distance-based spacing + learned patterns."""

    # MOVING
    if speed_kmh >= 1.0:
        spacing = _target_spacing(speed_kmh)
        speed_ms = speed_kmh / 3.6
        interval = max(MIN_POLL_INTERVAL, min(spacing / speed_ms, 25.0))

        if trend == "accelerating" and speed_kmh < 15:
            interval = min(interval, 6.0)
            reason = "departing"
        elif trend == "decelerating" and speed_kmh < 20:
            interval = min(interval, 5.0)
            reason = "arriving"
        else:
            reason = f"{speed_kmh:.0f}km/h, {spacing:.0f}m spacing"

        interval *= _battery_multiplier(battery, charging)
        return max(MIN_POLL_INTERVAL, interval), reason

    # STATIONARY
    place = intelligence.find_nearest_place(person, lat, lon) if intelligence else None

    if place and place["visit_count"] >= MIN_OBSERVATIONS:
        now = datetime.now(UTC)
        predicted = intelligence.predict_dwell_remaining(person, place["id"], stationary_secs, now.weekday(), now.hour)
        if predicted is not None and predicted > 0:
            interval = max(30, min(predicted / 4, MAX_POLL_INTERVAL))
            if predicted < 300:
                interval = min(interval, 30)
            reason = f"at known place, ~{int(predicted)}s remaining"
        else:
            interval = _progressive_backoff(stationary_secs)
            reason = f"at known place ({place.get('label', 'unlabeled')})"
    else:
        interval = _progressive_backoff(stationary_secs)
        reason = "stationary"

    # Predictive pre-polling
    if stationary_secs > 300 and intelligence:
        p = intelligence.departure_probability(person, lat, lon, datetime.now(UTC))
        if p > 0.6:
            interval = min(interval, 8.0)
            reason = f"predicted departure (P={p:.0%})"
        elif p > 0.3:
            interval = min(interval, 15.0)
            reason = f"possible departure (P={p:.0%})"

    interval *= _battery_multiplier(battery, charging)
    return max(MIN_POLL_INTERVAL, min(interval, MAX_POLL_INTERVAL)), reason


def _progressive_backoff(stationary_secs):
    if stationary_secs < 120:
        return 20
    if stationary_secs < 600:
        return 90
    if stationary_secs < 1800:
        return 240
    return MAX_POLL_INTERVAL
