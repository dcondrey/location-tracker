"""Local road snapping using OpenStreetMap data via osmnx + networkx.

Downloads the road network for the tracking area, caches it locally,
and snaps GPS traces to actual road segments. No external API needed.
"""

import logging
import pickle
from pathlib import Path

import networkx as nx
import osmnx as ox

log = logging.getLogger(__name__)

APP_DIR = Path.home() / ".local" / "share" / "location-tracker"
GRAPH_CACHE = APP_DIR / "road_network.graphml"
GRAPH_PICKLE = APP_DIR / "road_network.pkl"
BUFFER_M = 2000


class RoadSnapper:
    def __init__(self):
        self._graph = None
        self._graph_proj = None

    def _ensure_graph(self, center_lat, center_lon):
        """Load or download the road network graph centered on the tracking area."""
        if self._graph is not None:
            return

        if GRAPH_PICKLE.exists():
            try:
                with open(GRAPH_PICKLE, "rb") as f:
                    data = pickle.load(f)  # noqa: S301
                self._graph = data["graph"]
                self._graph_proj = data["graph_proj"]
                log.info("Road network loaded from cache.")
                return
            except Exception as e:
                log.warning("Failed to load cached road network: %s", e)

        log.info("Downloading road network for (%.4f, %.4f)...", center_lat, center_lon)
        self._graph = ox.graph_from_point(
            (center_lat, center_lon),
            dist=5000,
            network_type="drive",
            simplify=True,
        )
        self._graph_proj = ox.project_graph(self._graph)

        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(GRAPH_PICKLE, "wb") as f:
            pickle.dump({"graph": self._graph, "graph_proj": self._graph_proj}, f)
        log.info(
            "Road network cached (%d nodes, %d edges).", self._graph.number_of_nodes(), self._graph.number_of_edges()
        )

    def expand_graph(self, lat, lon):
        """Expand the graph if a point is outside the current coverage."""
        if self._graph is None:
            self._ensure_graph(lat, lon)
            return

        try:
            ox.nearest_nodes(self._graph, lon, lat)
        except Exception:
            log.info("Point (%.4f, %.4f) outside road network, expanding...", lat, lon)
            new_graph = ox.graph_from_point((lat, lon), dist=5000, network_type="drive", simplify=True)
            self._graph = nx.compose(self._graph, new_graph)
            self._graph_proj = ox.project_graph(self._graph)
            with open(GRAPH_PICKLE, "wb") as f:
                pickle.dump({"graph": self._graph, "graph_proj": self._graph_proj}, f)

    def snap_trace(self, coords):
        """Snap a list of [lat, lon] coordinates to the road network.

        Returns a list of [lat, lon] points that follow actual roads.
        """
        if not coords or len(coords) < 2:
            return coords

        self._ensure_graph(coords[0][0], coords[0][1])

        try:
            snapped_nodes = []
            for lat, lon in coords:
                node = ox.nearest_nodes(self._graph, lon, lat)
                if not snapped_nodes or node != snapped_nodes[-1]:
                    snapped_nodes.append(node)

            if len(snapped_nodes) < 2:
                return coords

            route_points = []
            for i in range(len(snapped_nodes) - 1):
                try:
                    path = nx.shortest_path(self._graph, snapped_nodes[i], snapped_nodes[i + 1], weight="length")
                    for node_id in path:
                        node_data = self._graph.nodes[node_id]
                        pt = [node_data["y"], node_data["x"]]
                        if not route_points or pt != route_points[-1]:
                            route_points.append(pt)
                except nx.NetworkXNoPath:
                    node_data = self._graph.nodes[snapped_nodes[i + 1]]
                    route_points.append([node_data["y"], node_data["x"]])

            return route_points if len(route_points) >= 2 else coords

        except Exception as e:
            log.warning("Road snap failed: %s", e)
            return coords


_snapper = None


def get_snapper():
    global _snapper
    if _snapper is None:
        _snapper = RoadSnapper()
    return _snapper
