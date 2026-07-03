"use strict";

/* ------------------------------------------------------------------ */
/* Configuration                                                       */
/* ------------------------------------------------------------------ */
const CONFIG = {
  COLORS: [
    "#4f6df5",
    "#ef4444",
    "#2dd4a8",
    "#eab308",
    "#a855f7",
    "#f97316",
    "#06d6a0",
    "#ec4899",
  ],
  INTERVALS: { MAP: 30000, POLL: 15000, SELF: 60000 },
  EARTH_RADIUS_M: 6371000,
  STOP_DISTANCE_M: 25,
  SNAP_TIMEOUT_MS: 10000,
  TOAST_MS: 3200,
  API: {
    LOCATIONS: "/api/locations",
    STATS: "/api/stats",
    POLL_STATUS: "/api/poll-status",
    SNAP: "/api/snap",
    SELF_LOCATION: "/api/self-location",
    EXPORT: "/api/export",
  },
  TILES: {
    roadmap: "https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}",
    satellite: "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
    hybrid: "https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
    terrain: "https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}",
  },
  HEAT_GRADIENT: {
    0.2: "#4f6df5",
    0.4: "#2dd4a8",
    0.6: "#eab308",
    0.8: "#f97316",
    1.0: "#ef4444",
  },
};

/* ------------------------------------------------------------------ */
/* Utilities                                                           */
/* ------------------------------------------------------------------ */
const Utils = {
  escapeHTML(s) {
    const d = document.createElement("div");
    d.textContent = s || "";
    return d.innerHTML;
  },

  showToast(msg, type = "info") {
    const el = document.createElement("div");
    el.className = "toast toast-" + type;
    el.textContent = msg;
    document.getElementById("toast-container").appendChild(el);
    setTimeout(() => el.remove(), CONFIG.TOAST_MS);
  },

  timeAgo(ts) {
    const diff = (Date.now() - new Date(ts).getTime()) / 1000;
    if (diff < 60) return "Just now";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return Math.floor(diff / 86400) + "d ago";
  },

  haversine(lon1, lat1, lon2, lat2) {
    const toRad = (x) => (x * Math.PI) / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return CONFIG.EARTH_RADIUS_M * 2 * Math.asin(Math.sqrt(a));
  },

  hexToRgb(hex) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return r + "," + g + "," + b;
  },

  speedInfo(points) {
    if (!points || points.length < 2)
      return { speed: 0, label: "Stationary", cls: "badge-stationary" };
    const last = points[points.length - 1];
    const prev = points[points.length - 2];
    const dist = Utils.haversine(
      prev.longitude,
      prev.latitude,
      last.longitude,
      last.latitude,
    );
    const dt = (new Date(last.timestamp) - new Date(prev.timestamp)) / 1000;
    if (dt <= 0)
      return { speed: 0, label: "Stationary", cls: "badge-stationary" };
    const kmh = (dist / dt) * 3.6;
    if (kmh < 1)
      return { speed: kmh, label: "Stationary", cls: "badge-stationary" };
    if (kmh < 10) return { speed: kmh, label: "Walking", cls: "badge-slow" };
    if (kmh < 60) return { speed: kmh, label: "Driving", cls: "badge-moving" };
    return { speed: kmh, label: "Highway", cls: "badge-fast" };
  },

  computeStops(points) {
    if (points.length === 0) return [];
    const stops = [];
    let cur = {
      lat: points[0].latitude,
      lon: points[0].longitude,
      start: points[0].timestamp,
      end: points[0].timestamp,
      address: points[0].address,
      count: 1,
    };
    for (let i = 1; i < points.length; i++) {
      const dist = Utils.haversine(
        cur.lon,
        cur.lat,
        points[i].longitude,
        points[i].latitude,
      );
      if (dist < CONFIG.STOP_DISTANCE_M) {
        cur.end = points[i].timestamp;
        cur.count++;
      } else {
        stops.push(cur);
        cur = {
          lat: points[i].latitude,
          lon: points[i].longitude,
          start: points[i].timestamp,
          end: points[i].timestamp,
          address: points[i].address,
          count: 1,
        };
      }
    }
    stops.push(cur);
    return stops;
  },
};

/* ------------------------------------------------------------------ */
/* API client                                                          */
/* ------------------------------------------------------------------ */
const APIClient = {
  _snapCache: {},

  async fetchJSON(url, options = {}) {
    const resp = await fetch(url, options);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  },

  _coordsHash(coords) {
    if (!coords || coords.length === 0) return "";
    return (
      coords.length +
      ":" +
      coords[0][0].toFixed(4) +
      "," +
      coords[coords.length - 1][0].toFixed(4)
    );
  },

  async snapToRoads(coords) {
    if (!coords || coords.length < 2) return coords;
    const hash = this._coordsHash(coords);
    if (this._snapCache[hash]) return this._snapCache[hash];

    try {
      const data = await this.fetchJSON(CONFIG.API.SNAP, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ coords }),
        signal: AbortSignal.timeout(CONFIG.SNAP_TIMEOUT_MS),
      });
      if (data.coords && data.coords.length >= 2) {
        this._snapCache[hash] = data.coords;
        return data.coords;
      }
    } catch (e) {
      console.warn("Road snap failed:", e.message);
    }

    this._snapCache[hash] = coords;
    return coords;
  },

  getLocations(days) {
    return this.fetchJSON(CONFIG.API.LOCATIONS + "?days=" + days);
  },

  getStats() {
    return this.fetchJSON(CONFIG.API.STATS);
  },

  getPollStatus() {
    return this.fetchJSON(CONFIG.API.POLL_STATUS);
  },

  postSelfLocation(payload) {
    return fetch(CONFIG.API.SELF_LOCATION, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  async exportBlob(fmt) {
    const resp = await fetch(CONFIG.API.EXPORT + "?format=" + fmt);
    return resp.blob();
  },
};

/* ------------------------------------------------------------------ */
/* Leaflet map management                                              */
/* ------------------------------------------------------------------ */
class MapManager {
  constructor(containerId) {
    this.instance = L.map(containerId, {
      zoomControl: false,
      attributionControl: false,
    }).setView([37.7749, -122.4194], 13);
    L.control.zoom({ position: "bottomleft" }).addTo(this.instance);
    L.control
      .attribution({ position: "bottomleft", prefix: false })
      .addTo(this.instance);

    this.tileLayers = {};
    for (const [name, url] of Object.entries(CONFIG.TILES)) {
      this.tileLayers[name] = L.tileLayer(url, {
        maxZoom: 22,
        attribution: "&copy; Google",
      });
    }

    this.layers = {};
    this.heatLayer = null;
    this.selfMarker = null;
    this.currentBaseLayer = null;
    this.firstLoad = true;
  }

  switchLayer(name) {
    if (this.currentBaseLayer) this.instance.removeLayer(this.currentBaseLayer);
    this.currentBaseLayer = this.tileLayers[name];
    this.currentBaseLayer.addTo(this.instance);
    document
      .querySelectorAll(".map-pill")
      .forEach((p) => p.classList.toggle("active", p.dataset.layer === name));
  }

  clearOverlays() {
    Object.values(this.layers).forEach((l) => this.instance.removeLayer(l));
    this.layers = {};
    if (this.heatLayer) {
      this.instance.removeLayer(this.heatLayer);
      this.heatLayer = null;
    }
  }

  addPersonLayer(person, group) {
    group.addTo(this.instance);
    this.layers[person] = group;
  }

  setHeatLayer(coords) {
    const heatData = coords.map((c) => [c[0], c[1], 0.6]);
    this.heatLayer = L.heatLayer(heatData, {
      radius: 20,
      blur: 25,
      maxZoom: 17,
      gradient: CONFIG.HEAT_GRADIENT,
    }).addTo(this.instance);
  }

  fitToAll(coords) {
    if (coords.length > 0 && this.firstLoad) {
      this.instance.fitBounds(L.latLngBounds(coords).pad(0.08));
      this.firstLoad = false;
    }
  }

  fitAllLayers() {
    const allBounds = [];
    Object.values(this.layers).forEach((l) => {
      try {
        allBounds.push(l.getBounds());
      } catch (e) {
        console.warn(e);
      }
    });
    if (allBounds.length > 0) {
      let combined = allBounds[0];
      allBounds.slice(1).forEach((b) => combined.extend(b));
      this.instance.fitBounds(combined.pad(0.1));
    }
  }

  focusPerson(person) {
    const layer = this.layers[person];
    if (layer) this.instance.fitBounds(layer.getBounds().pad(0.15));
  }

  setSelfMarker(lat, lon) {
    if (this.selfMarker) this.instance.removeLayer(this.selfMarker);
    const icon = L.divIcon({
      className: "",
      html: `<div style="position:relative;width:32px;height:32px;">
                    <div class="pulse-ring" style="position:absolute;inset:0;border-radius:50%;border:2px solid var(--green);"></div>
                    <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:12px;height:12px;border-radius:50%;background:var(--green);border:2px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,0.3);"></div>
                </div>`,
      iconSize: [32, 32],
      iconAnchor: [16, 16],
    });
    this.selfMarker = L.marker([lat, lon], { icon })
      .bindPopup(
        '<b style="color:#fff;">You</b><br><span style="color:#999;">Updated just now</span>',
      )
      .addTo(this.instance);
  }

  clearSelfMarker() {
    if (this.selfMarker) {
      this.instance.removeLayer(this.selfMarker);
      this.selfMarker = null;
    }
  }
}

/* ------------------------------------------------------------------ */
/* Application orchestrator                                            */
/* ------------------------------------------------------------------ */
class App {
  constructor() {
    this.mapMgr = new MapManager("map");
    this.state = {
      currentData: null,
      speedInfo: {},
      viewMode: "path",
      lastFingerprint: "",
      peopleOrder: [],
    };
    this.selfTracking = { active: false, intervalId: null };
    this.init();
  }

  init() {
    this.mapMgr.switchLayer("roadmap");
    this.refreshMap();
    this.loadPollStatus();

    const refreshId = setInterval(
      () => this.refreshMap(),
      CONFIG.INTERVALS.MAP,
    );
    const pollId = setInterval(
      () => this.loadPollStatus(),
      CONFIG.INTERVALS.POLL,
    );
    window.addEventListener("beforeunload", () => {
      clearInterval(refreshId);
      clearInterval(pollId);
    });

    this.bindEvents();
  }

  bindEvents() {
    document
      .getElementById("sidebar-toggle")
      .addEventListener("click", () => this.toggleSidebar());

    const sidebar = document.getElementById("sidebar");
    sidebar.addEventListener("click", (e) => {
      if (window.innerWidth <= 640 && e.target === sidebar)
        this.toggleSidebar();
    });

    document.querySelectorAll(".map-pill").forEach((pill) => {
      this._onActivate(pill, () => this.mapMgr.switchLayer(pill.dataset.layer));
    });

    document.querySelectorAll(".view-toggle button").forEach((btn) => {
      btn.addEventListener("click", () => this.setViewMode(btn.dataset.view));
    });

    document
      .getElementById("time-filter")
      .addEventListener("change", () => this.refreshMap());

    document
      .getElementById("timeline-slider")
      .addEventListener("input", (e) => this.onTimelineChange(e.target.value));

    document
      .getElementById("self-track-btn")
      .addEventListener("click", () => this.toggleSelfTracking());

    const actions = {
      refresh: () => this.refreshMap(),
      fit: () => this.mapMgr.fitAllLayers(),
      export: () => this.exportData(),
    };
    document.querySelectorAll("[data-action]").forEach((btn) => {
      const fn = actions[btn.dataset.action];
      if (fn) btn.addEventListener("click", fn);
    });

    // Delegated: person cards are rendered dynamically.
    const focusFromCard = (card) => {
      const person = this.state.peopleOrder[Number(card.dataset.personIndex)];
      if (person != null) this.mapMgr.focusPerson(person);
    };
    const peopleList = document.getElementById("people-list");
    peopleList.addEventListener("click", (e) => {
      const card = e.target.closest(".person-card");
      if (card) focusFromCard(card);
    });
    peopleList.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const card = e.target.closest(".person-card");
      if (card) {
        e.preventDefault();
        focusFromCard(card);
      }
    });
  }

  _onActivate(el, handler) {
    el.addEventListener("click", handler);
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        handler(e);
      }
    });
  }

  setViewMode(mode) {
    this.state.viewMode = mode;
    document
      .querySelectorAll(".view-toggle button")
      .forEach((b) => b.classList.toggle("active", b.dataset.view === mode));
    if (this.state.currentData) this.renderMap();
  }

  toggleSidebar() {
    const sb = document.getElementById("sidebar");
    if (window.innerWidth <= 640) {
      sb.classList.toggle("expanded");
    } else {
      const btn = document.getElementById("sidebar-toggle");
      sb.classList.toggle("collapsed");
      const collapsed = sb.classList.contains("collapsed");
      btn.textContent = collapsed ? "‹" : "›";
      btn.classList.toggle("shifted", collapsed);
    }
  }

  async refreshMap() {
    const days = document.getElementById("time-filter").value;
    try {
      const result = await APIClient.getLocations(days);
      this.state.currentData = result.locations || result;
      this.state.speedInfo = result.speed_info || {};
      this.renderMap();
      this.renderPeopleList();
      this.loadStats();
    } catch (e) {
      console.error("Refresh failed:", e);
      Utils.showToast("Failed to refresh data", "error");
    }
  }

  _fingerprint(data) {
    let fp = "";
    for (const [person, pts] of Object.entries(data)) {
      fp += person + ":" + (pts ? pts.length : 0) + ",";
      if (pts && pts.length > 0) fp += pts[pts.length - 1].timestamp + ";";
    }
    return fp;
  }

  renderMap() {
    const data = this.state.currentData;
    if (!data) return;

    const sliderVal = parseInt(
      document.getElementById("timeline-slider").value,
    );
    const fp =
      this._fingerprint(data) + "|" + sliderVal + "|" + this.state.viewMode;
    if (fp === this.state.lastFingerprint) return;
    this.state.lastFingerprint = fp;

    this.mapMgr.clearOverlays();

    let allCoords = [];
    const people = Object.keys(data);

    people.forEach((person, idx) => {
      const color = CONFIG.COLORS[idx % CONFIG.COLORS.length];
      let points = data[person];
      if (!points || points.length === 0) return;

      // Timeline filtering
      if (sliderVal < 100 && points.length > 1) {
        const cutIdx = Math.max(
          1,
          Math.floor((points.length * sliderVal) / 100),
        );
        points = points.slice(0, cutIdx);
      }

      const group = L.featureGroup();
      const coords = points.map((p) => [p.latitude, p.longitude]);
      allCoords = allCoords.concat(coords);

      if (this.state.viewMode === "heatmap") {
        // Heatmap handled below after collecting all coords
      } else if (this.state.viewMode === "points") {
        points.forEach((p, i) => {
          const opacity = 0.3 + 0.7 * (i / points.length);
          L.circleMarker([p.latitude, p.longitude], {
            radius: 4,
            color: color,
            weight: 0,
            fillColor: color,
            fillOpacity: opacity,
          })
            .bindTooltip(`${new Date(p.timestamp).toLocaleString()}`, {
              direction: "top",
            })
            .addTo(group);
        });
      } else {
        this._drawPath(group, coords, color, person, points);
        this._drawStops(group, points, color);
      }

      this._drawLatest(group, points, color, person, idx);

      this.mapMgr.addPersonLayer(person, group);
    });

    if (this.state.viewMode === "heatmap" && allCoords.length > 0) {
      this.mapMgr.setHeatLayer(allCoords);
    }

    this.mapMgr.fitToAll(allCoords);
    this.updateTimelineLabels(data);
  }

  _drawPath(group, coords, color, person, points) {
    if (coords.length <= 1) return;
    const rawLine = L.polyline(coords, {
      color: color,
      weight: 3,
      opacity: 0.25,
      smoothFactor: 1.5,
      lineCap: "round",
      lineJoin: "round",
      dashArray: "4 6",
    }).addTo(group);

    const si = this.state.speedInfo[person] || Utils.speedInfo(points);
    const isWalking = si.label === "Stationary" || si.label === "Walking";

    if (!isWalking && coords.length >= 3) {
      APIClient.snapToRoads(coords).then((snapped) => {
        if (snapped && snapped.length > 1) {
          group.removeLayer(rawLine);
          L.polyline(snapped, {
            color: color,
            weight: 4,
            opacity: 0.7,
            smoothFactor: 1,
            lineCap: "round",
            lineJoin: "round",
          }).addTo(group);
        }
      });
    }
  }

  _drawStops(group, points, color) {
    const stops = Utils.computeStops(points);
    stops.forEach((stop) => {
      const mins = (new Date(stop.end) - new Date(stop.start)) / 60000;
      const radius =
        mins < 5 ? 3 : mins < 15 ? 5 : mins < 45 ? 8 : mins < 120 ? 11 : 15;
      const durStr =
        mins < 1
          ? "passing"
          : mins < 60
            ? Math.round(mins) + "m"
            : Math.round((mins / 60) * 10) / 10 + "h";

      L.circleMarker([stop.lat, stop.lon], {
        radius: radius,
        color: color,
        weight: 1.5,
        fillColor: color,
        fillOpacity: 0.25,
      })
        .bindTooltip(`<b>${Utils.escapeHTML(stop.address)}</b><br>${durStr}`, {
          direction: "top",
        })
        .addTo(group);
    });
  }

  _drawLatest(group, points, color, person, idx) {
    const latest = points[points.length - 1];
    const pulseDiv = L.divIcon({
      className: "",
      html: `<div style="position:relative;width:40px;height:40px;">
                <div class="pulse-ring" style="position:absolute;inset:0;border-radius:50%;border:2px solid ${color};opacity:0.6;"></div>
                <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:14px;height:14px;border-radius:50%;background:${color};border:3px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,0.4);"></div>
            </div>`,
      iconSize: [40, 40],
      iconAnchor: [20, 20],
    });
    if (latest.accuracy != null && latest.accuracy > 0) {
      L.circle([latest.latitude, latest.longitude], {
        radius: latest.accuracy,
        color: color,
        weight: 1,
        fillColor: color,
        fillOpacity: 0.06,
        dashArray: "4 4",
        interactive: false,
      }).addTo(group);
    }
    const accStr =
      latest.accuracy != null ? Math.round(latest.accuracy) + "m" : "";
    L.marker([latest.latitude, latest.longitude], { icon: pulseDiv })
      .bindPopup(
        `<div style="min-width:200px;">` +
          `<div style="font-weight:800;font-size:16px;margin-bottom:8px;color:#fff;">${Utils.escapeHTML(person)}</div>` +
          `<div style="color:#aaa;font-size:13px;margin-bottom:4px;">${Utils.escapeHTML(latest.address || "Unknown")}</div>` +
          `<div style="color:#777;font-size:11px;">${new Date(latest.timestamp).toLocaleString()}</div>` +
          (latest.battery != null
            ? `<div style="color:#777;font-size:11px;margin-top:3px;">Battery: ${latest.battery}%${latest.charging ? " (charging)" : ""}</div>`
            : "") +
          (accStr
            ? `<div style="color:#777;font-size:11px;margin-top:2px;">Accuracy: &plusmn;${accStr}</div>`
            : "") +
          `</div>`,
      )
      .addTo(group);

    if (idx === 0) {
      const banner = document.getElementById("latest-location-banner");
      banner.style.display = "block";
      document.getElementById("latest-address").textContent =
        latest.address || "Unknown location";
      document.getElementById("latest-time").textContent = Utils.timeAgo(
        latest.timestamp,
      );
      const battEl = document.getElementById("latest-batt");
      if (latest.battery != null) {
        battEl.textContent =
          "Battery: " +
          latest.battery +
          "%" +
          (latest.charging ? " (charging)" : "");
      } else {
        battEl.textContent = "";
      }
    }
  }

  updateTimelineLabels(data) {
    let earliest = null,
      latest = null;
    for (const pts of Object.values(data)) {
      if (!pts || pts.length === 0) continue;
      const first = new Date(pts[0].timestamp);
      const last = new Date(pts[pts.length - 1].timestamp);
      if (!earliest || first < earliest) earliest = first;
      if (!latest || last > latest) latest = last;
    }
    if (earliest) {
      document.getElementById("timeline-start").textContent =
        earliest.toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
        });
      document.getElementById("timeline-end").textContent =
        latest.toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
        });
    }
  }

  onTimelineChange(val) {
    const pct = parseInt(val);
    const data = this.state.currentData;
    if (pct >= 100) {
      document.getElementById("timeline-current").textContent = "Latest";
    } else if (data) {
      document.getElementById("timeline-current").textContent =
        this._timelineDateLabel(data, pct);
    } else {
      document.getElementById("timeline-current").textContent = pct + "%";
    }
    if (data) this.renderMap();
  }

  _timelineDateLabel(data, pct) {
    const allTimestamps = [];
    for (const pts of Object.values(data)) {
      if (!pts || pts.length === 0) continue;
      for (const p of pts) allTimestamps.push(new Date(p.timestamp).getTime());
    }
    if (allTimestamps.length === 0) return pct + "%";
    allTimestamps.sort((a, b) => a - b);
    const idx = Math.max(0, Math.floor((allTimestamps.length * pct) / 100) - 1);
    const d = new Date(allTimestamps[idx]);
    return (
      d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
      " " +
      d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    );
  }

  renderPeopleList() {
    const data = this.state.currentData || {};
    const container = document.getElementById("people-list");
    const people = Object.keys(data);
    this.state.peopleOrder = people;
    container.innerHTML = people
      .map((person, idx) => {
        const pts = data[person];
        const last = pts && pts.length > 0 ? pts[pts.length - 1] : null;
        const color = CONFIG.COLORS[idx % CONFIG.COLORS.length];
        const si = this.state.speedInfo[person] || Utils.speedInfo(pts);
        const spd = si.speed_kmh != null ? si.speed_kmh : si.speed;
        const speedStr = spd >= 1 ? Math.round(spd) + " km/h" : "";
        const isRecent =
          last && Date.now() - new Date(last.timestamp).getTime() < 3600000;
        return `<div class="person-card" data-person-index="${idx}" role="button" tabindex="0" aria-label="Focus map on ${Utils.escapeHTML(person)}">
            <div class="name">
                <span class="dot ${isRecent ? "live" : ""}" style="background:${color};--dot-rgb:${Utils.hexToRgb(color)}"></span>
                ${Utils.escapeHTML(person)}
                <span class="badge ${si.cls}">${si.label}${speedStr ? " " + speedStr : ""}</span>
            </div>
            <div class="meta">${last ? Utils.escapeHTML(last.address || "Unknown") : "No data"}</div>
            <div class="meta">${pts ? pts.length : 0} pts &middot; ${last ? Utils.timeAgo(last.timestamp) : "-"}</div>
        </div>`;
      })
      .join("");
  }

  async loadStats() {
    try {
      const stats = await APIClient.getStats();
      const panel = document.getElementById("stats-panel");
      let html = "";
      for (const [person, s] of Object.entries(stats)) {
        html += `<div style="margin-bottom:14px;">
                <div style="font-size:13px;font-weight:700;margin-bottom:8px;color:#fff;">${Utils.escapeHTML(person)}</div>
                <div class="stat-grid">
                    <div class="stat-card"><div class="stat-label">Distance</div><div class="stat-value">${s.total_distance_km}<span class="stat-unit"> km</span></div></div>
                    <div class="stat-card"><div class="stat-label">Stops</div><div class="stat-value">${s.total_stops}</div></div>
                    <div class="stat-card"><div class="stat-label">Dwell</div><div class="stat-value">${s.total_dwell_hours}<span class="stat-unit"> hrs</span></div></div>
                    <div class="stat-card"><div class="stat-label">Points</div><div class="stat-value">${s.total_points}</div></div>
                </div>
            </div>`;
      }
      panel.innerHTML =
        html ||
        '<div style="font-size:13px;color:var(--text-muted);">No data yet</div>';
    } catch (e) {
      console.warn(e);
    }
  }

  async loadPollStatus() {
    try {
      const data = await APIClient.getPollStatus();
      const el = document.getElementById("poll-status");
      const reasonMap = {
        "long stationary": "not moving",
        stationary: "settled",
        "recently stopped": "recently stopped",
        "just stopped": "just stopped",
        walking: "walking",
        driving: "driving",
        highway: "highway",
        departing: "departing",
        arriving: "arriving",
      };
      const reason = reasonMap[data.speed_category] || data.speed_category;
      const secs = data.current_interval;
      const label = secs >= 60 ? Math.round(secs / 60) + " min" : secs + "s";
      if (data.error) {
        el.innerHTML = `Polling every <span class="highlight">${label}</span> &middot; <span style="color:var(--red,#e74c3c)">error: ${Utils.escapeHTML(data.error)}</span>`;
      } else {
        el.innerHTML = `Polling every <span class="highlight">${label}</span> &middot; ${reason}`;
      }
    } catch (e) {
      console.warn(e);
    }
  }

  toggleSelfTracking() {
    const btn = document.getElementById("self-track-btn");
    const status = document.getElementById("self-track-status");
    if (!this.selfTracking.active) {
      if (!navigator.geolocation) {
        status.textContent = "Geolocation not supported";
        return;
      }
      if (!window.isSecureContext) {
        status.textContent = "Requires HTTPS or localhost";
        Utils.showToast(
          "Geolocation requires a secure origin. Use https:// or access via localhost.",
          "error",
        );
        return;
      }
      this.selfTracking.active = true;
      btn.textContent = "Disable My Location";
      btn.classList.add("active");
      status.className = "status-pill active";
      status.textContent = "Tracking your location";
      this.sendSelfLocation();
      this.selfTracking.intervalId = setInterval(
        () => this.sendSelfLocation(),
        CONFIG.INTERVALS.SELF,
      );
      Utils.showToast("Self-tracking enabled", "success");
    } else {
      this.selfTracking.active = false;
      btn.textContent = "Enable My Location";
      btn.classList.remove("active");
      status.className = "status-pill";
      status.textContent = "Not tracking your location";
      if (this.selfTracking.intervalId)
        clearInterval(this.selfTracking.intervalId);
      this.mapMgr.clearSelfMarker();
      Utils.showToast("Self-tracking disabled", "info");
    }
  }

  sendSelfLocation() {
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const payload = {
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        };
        try {
          await APIClient.postSelfLocation(payload);
          this.mapMgr.setSelfMarker(pos.coords.latitude, pos.coords.longitude);
        } catch (e) {
          Utils.showToast("Failed to send location", "error");
        }
      },
      (err) => {
        Utils.showToast("Geolocation error: " + err.message, "error");
        document.getElementById("self-track-status").textContent =
          "Error: " + err.message;
      },
      { enableHighAccuracy: true, maximumAge: 30000 },
    );
  }

  async exportData() {
    const fmt = document.getElementById("export-format").value;
    const extMap = { json: "json", csv: "csv", geojson: "geojson" };
    try {
      const blob = await APIClient.exportBlob(fmt);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download =
        "location-history-" +
        new Date().toISOString().slice(0, 10) +
        "." +
        (extMap[fmt] || "json");
      a.click();
      URL.revokeObjectURL(a.href);
      Utils.showToast(
        "Export downloaded (" + fmt.toUpperCase() + ")",
        "success",
      );
    } catch (e) {
      Utils.showToast("Export failed", "error");
    }
  }
}

window.app = new App();
