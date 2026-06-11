import logging
import math
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import folium
import pandas as pd
from locationsharinglib import InvalidCookies, InvalidData, Service

from db import LocationDB

log = logging.getLogger(__name__)

COLORS = ['#4361ee', '#e63946', '#2a9d8f', '#e9c46a', '#7209b7', '#f77f00', '#06d6a0', '#ef476f']
STOP_DISTANCE_METERS = 25


class LocationTracker:
    def __init__(self, cookies_file, email, data_file='location_history.db'):
        self.cookies_file = cookies_file
        self.email = email
        self.db = LocationDB(data_file)
        self._stats_cache = None
        self._stats_cache_time = 0
        self._stats_cache_points = 0

        # Auto-migrate from JSON if DB is empty and JSON exists
        json_path = Path(data_file).with_suffix('.json')
        if self.db.get_total_points() == 0 and json_path.exists():
            log.info("Migrating data from %s to SQLite...", json_path)
            self.db.import_from_json(json_path)

    @property
    def history(self):
        return self.db.get_history_dict()

    def save_history(self):
        pass  # SQLite commits per-insert; no-op for backward compat

    def haversine(self, lon1, lat1, lon2, lat2):
        lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))
        return c * 6371000

    def _try_refresh_cookies(self):
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

    def poll_location(self):
        try:
            from cookie_store import decrypt_to_tempfile, has_encrypted_cookies
            if has_encrypted_cookies(self.cookies_file):
                tmp_path = decrypt_to_tempfile(self.cookies_file)
                if not tmp_path:
                    log.error("Cannot decrypt cookies. Re-run: location-tracker cookies")
                    return False
                try:
                    service = Service(
                        cookies_file=tmp_path,
                        authenticating_account=self.email
                    )
                finally:
                    os.unlink(tmp_path)
            else:
                service = Service(
                    cookies_file=self.cookies_file,
                    authenticating_account=self.email
                )

            for person in service.get_all_people():
                person_id = person.full_name or person.email or "Unknown"
                ts = datetime.now(UTC).isoformat()
                battery = getattr(person, 'battery_level', None)
                charging = getattr(person, 'charging', None)
                address = getattr(person, 'address', None) or 'Unknown Address'

                self.db.add_location(
                    person=person_id,
                    timestamp=ts,
                    latitude=person.latitude,
                    longitude=person.longitude,
                    accuracy=getattr(person, 'accuracy', None),
                    battery=battery,
                    charging=charging,
                    address=address,
                )

                batt_str = f"{battery}%" if battery is not None else "N/A"
                log.info(
                    "Ping: %s | Batt: %s | Coords: (%.4f, %.4f)",
                    person_id, batt_str, person.latitude, person.longitude
                )

            return True

        except InvalidCookies:
            log.warning("Cookies expired. Attempting automatic refresh...")
            if self._try_refresh_cookies():
                log.info("Cookies refreshed successfully. Retrying poll.")
                return self.poll_location()
            log.error("Auto-refresh failed. Re-run: location-tracker cookies")
            return False
        except InvalidData as e:
            log.warning("Poll returned invalid data (transient): %s", e)
            return False
        except (ConnectionError, TimeoutError, OSError) as e:
            log.warning("Poll failed (network): %s", e)
            return False
        except Exception as e:
            log.error("Poll failed (unexpected): %s: %s", type(e).__name__, e)
            return False

    def get_people(self):
        return self.db.get_people()

    def get_stats(self):
        total_points = self.db.get_total_points()
        now = time.time()
        if (self._stats_cache is not None
                and now - self._stats_cache_time < 30
                and total_points == self._stats_cache_points):
            return self._stats_cache

        stats = {}
        for person, locations in self.history.items():
            if not locations:
                continue
            df = pd.DataFrame(locations)
            df['timestamp'] = pd.to_datetime(df['timestamp'])

            total_distance = 0.0
            for i in range(1, len(df)):
                total_distance += self.haversine(
                    df.iloc[i - 1]['longitude'], df.iloc[i - 1]['latitude'],
                    df.iloc[i]['longitude'], df.iloc[i]['latitude']
                )

            stops = self._compute_stops(df)
            total_dwell = sum((s['end_time'] - s['start_time']).total_seconds() for s in stops)

            stats[person] = {
                'total_points': len(df),
                'total_distance_km': round(total_distance / 1000, 2),
                'total_stops': len(stops),
                'total_dwell_hours': round(total_dwell / 3600, 1),
                'first_seen': df['timestamp'].min().isoformat(),
                'last_seen': df['timestamp'].max().isoformat(),
            }
        self._stats_cache = stats
        self._stats_cache_time = time.time()
        self._stats_cache_points = total_points
        return stats

    def print_stats(self):
        stats = self.get_stats()
        if not stats:
            log.info("No data collected yet.")
            return
        for person, s in stats.items():
            log.info("--- %s ---", person)
            log.info("  Points: %d | Distance: %.1f km | Stops: %d",
                     s['total_points'], s['total_distance_km'], s['total_stops'])
            log.info("  Dwell time: %.1f hours", s['total_dwell_hours'])
            log.info("  Tracked: %s to %s", s['first_seen'][:10], s['last_seen'][:10])

    def _compute_stops(self, df):
        if df.empty:
            return []
        df = df.sort_values('timestamp').reset_index(drop=True)
        stops = []
        current = {
            'lat': df.iloc[0]['latitude'], 'lon': df.iloc[0]['longitude'],
            'start_time': df.iloc[0]['timestamp'], 'end_time': df.iloc[0]['timestamp'],
            'batteries': [df.iloc[0].get('battery')], 'charging': df.iloc[0].get('charging'),
            'address': df.iloc[0].get('address', 'Unknown'), 'count': 1
        }

        for i in range(1, len(df)):
            row = df.iloc[i]
            dist = self.haversine(current['lon'], current['lat'], row['longitude'], row['latitude'])

            if dist < STOP_DISTANCE_METERS:
                current['end_time'] = row['timestamp']
                current['batteries'].append(row.get('battery'))
                current['charging'] = row.get('charging')
                current['count'] += 1
            else:
                stops.append(current)
                current = {
                    'lat': row['latitude'], 'lon': row['longitude'],
                    'start_time': row['timestamp'], 'end_time': row['timestamp'],
                    'batteries': [row.get('battery')], 'charging': row.get('charging'),
                    'address': row.get('address', 'Unknown'), 'count': 1
                }
        stops.append(current)
        return stops

    def generate_map(self, output_file='location_map.html', days=None, person_filter=None):
        all_locations = []
        for person, locations in self.history.items():
            if person_filter and person != person_filter:
                continue
            for loc in locations:
                loc_copy = dict(loc)
                loc_copy['person'] = person
                all_locations.append(loc_copy)

        if not all_locations:
            log.warning("No location data available yet.")
            return None

        df = pd.DataFrame(all_locations)
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        if days:
            cutoff = datetime.now(UTC) - timedelta(days=days)
            df = df[df['timestamp'] >= cutoff]
            if df.empty:
                log.warning("No data in the last %d days.", days)
                return None

        m = folium.Map(
            location=[df['latitude'].mean(), df['longitude'].mean()],
            zoom_start=14,
            tiles='CartoDB positron'
        )

        # Add tile layer options
        folium.TileLayer('OpenStreetMap', name='Street Map').add_to(m)
        folium.TileLayer('CartoDB dark_matter', name='Dark Mode').add_to(m)

        people = df['person'].unique()
        color_map = {p: COLORS[i % len(COLORS)] for i, p in enumerate(people)}

        for person in people:
            person_df = df[df['person'] == person].sort_values('timestamp').reset_index(drop=True)
            color = color_map[person]

            # Feature group for toggling
            fg = folium.FeatureGroup(name=person)

            # Draw path
            coordinates = [[row['latitude'], row['longitude']] for _, row in person_df.iterrows()]
            if len(coordinates) > 1:
                folium.PolyLine(
                    coordinates,
                    weight=3,
                    color=color,
                    opacity=0.6,
                    smooth_factor=1.5,
                    dash_array='5 8'
                ).add_to(fg)

            # Draw stops
            stops = self._compute_stops(person_df)
            for stop in stops:
                duration = stop['end_time'] - stop['start_time']
                minutes = duration.total_seconds() / 60.0

                if minutes < 5:
                    radius = 4
                elif minutes < 15:
                    radius = 6
                elif minutes < 45:
                    radius = 9
                elif minutes < 120:
                    radius = 12
                else:
                    radius = 16

                valid_batteries = [b for b in stop['batteries'] if b is not None]
                avg_battery = int(sum(valid_batteries) / len(valid_batteries)) if valid_batteries else None
                batt_str = f"{avg_battery}%" if avg_battery is not None else "N/A"
                charging_icon = " (charging)" if stop['charging'] else ""

                if minutes < 1:
                    duration_str = "Passing through"
                    time_str = f"{stop['start_time'].strftime('%b %d, %I:%M %p')}"
                else:
                    h, m_rem = divmod(int(minutes), 60)
                    duration_str = f"{h}h {m_rem}m" if h > 0 else f"{m_rem} mins"
                    time_str = f"{stop['start_time'].strftime('%I:%M %p')} - {stop['end_time'].strftime('%I:%M %p')}"

                popup_html = f"""
                <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; width: 240px; color: #333;">
                    <div style="background: {color}; color: white; padding: 8px 12px; margin: -10px -10px 10px -10px; font-weight: 600; font-size: 14px; border-radius: 4px 4px 0 0;">
                        {person}
                    </div>
                    <p style="margin: 4px 0; font-size: 13px;"><b>Address:</b> {stop['address']}</p>
                    <hr style="border: 0; border-top: 1px solid #eee; margin: 8px 0;">
                    <p style="margin: 4px 0; font-size: 13px;"><b>Date:</b> {stop['start_time'].strftime('%b %d, %Y')}</p>
                    <p style="margin: 4px 0; font-size: 13px;"><b>Time:</b> {time_str}</p>
                    <p style="margin: 4px 0; font-size: 13px;"><b>Duration:</b> {duration_str}</p>
                    <p style="margin: 4px 0; font-size: 13px;"><b>Battery:</b> {batt_str}{charging_icon}</p>
                    <p style="margin: 4px 0; font-size: 13px;"><b>Pings:</b> {stop['count']}</p>
                </div>
                """

                folium.CircleMarker(
                    location=[stop['lat'], stop['lon']],
                    radius=radius,
                    popup=folium.Popup(popup_html, max_width=280),
                    tooltip=f"{person}: {stop['address']} ({duration_str})",
                    color='#ffffff',
                    weight=2,
                    fill=True,
                    fillColor=color,
                    fill_opacity=0.85
                ).add_to(fg)

            fg.add_to(m)

        folium.LayerControl(collapsed=False).add_to(m)
        self._add_legend(m, color_map)

        m.save(output_file)
        log.info("Map saved to '%s'", output_file)
        return output_file

    def _add_legend(self, m, color_map):
        people_items = ''.join(
            f'<div style="display:flex;align-items:center;margin-bottom:5px;">'
            f'<div style="width:12px;height:12px;background:{c};border-radius:50%;margin-right:8px;border:2px solid white;"></div>'
            f'<span>{p}</span></div>'
            for p, c in color_map.items()
        )
        legend_html = f"""
        <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
                    background:white;padding:14px 18px;border-radius:10px;
                    box-shadow:0 4px 12px rgba(0,0,0,0.15);font-family:sans-serif;font-size:12px;color:#333;">
            <b style="font-size:14px;">Location Tracker</b><br><br>
            {people_items}
            <hr style="border:0;border-top:1px solid #eee;margin:8px 0;">
            <div style="display:flex;align-items:center;margin-bottom:4px;">
                <div style="width:20px;height:3px;background:#4361ee;margin-right:8px;border-style:dashed;"></div> Travel Path
            </div>
            <div style="display:flex;align-items:center;margin-bottom:4px;">
                <div style="width:8px;height:8px;background:#4361ee;border-radius:50%;margin-right:8px;"></div> Brief Stop
            </div>
            <div style="display:flex;align-items:center;">
                <div style="width:16px;height:16px;background:#4361ee;border-radius:50%;margin-right:8px;"></div> Long Stop
            </div>
        </div>
        """
        m.get_root().html.add_child(folium.Element(legend_html))
