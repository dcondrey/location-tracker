const COLORS = [
  "#4f6df5",
  "#ef4444",
  "#2dd4a8",
  "#eab308",
  "#a855f7",
  "#f97316",
  "#06d6a0",
  "#ec4899",
];
function esc(s) {
  const d = document.createElement("div");
  d.textContent = s || "";
  return d.innerHTML;
}
function showToast(msg, type) {
  type = type || "info";
  const el = document.createElement("div");
  el.className = "toast toast-" + type;
  el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  setTimeout(() => el.remove(), 3200);
}
let map,
  layers = {},
  heatLayer = null;
let selfTrackingActive = false,
  selfTrackInterval = null,
  selfMarker = null;
let currentBaseLayer = null,
  firstLoad = true,
  currentData = null,
  viewMode = "path";
let lastDataFingerprint = "",
  lastViewMode = "";

/* Google Maps tiles (no API key needed for basic raster tiles) */
const tileLayers = {
  roadmap: L.tileLayer("https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}", {
    maxZoom: 22,
    attribution: "&copy; Google",
  }),
  satellite: L.tileLayer("https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}", {
    maxZoom: 22,
    attribution: "&copy; Google",
  }),
  hybrid: L.tileLayer("https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}", {
    maxZoom: 22,
    attribution: "&copy; Google",
  }),
  terrain: L.tileLayer("https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}", {
    maxZoom: 22,
    attribution: "&copy; Google",
  }),
  dark: L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    { maxZoom: 20, attribution: "&copy; CARTO" },
  ),
};

function initMap() {
  map = L.map("map", { zoomControl: false, attributionControl: false }).setView(
    [37.7749, -122.4194],
    13,
  );
  L.control.zoom({ position: "bottomleft" }).addTo(map);
  L.control.attribution({ position: "bottomleft", prefix: false }).addTo(map);
  switchMapLayer("roadmap");
  refreshMap();
  const refreshId = setInterval(refreshMap, 30000);
  loadPollStatus();
  const pollStatusId = setInterval(loadPollStatus, 15000);
  window.addEventListener("beforeunload", () => {
    clearInterval(refreshId);
    clearInterval(pollStatusId);
  });
}

function switchMapLayer(name) {
  if (currentBaseLayer) map.removeLayer(currentBaseLayer);
  currentBaseLayer = tileLayers[name];
  currentBaseLayer.addTo(map);
  document
    .querySelectorAll(".map-pill")
    .forEach((p) => p.classList.toggle("active", p.dataset.layer === name));
}

function setViewMode(mode) {
  viewMode = mode;
  document
    .querySelectorAll(".view-toggle button")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === mode));
  if (currentData) renderMap(currentData);
}

function toggleSidebar() {
  const sb = document.getElementById("sidebar");
  const isMobile = window.innerWidth <= 640;
  if (isMobile) {
    sb.classList.toggle("expanded");
  } else {
    const btn = document.getElementById("sidebar-toggle");
    sb.classList.toggle("collapsed");
    const collapsed = sb.classList.contains("collapsed");
    btn.textContent = collapsed ? "\\u2039" : "\\u203a";
    btn.classList.toggle("shifted", collapsed);
  }
}

let currentSpeedInfo = {};

async function refreshMap() {
  const days = document.getElementById("time-filter").value;
  try {
    const resp = await fetch("/api/locations?days=" + days);
    const result = await resp.json();
    const data = result.locations || result;
    currentSpeedInfo = result.speed_info || {};
    currentData = data;
    renderMap(data);
    renderPeopleList(data);
    loadStats();
  } catch (e) {
    console.error("Refresh failed:", e);
    showToast("Failed to refresh data", "error");
  }
}

function dataFingerprint(data) {
  let fp = "";
  for (const [person, pts] of Object.entries(data)) {
    fp += person + ":" + (pts ? pts.length : 0) + ",";
    if (pts && pts.length > 0) fp += pts[pts.length - 1].timestamp + ";";
  }
  return fp;
}

function renderMap(data) {
  const sliderVal = parseInt(document.getElementById("timeline-slider").value);
  const fp = dataFingerprint(data) + "|" + sliderVal + "|" + viewMode;
  if (fp === lastDataFingerprint) return;
  lastDataFingerprint = fp;

  Object.values(layers).forEach((l) => map.removeLayer(l));
  layers = {};
  if (heatLayer) {
    map.removeLayer(heatLayer);
    heatLayer = null;
  }

  let allCoords = [];
  const people = Object.keys(data);

  people.forEach((person, idx) => {
    const color = COLORS[idx % COLORS.length];
    let points = data[person];
    if (!points || points.length === 0) return;

    // Timeline filtering
    if (sliderVal < 100 && points.length > 1) {
      const cutIdx = Math.max(1, Math.floor((points.length * sliderVal) / 100));
      points = points.slice(0, cutIdx);
    }

    const group = L.layerGroup();
    const coords = points.map((p) => [p.latitude, p.longitude]);
    allCoords = allCoords.concat(coords);

    if (viewMode === "heatmap") {
      // Heatmap handled below after collecting all coords
    } else if (viewMode === "points") {
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
      // Path view: segmented path with increasing opacity
      if (coords.length > 1) {
        const segSize = Math.max(1, Math.floor(coords.length / 5));
        for (let s = 0; s < coords.length - 1; s += segSize) {
          const seg = coords.slice(s, Math.min(s + segSize + 1, coords.length));
          const opacity = 0.2 + 0.6 * (s / coords.length);
          if (seg.length > 1) {
            L.polyline(seg, {
              color: color,
              weight: 3.5,
              opacity: opacity,
              smoothFactor: 1.5,
              lineCap: "round",
              lineJoin: "round",
            }).addTo(group);
          }
        }
      }

      // Stop nodes
      const stops = computeStops(points);
      stops.forEach((stop) => {
        const mins = (new Date(stop.end) - new Date(stop.start)) / 60000;
        let radius =
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
          .bindTooltip(`<b>${esc(stop.address)}</b><br>${durStr}`, {
            direction: "top",
          })
          .addTo(group);
      });
    }

    // Latest position marker (always shown)
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
          `<div style="font-weight:800;font-size:16px;margin-bottom:8px;color:#fff;">${esc(person)}</div>` +
          `<div style="color:#aaa;font-size:13px;margin-bottom:4px;">${esc(latest.address || "Unknown")}</div>` +
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

    // Update banner for first person
    if (idx === 0) {
      const banner = document.getElementById("latest-location-banner");
      banner.style.display = "block";
      document.getElementById("latest-address").textContent =
        latest.address || "Unknown location";
      document.getElementById("latest-time").textContent = timeAgo(
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

    group.addTo(map);
    layers[person] = group;
  });

  // Heatmap rendering
  if (viewMode === "heatmap" && allCoords.length > 0) {
    const heatData = allCoords.map((c) => [c[0], c[1], 0.6]);
    heatLayer = L.heatLayer(heatData, {
      radius: 20,
      blur: 25,
      maxZoom: 17,
      gradient: {
        0.2: "#4f6df5",
        0.4: "#2dd4a8",
        0.6: "#eab308",
        0.8: "#f97316",
        1.0: "#ef4444",
      },
    }).addTo(map);
  }

  if (allCoords.length > 0 && firstLoad) {
    map.fitBounds(L.latLngBounds(allCoords).pad(0.08));
    firstLoad = false;
  }

  // Update timeline labels
  updateTimelineLabels(data);
}

function updateTimelineLabels(data) {
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
      latest.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
}

function onTimelineChange(val) {
  const pct = parseInt(val);
  if (pct >= 100) {
    document.getElementById("timeline-current").textContent = "Latest";
  } else if (currentData) {
    const dateLabel = getTimelineDateLabel(currentData, pct);
    document.getElementById("timeline-current").textContent = dateLabel;
  } else {
    document.getElementById("timeline-current").textContent = pct + "%";
  }
  if (currentData) renderMap(currentData);
}

function getTimelineDateLabel(data, pct) {
  let allTimestamps = [];
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

function timeAgo(ts) {
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60) return "Just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  return Math.floor(diff / 86400) + "d ago";
}

function computeStops(points) {
  if (points.length === 0) return [];
  let stops = [];
  let cur = {
    lat: points[0].latitude,
    lon: points[0].longitude,
    start: points[0].timestamp,
    end: points[0].timestamp,
    address: points[0].address,
    count: 1,
  };
  for (let i = 1; i < points.length; i++) {
    const dist = haversine(
      cur.lon,
      cur.lat,
      points[i].longitude,
      points[i].latitude,
    );
    if (dist < 25) {
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
}

function haversine(lon1, lat1, lon2, lat2) {
  const R = 6371000,
    toRad = (x) => (x * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1),
    dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.asin(Math.sqrt(a));
}

function getSpeedInfo(points) {
  if (!points || points.length < 2)
    return { speed: 0, label: "Stationary", cls: "badge-stationary" };
  const last = points[points.length - 1],
    prev = points[points.length - 2];
  const dist = haversine(
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
}

function renderPeopleList(data) {
  const container = document.getElementById("people-list");
  const people = Object.keys(data);
  container.innerHTML = people
    .map((person, idx) => {
      const pts = data[person];
      const last = pts && pts.length > 0 ? pts[pts.length - 1] : null;
      const color = COLORS[idx % COLORS.length];
      const si = currentSpeedInfo[person] || getSpeedInfo(pts);
      const spd = si.speed_kmh != null ? si.speed_kmh : si.speed;
      const speedStr = spd >= 1 ? Math.round(spd) + " km/h" : "";
      const isRecent =
        last && Date.now() - new Date(last.timestamp).getTime() < 3600000;
      return `<div class="person-card" onclick="focusPerson('${esc(person)}')">
            <div class="name">
                <span class="dot ${isRecent ? "live" : ""}" style="background:${color};--dot-rgb:${hexToRgb(color)}"></span>
                ${esc(person)}
                <span class="badge ${si.cls}">${si.label}${speedStr ? " " + speedStr : ""}</span>
            </div>
            <div class="meta">${last ? esc(last.address || "Unknown") : "No data"}</div>
            <div class="meta">${pts ? pts.length : 0} pts &middot; ${last ? timeAgo(last.timestamp) : "-"}</div>
        </div>`;
    })
    .join("");
}

function hexToRgb(hex) {
  const r = parseInt(hex.slice(1, 3), 16),
    g = parseInt(hex.slice(3, 5), 16),
    b = parseInt(hex.slice(5, 7), 16);
  return r + "," + g + "," + b;
}

function focusPerson(person) {
  const layer = layers[person];
  if (layer) map.fitBounds(layer.getBounds().pad(0.15));
}

function fitBounds() {
  let allBounds = [];
  Object.values(layers).forEach((l) => {
    try {
      allBounds.push(l.getBounds());
    } catch (e) {
      console.warn(e);
    }
  });
  if (allBounds.length > 0) {
    let combined = allBounds[0];
    allBounds.slice(1).forEach((b) => combined.extend(b));
    map.fitBounds(combined.pad(0.1));
  }
}

async function loadStats() {
  try {
    const resp = await fetch("/api/stats");
    const stats = await resp.json();
    const panel = document.getElementById("stats-panel");
    let html = "";
    for (const [person, s] of Object.entries(stats)) {
      html += `<div style="margin-bottom:14px;">
                <div style="font-size:13px;font-weight:700;margin-bottom:8px;color:#fff;">${esc(person)}</div>
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

async function loadPollStatus() {
  try {
    const resp = await fetch("/api/poll-status");
    const data = await resp.json();
    const el = document.getElementById("poll-status");
    const mins = Math.round(data.current_interval / 60);
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
    el.innerHTML = `Polling every <span class="highlight">${label}</span> &middot; ${reason}`;
  } catch (e) {
    console.warn(e);
  }
}

function toggleSelfTracking() {
  const btn = document.getElementById("self-track-btn");
  const status = document.getElementById("self-track-status");
  if (!selfTrackingActive) {
    if (!navigator.geolocation) {
      status.textContent = "Geolocation not supported";
      return;
    }
    selfTrackingActive = true;
    btn.textContent = "Disable My Location";
    btn.classList.add("active");
    status.className = "status-pill active";
    status.textContent = "Tracking your location";
    sendSelfLocation();
    selfTrackInterval = setInterval(sendSelfLocation, 60000);
    showToast("Self-tracking enabled", "success");
  } else {
    selfTrackingActive = false;
    btn.textContent = "Enable My Location";
    btn.classList.remove("active");
    status.className = "status-pill";
    status.textContent = "Not tracking your location";
    if (selfTrackInterval) clearInterval(selfTrackInterval);
    if (selfMarker) {
      map.removeLayer(selfMarker);
      selfMarker = null;
    }
    showToast("Self-tracking disabled", "info");
  }
}

function sendSelfLocation() {
  navigator.geolocation.getCurrentPosition(
    async (pos) => {
      const payload = {
        latitude: pos.coords.latitude,
        longitude: pos.coords.longitude,
        accuracy: pos.coords.accuracy,
      };
      try {
        await fetch("/api/self-location", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (selfMarker) map.removeLayer(selfMarker);
        const selfIcon = L.divIcon({
          className: "",
          html: `<div style="position:relative;width:32px;height:32px;">
                    <div class="pulse-ring" style="position:absolute;inset:0;border-radius:50%;border:2px solid var(--green);"></div>
                    <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:12px;height:12px;border-radius:50%;background:var(--green);border:2px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,0.3);"></div>
                </div>`,
          iconSize: [32, 32],
          iconAnchor: [16, 16],
        });
        selfMarker = L.marker([pos.coords.latitude, pos.coords.longitude], {
          icon: selfIcon,
        })
          .bindPopup(
            '<b style="color:#fff;">You</b><br><span style="color:#999;">Updated just now</span>',
          )
          .addTo(map);
      } catch (e) {
        showToast("Failed to send location", "error");
      }
    },
    (err) => {
      showToast("Geolocation error: " + err.message, "error");
      document.getElementById("self-track-status").textContent =
        "Error: " + err.message;
    },
    { enableHighAccuracy: true, maximumAge: 30000 },
  );
}

async function exportData() {
  const fmt = document.getElementById("export-format").value;
  const extMap = { json: "json", csv: "csv", geojson: "geojson" };
  try {
    const resp = await fetch("/api/export?format=" + fmt);
    const blob = await resp.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download =
      "location-history-" +
      new Date().toISOString().slice(0, 10) +
      "." +
      (extMap[fmt] || "json");
    a.click();
    URL.revokeObjectURL(a.href);
    showToast("Export downloaded (" + fmt.toUpperCase() + ")", "success");
  } catch (e) {
    showToast("Export failed", "error");
  }
}

initMap();
