import csv
import io
import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from tracker import LocationTracker

log = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def _haversine_m(lat1, lon1, lat2, lon2):
    """Distance in meters between two lat/lon points."""
    import math

    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


def _analyze_movement(points):
    """Analyze recent movement for a person. Returns dict with speed, trend, stationary_seconds."""
    if not points or len(points) < 2:
        return {"speed_kmh": 0, "trend": "none", "stationary_seconds": float("inf")}

    recent = points[-10:]
    speeds = []
    for i in range(1, len(recent)):
        prev, curr = recent[i - 1], recent[i]
        dist = _haversine_m(prev["latitude"], prev["longitude"], curr["latitude"], curr["longitude"])
        try:
            t1 = datetime.fromisoformat(prev["timestamp"])
            t2 = datetime.fromisoformat(curr["timestamp"])
        except (ValueError, TypeError):
            continue
        dt = (t2 - t1).total_seconds()
        if dt > 0:
            speeds.append((dist / dt) * 3.6)

    if not speeds:
        return {"speed_kmh": 0, "trend": "none", "stationary_seconds": float("inf")}

    current_speed = speeds[-1]
    avg_speed = sum(speeds) / len(speeds)

    # Detect trend: accelerating, decelerating, or steady
    if len(speeds) >= 3:
        first_half = sum(speeds[: len(speeds) // 2]) / max(len(speeds) // 2, 1)
        second_half = sum(speeds[len(speeds) // 2 :]) / max(len(speeds) - len(speeds) // 2, 1)
        if second_half > first_half * 1.5:
            trend = "accelerating"
        elif second_half < first_half * 0.5:
            trend = "decelerating"
        else:
            trend = "steady"
    else:
        trend = "steady"

    # Calculate how long stationary (consecutive points within 25m)
    stationary_seconds = 0
    if current_speed < 1:
        for i in range(len(recent) - 1, 0, -1):
            dist = _haversine_m(
                recent[i]["latitude"],
                recent[i]["longitude"],
                recent[i - 1]["latitude"],
                recent[i - 1]["longitude"],
            )
            if dist > 25:
                break
            try:
                t1 = datetime.fromisoformat(recent[i - 1]["timestamp"])
                t2 = datetime.fromisoformat(recent[i]["timestamp"])
                stationary_seconds += (t2 - t1).total_seconds()
            except (ValueError, TypeError):
                break

    return {
        "speed_kmh": round(current_speed, 1),
        "avg_speed_kmh": round(avg_speed, 1),
        "trend": trend,
        "stationary_seconds": stationary_seconds,
    }


def _speed_info_for_points(points):
    """Compute speed info for a person's location points."""
    m = _analyze_movement(points)
    kmh = m["speed_kmh"]
    if kmh < 1:
        return {"speed_kmh": kmh, "label": "Stationary", "cls": "badge-stationary"}
    if kmh < 10:
        return {"speed_kmh": kmh, "label": "Walking", "cls": "badge-slow"}
    if kmh < 60:
        return {"speed_kmh": kmh, "label": "Driving", "cls": "badge-moving"}
    return {"speed_kmh": kmh, "label": "Highway", "cls": "badge-fast"}


def _adaptive_interval(history, default_interval):
    """Compute poll interval based on movement analysis across all tracked people.

    Uses speed, acceleration trend, and stationary duration to determine
    how often to poll. Polls aggressively when movement is detected or
    starting, and backs off progressively when stationary.

    Interval curve:
    - Just started moving (accelerating):  10s  (catch departure)
    - Highway (>60 km/h):                  15s  (fast but stable)
    - Driving (10-60 km/h):                30s  (city detail)
    - Walking (1-10 km/h):                 45s  (pedestrian detail)
    - Decelerating (<10 km/h, slowing):    20s  (catch arrival)
    - Just stopped (<2 min stationary):    30s  (might resume)
    - Recently stopped (2-10 min):        120s  (probably parked)
    - Stationary (10-30 min):             300s  (settled in)
    - Long stationary (>30 min):          600s  (not going anywhere)
    """
    best_interval = 600
    best_category = "long stationary"

    for person, locations in history.items():
        if person == "Me":
            continue
        m = _analyze_movement(locations)
        speed = m["speed_kmh"]
        trend = m["trend"]
        still_secs = m["stationary_seconds"]

        if speed >= 60:
            interval, category = 15, "highway"
        elif speed >= 10:
            interval, category = 30, "driving"
        elif speed >= 1:
            if trend == "accelerating":
                interval, category = 10, "departing"
            elif trend == "decelerating":
                interval, category = 20, "arriving"
            else:
                interval, category = 45, "walking"
        else:
            if still_secs < 120:
                interval, category = 30, "just stopped"
            elif still_secs < 600:
                interval, category = 120, "recently stopped"
            elif still_secs < 1800:
                interval, category = 300, "stationary"
            else:
                interval, category = 600, "long stationary"

        if interval < best_interval:
            best_interval = interval
            best_category = category

    return best_interval, best_category


def run_dashboard(data_file, cookies_file, email, port, poll_interval):
    app = Flask(__name__, template_folder=str(_TEMPLATE_DIR), static_folder=str(_STATIC_DIR))
    app.logger.setLevel(logging.WARNING)
    tracker = LocationTracker(cookies_file, email, data_file)

    self_name = "Me"
    poll_state = {"interval": poll_interval, "category": "moderate"}
    poll_lock = threading.Lock()

    def background_poll():
        from intelligence import Intelligence
        from intelligence import compute_poll_interval as _compute

        intel = Intelligence(tracker.db.conn)
        intel.decay_old_observations()

        # Backfill learning data on first run
        for person in tracker.db.get_people():
            if person == "Me":
                continue
            place_count = intel.conn.execute(
                "SELECT COUNT(*) as c FROM known_places WHERE person=?", (person,)
            ).fetchone()["c"]
            if place_count == 0:
                locs = tracker.db.get_locations(person=person)
                if locs:
                    log.info("Backfilling intelligence for %s...", person)
                    intel.backfill_from_locations(person, locs)

        person_states = {}
        consecutive_failures = 0
        last_maintenance = time.time()

        while True:
            try:
                success = tracker.poll_location()
                if success:
                    consecutive_failures = 0
                    history = tracker.history
                    best_interval = 600
                    best_reason = "idle"

                    for person, points in history.items():
                        if person == "Me" or not points:
                            continue

                        latest = points[-1]
                        m = _analyze_movement(points)

                        # State tracking for arrivals/departures
                        nearest = intel.find_nearest_place(person, latest["latitude"], latest["longitude"])
                        at_place = None
                        if nearest:
                            d = _haversine_m(
                                latest["latitude"], latest["longitude"], nearest["latitude"], nearest["longitude"]
                            )
                            if d < nearest["radius"]:
                                at_place = nearest

                        prev = person_states.get(person, {})
                        prev_place = prev.get("place_id")

                        if prev_place is None and at_place:
                            intel.record_arrival(person, at_place["id"], latest["timestamp"])
                            if prev.get("departed_from") and prev.get("departed_at"):
                                trip_locs = tracker.db.get_locations(person=person, since=prev["departed_at"])
                                intel.record_route(
                                    person,
                                    prev["departed_from"],
                                    at_place["id"],
                                    prev["departed_at"],
                                    latest["timestamp"],
                                    trip_locs,
                                )
                        elif prev_place is not None and not at_place:
                            intel.record_departure(person, prev_place, latest["timestamp"])
                        elif prev_place is None and not at_place and m["stationary_seconds"] > 300:
                            new_id = intel.cluster_stop(
                                person, latest["latitude"], latest["longitude"], latest["timestamp"]
                            )
                            at_place = {"id": new_id, "radius": 75}
                            intel.record_arrival(person, new_id, latest["timestamp"])

                        person_states[person] = {
                            "place_id": at_place["id"] if at_place else None,
                            "departed_from": prev_place if (prev_place and not at_place) else prev.get("departed_from"),
                            "departed_at": latest["timestamp"]
                            if (prev_place and not at_place)
                            else prev.get("departed_at"),
                        }

                        # Geofence checking
                        fence_key = f"fences_{person}"
                        if fence_key not in person_states:
                            person_states[fence_key] = {}
                        intel.check_geofences(
                            tracker.db,
                            person,
                            latest["latitude"],
                            latest["longitude"],
                            latest["timestamp"],
                            person_states[fence_key],
                        )

                        # Speed zone learning on deceleration
                        if m["trend"] == "decelerating" and m["speed_kmh"] < 15 and m.get("avg_speed_kmh", 0) > 20:
                            intel.learn_speed_zones_from_trip(points[-10:])

                        # Compute interval for this person
                        interval, reason = _compute(
                            intel,
                            person,
                            latest["latitude"],
                            latest["longitude"],
                            m["speed_kmh"],
                            m["trend"],
                            m["stationary_seconds"],
                            latest.get("battery"),
                            latest.get("charging"),
                        )

                        # Speed zone proximity check
                        if m["speed_kmh"] >= 5 and intel.near_speed_zone(points[-5:]):
                            interval = max(4, interval * 0.5)
                            reason += " +turn"

                        if interval < best_interval:
                            best_interval = interval
                            best_reason = reason

                    interval = best_interval
                    category = best_reason
                else:
                    consecutive_failures += 1
                    interval = min(poll_interval * (2**consecutive_failures), 1800)
                    category = "error"

                # Daily maintenance
                if time.time() - last_maintenance > 86400:
                    intel.decay_old_observations()
                    last_maintenance = time.time()

                with poll_lock:
                    poll_state["interval"] = int(interval)
                    poll_state["category"] = category
                log.info("Next poll in %ds (%s)", int(interval), category)
                time.sleep(interval)
            except Exception as e:
                log.error("Poll thread error: %s: %s", type(e).__name__, e)
                consecutive_failures += 1
                time.sleep(min(poll_interval * (2**consecutive_failures), 1800))

    poll_thread = threading.Thread(target=background_poll, daemon=True)
    poll_thread.start()

    @app.route("/")
    def index():
        return render_template("index.html")

    API_VERSION = 1

    @app.route("/api/locations")
    @app.route("/api/v1/locations")
    def api_locations():
        days = request.args.get("days", "0", type=str)
        try:
            days_int = int(days) if days != "0" else None
        except ValueError:
            days_int = None
        from datetime import timedelta

        since = None
        if days_int:
            since = (datetime.now(UTC) - timedelta(days=days_int)).isoformat()
        data = tracker.db.get_history_dict(since=since)
        # Attach speed_info per person so the frontend doesn't need to recompute
        speed_info = {}
        for person, pts in data.items():
            speed_info[person] = _speed_info_for_points(pts)
        return jsonify({"api_version": API_VERSION, "locations": data, "speed_info": speed_info})

    @app.route("/api/stats")
    @app.route("/api/v1/stats")
    def api_stats():
        return jsonify(tracker.get_stats())

    @app.route("/api/self-location", methods=["POST"])
    @app.route("/api/v1/self-location", methods=["POST"])
    def api_self_location():
        data = request.get_json()
        if not data or "latitude" not in data or "longitude" not in data:
            return jsonify({"error": "missing fields"}), 400

        try:
            lat = float(data["latitude"])
            lon = float(data["longitude"])
        except (TypeError, ValueError):
            return jsonify({"error": "invalid coordinates"}), 400

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return jsonify({"error": "coordinates out of range"}), 400

        tracker.db.add_location(
            person=self_name,
            timestamp=datetime.now(UTC).isoformat(),
            latitude=lat,
            longitude=lon,
            accuracy=data.get("accuracy"),
            address=f"({lat:.4f}, {lon:.4f})",
        )
        return jsonify({"ok": True})

    @app.route("/api/poll-status")
    @app.route("/api/v1/poll-status")
    def api_poll_status():
        with poll_lock:
            return jsonify(
                {
                    "current_interval": poll_state["interval"],
                    "speed_category": poll_state["category"],
                }
            )

    @app.route("/api/health")
    @app.route("/api/v1/health")
    def api_health():
        return jsonify(tracker.db.get_health())

    @app.route("/api/snap", methods=["POST"])
    @app.route("/api/v1/snap", methods=["POST"])
    def api_snap():
        data = request.get_json()
        if not data or "coords" not in data:
            return jsonify({"error": "missing coords"}), 400
        try:
            from road_snap import get_snapper

            snapped = get_snapper().snap_trace(data["coords"])
            return jsonify({"coords": snapped})
        except Exception as e:
            return jsonify({"coords": data["coords"], "error": str(e)})

    @app.route("/api/geofences")
    @app.route("/api/v1/geofences")
    def api_geofences():
        person = request.args.get("person")
        return jsonify(tracker.db.get_geofences(person))

    @app.route("/api/geofences", methods=["POST"])
    @app.route("/api/v1/geofences", methods=["POST"])
    def api_add_geofence():
        data = request.get_json()
        if not data or not all(k in data for k in ("person", "label", "latitude", "longitude")):
            return jsonify({"error": "missing fields"}), 400
        gid = tracker.db.add_geofence(
            data["person"],
            data["label"],
            float(data["latitude"]),
            float(data["longitude"]),
            radius_m=float(data.get("radius_m", 200)),
            on_enter=data.get("on_enter", True),
            on_exit=data.get("on_exit", True),
        )
        return jsonify({"ok": True, "id": gid})

    @app.route("/api/geofence-events")
    @app.route("/api/v1/geofence-events")
    def api_geofence_events():
        unack = request.args.get("unacknowledged", "false") == "true"
        return jsonify(tracker.db.get_geofence_events(unacknowledged_only=unack))

    @app.route("/api/geofence-events/acknowledge", methods=["POST"])
    @app.route("/api/v1/geofence-events/acknowledge", methods=["POST"])
    def api_ack_geofence_events():
        tracker.db.acknowledge_geofence_events()
        return jsonify({"ok": True})

    @app.route("/api/export")
    @app.route("/api/v1/export")
    def api_export():
        fmt = request.args.get("format", "json")
        history = tracker.history

        if fmt == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(
                ["person", "timestamp", "latitude", "longitude", "accuracy", "battery", "charging", "address"]
            )
            for person, locations in history.items():
                for loc in locations:
                    writer.writerow(
                        [
                            person,
                            loc.get("timestamp"),
                            loc.get("latitude"),
                            loc.get("longitude"),
                            loc.get("accuracy"),
                            loc.get("battery"),
                            loc.get("charging"),
                            loc.get("address"),
                        ]
                    )
            return Response(
                buf.getvalue(),
                mimetype="text/csv",
                headers={"Content-Disposition": "attachment; filename=location-history.csv"},
            )

        if fmt == "geojson":
            features = []
            for person, locations in history.items():
                for loc in locations:
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Point",
                                "coordinates": [loc.get("longitude"), loc.get("latitude")],
                            },
                            "properties": {
                                "person": person,
                                "timestamp": loc.get("timestamp"),
                                "accuracy": loc.get("accuracy"),
                                "battery": loc.get("battery"),
                                "charging": loc.get("charging"),
                                "address": loc.get("address"),
                            },
                        }
                    )
            geojson = {"type": "FeatureCollection", "features": features}
            return Response(
                json.dumps(geojson),
                mimetype="application/geo+json",
                headers={"Content-Disposition": "attachment; filename=location-history.geojson"},
            )

        return jsonify(history)

    log.info("Dashboard running at http://tracker.local (port %d)", port)
    app.run(host="127.0.0.1", port=port, debug=False)
