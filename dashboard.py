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



def _compute_speed_kmh(history):
    """Compute recent speed in km/h from the last few data points across all people."""
    import math
    best_speed = 0.0
    for person, locations in history.items():
        if person == "Me" or len(locations) < 2:
            continue
        recent = locations[-5:]
        for i in range(1, len(recent)):
            prev, curr = recent[i - 1], recent[i]
            lat1, lon1 = math.radians(prev['latitude']), math.radians(prev['longitude'])
            lat2, lon2 = math.radians(curr['latitude']), math.radians(curr['longitude'])
            dlat, dlon = lat2 - lat1, lon2 - lon1
            a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
            dist_m = 6371000 * 2 * math.asin(math.sqrt(a))
            try:
                t1 = datetime.fromisoformat(prev['timestamp'])
                t2 = datetime.fromisoformat(curr['timestamp'])
            except (ValueError, TypeError):
                continue
            dt = (t2 - t1).total_seconds()
            if dt > 0:
                speed = (dist_m / dt) * 3.6
                best_speed = max(best_speed, speed)
    return best_speed


def _speed_info_for_points(points):
    """Compute speed info for a person's location points."""
    import math
    if not points or len(points) < 2:
        return {"speed_kmh": 0, "label": "Stationary", "cls": "badge-stationary"}
    prev, last = points[-2], points[-1]
    lat1, lon1 = math.radians(prev['latitude']), math.radians(prev['longitude'])
    lat2, lon2 = math.radians(last['latitude']), math.radians(last['longitude'])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    dist_m = 6371000 * 2 * math.asin(math.sqrt(a))
    try:
        t1 = datetime.fromisoformat(prev['timestamp'])
        t2 = datetime.fromisoformat(last['timestamp'])
    except (ValueError, TypeError):
        return {"speed_kmh": 0, "label": "Stationary", "cls": "badge-stationary"}
    dt = (t2 - t1).total_seconds()
    if dt <= 0:
        return {"speed_kmh": 0, "label": "Stationary", "cls": "badge-stationary"}
    kmh = (dist_m / dt) * 3.6
    if kmh < 1:
        return {"speed_kmh": round(kmh, 1), "label": "Stationary", "cls": "badge-stationary"}
    if kmh < 10:
        return {"speed_kmh": round(kmh, 1), "label": "Walking", "cls": "badge-slow"}
    if kmh < 60:
        return {"speed_kmh": round(kmh, 1), "label": "Driving", "cls": "badge-moving"}
    return {"speed_kmh": round(kmh, 1), "label": "Highway", "cls": "badge-fast"}


def _adaptive_interval(history, default_interval):
    """Return (interval_seconds, speed_category) based on movement speed.

    Polls aggressively during movement for accurate path tracking:
    - Stationary (< 1 km/h for 5+ min): 600s (10 min)
    - Slow (1-10 km/h, e.g. walking): 60s (1 min)
    - Moderate (10-60 km/h, e.g. city driving): 30s
    - Fast (> 60 km/h, e.g. highway): 15s
    """
    speed = _compute_speed_kmh(history)
    if speed >= 60:
        return 15, "fast"
    elif speed >= 10:
        return 30, "moderate"
    elif speed >= 1:
        return 60, "slow"
    else:
        # Check if stationary for 5+ minutes
        for person, locations in history.items():
            if person == "Me" or len(locations) < 2:
                continue
            last = locations[-1]
            second_last = locations[-2]
            try:
                t1 = datetime.fromisoformat(second_last['timestamp'])
                t2 = datetime.fromisoformat(last['timestamp'])
            except (ValueError, TypeError):
                continue
            if (t2 - t1).total_seconds() >= 300:
                return 600, "stationary"
        return default_interval, "stationary"


def run_dashboard(data_file, cookies_file, email, port, poll_interval):
    app = Flask(__name__, template_folder=str(_TEMPLATE_DIR), static_folder=str(_STATIC_DIR))
    app.logger.setLevel(logging.WARNING)
    tracker = LocationTracker(cookies_file, email, data_file)

    self_name = "Me"
    poll_state = {"interval": poll_interval, "category": "moderate"}
    poll_lock = threading.Lock()

    def background_poll():
        while True:
            try:
                tracker.poll_location()
                interval, category = _adaptive_interval(tracker.history, poll_interval)
                with poll_lock:
                    poll_state["interval"] = interval
                    poll_state["category"] = category
                log.info("Next poll in %ds (speed: %s)", interval, category)
                time.sleep(interval)
            except Exception as e:
                log.error("Poll thread error: %s: %s", type(e).__name__, e)
                time.sleep(poll_interval)

    poll_thread = threading.Thread(target=background_poll, daemon=True)
    poll_thread.start()

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/api/locations')
    def api_locations():
        days = request.args.get('days', '0', type=str)
        try:
            days_int = int(days) if days != '0' else None
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
        return jsonify({"locations": data, "speed_info": speed_info})

    @app.route('/api/stats')
    def api_stats():
        return jsonify(tracker.get_stats())

    @app.route('/api/self-location', methods=['POST'])
    def api_self_location():
        data = request.get_json()
        if not data or 'latitude' not in data or 'longitude' not in data:
            return jsonify({'error': 'missing fields'}), 400

        try:
            lat = float(data['latitude'])
            lon = float(data['longitude'])
        except (TypeError, ValueError):
            return jsonify({'error': 'invalid coordinates'}), 400

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return jsonify({'error': 'coordinates out of range'}), 400

        tracker.db.add_location(
            person=self_name,
            timestamp=datetime.now(UTC).isoformat(),
            latitude=lat,
            longitude=lon,
            accuracy=data.get('accuracy'),
            address=f"({lat:.4f}, {lon:.4f})",
        )
        return jsonify({'ok': True})

    @app.route('/api/poll-status')
    def api_poll_status():
        with poll_lock:
            return jsonify({
                'current_interval': poll_state['interval'],
                'speed_category': poll_state['category'],
            })

    @app.route('/api/export')
    def api_export():
        fmt = request.args.get('format', 'json')
        history = tracker.history

        if fmt == 'csv':
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(['person', 'timestamp', 'latitude', 'longitude',
                             'accuracy', 'battery', 'charging', 'address'])
            for person, locations in history.items():
                for loc in locations:
                    writer.writerow([
                        person, loc.get('timestamp'), loc.get('latitude'),
                        loc.get('longitude'), loc.get('accuracy'),
                        loc.get('battery'), loc.get('charging'),
                        loc.get('address'),
                    ])
            return Response(buf.getvalue(), mimetype='text/csv',
                            headers={'Content-Disposition': 'attachment; filename=location-history.csv'})

        if fmt == 'geojson':
            features = []
            for person, locations in history.items():
                for loc in locations:
                    features.append({
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [loc.get('longitude'), loc.get('latitude')],
                        },
                        "properties": {
                            "person": person,
                            "timestamp": loc.get('timestamp'),
                            "accuracy": loc.get('accuracy'),
                            "battery": loc.get('battery'),
                            "charging": loc.get('charging'),
                            "address": loc.get('address'),
                        },
                    })
            geojson = {"type": "FeatureCollection", "features": features}
            return Response(json.dumps(geojson), mimetype='application/geo+json',
                            headers={'Content-Disposition': 'attachment; filename=location-history.geojson'})

        return jsonify(history)

    log.info("Dashboard running at http://tracker (port %d)", port)
    app.run(host='127.0.0.1', port=port, debug=False)
