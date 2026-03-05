"""Watch OSM augmented diffs for accidental node drags."""

import math
import xml.etree.ElementTree as ET


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance in meters between two lat/lon points."""
    R = 6_371_000  # Earth radius in meters
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def detect_node_drags(root, threshold_meters=10):
    """Detect single-node drags in an augmented diff XML tree.

    Returns a list of dicts with info about each detected drag.
    """
    drags = []

    for action in root.findall("action"):
        if action.get("type") != "modify":
            continue

        old = action.find("old")
        new = action.find("new")
        if old is None or new is None:
            continue

        old_way = old.find("way")
        new_way = new.find("way")
        if old_way is None or new_way is None:
            continue

        old_nds = {
            nd.get("ref"): (float(nd.get("lat")), float(nd.get("lon")))
            for nd in old_way.findall("nd")
        }
        new_nds = {
            nd.get("ref"): (float(nd.get("lat")), float(nd.get("lon")))
            for nd in new_way.findall("nd")
        }

        # Only check nodes present in both old and new
        common_refs = set(old_nds) & set(new_nds)
        if len(common_refs) < 3:
            continue

        moved = []
        for ref in common_refs:
            old_lat, old_lon = old_nds[ref]
            new_lat, new_lon = new_nds[ref]
            dist = haversine_distance(old_lat, old_lon, new_lat, new_lon)
            if dist >= threshold_meters:
                moved.append((ref, dist))

        if len(moved) == 1:
            node_ref, distance = moved[0]
            way_name = ""
            for tag in new_way.findall("tag"):
                if tag.get("k") == "name":
                    way_name = tag.get("v", "")
                    break

            # Get changeset/user from the new way element
            changeset = new_way.get("changeset", "")
            user = new_way.get("user", "")

            drags.append({
                "way_id": new_way.get("id"),
                "way_name": way_name,
                "node_id": node_ref,
                "distance_meters": round(distance, 1),
                "changeset": changeset,
                "user": user,
            })

    return drags
