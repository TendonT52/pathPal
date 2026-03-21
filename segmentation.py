import os
import math
import logging
from datetime import datetime, date, time
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

DWELL_MINUTES = int(os.getenv("SEGMENT_DWELL_MINUTES", 15))
MIN_SEGMENT_MINUTES = 3
MIN_SEGMENT_METRES = 200
STOP_RADIUS_METRES = 100


# ---------------------------------------------------------------------------
# Haversine
# ---------------------------------------------------------------------------

def haversine(lat1, lon1, lat2, lon2):
    """Return distance in metres between two lat/lon points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Stop detection
# ---------------------------------------------------------------------------

def detect_stops(points, dwell_minutes=DWELL_MINUTES):
    """
    points: list of dicts with keys 'lat', 'lon', 'recorded_at' (datetime).
    Returns list of stop dicts: {centroid_lat, centroid_lon, start_time, end_time, point_indices}.
    """
    if not points:
        return []

    stops = []
    i = 0
    while i < len(points):
        cluster = [i]
        j = i + 1
        while j < len(points):
            d = haversine(points[i]["lat"], points[i]["lon"],
                          points[j]["lat"], points[j]["lon"])
            if d <= STOP_RADIUS_METRES:
                cluster.append(j)
                j += 1
            else:
                break

        if len(cluster) >= 2:
            t_start = points[cluster[0]]["recorded_at"]
            t_end = points[cluster[-1]]["recorded_at"]
            duration = (t_end - t_start).total_seconds() / 60
            if duration >= dwell_minutes:
                lats = [points[k]["lat"] for k in cluster]
                lons = [points[k]["lon"] for k in cluster]
                stops.append({
                    "centroid_lat": sum(lats) / len(lats),
                    "centroid_lon": sum(lons) / len(lons),
                    "start_time": t_start,
                    "end_time": t_end,
                    "point_indices": cluster,
                })
                i = cluster[-1] + 1
                continue

        i += 1

    return stops


# ---------------------------------------------------------------------------
# Segment extraction
# ---------------------------------------------------------------------------

def extract_segments(points, stops):
    """
    Returns list of segment dicts:
    {start_lat, start_lon, end_lat, end_lon, start_time, end_time}
    """
    segments = []
    for idx in range(len(stops) - 1):
        stop_a = stops[idx]
        stop_b = stops[idx + 1]

        seg_start_time = stop_a["end_time"]
        seg_end_time = stop_b["start_time"]

        duration_min = (seg_end_time - seg_start_time).total_seconds() / 60
        dist_m = haversine(stop_a["centroid_lat"], stop_a["centroid_lon"],
                           stop_b["centroid_lat"], stop_b["centroid_lon"])

        if duration_min < MIN_SEGMENT_MINUTES or dist_m < MIN_SEGMENT_METRES:
            continue

        segments.append({
            "start_lat": stop_a["centroid_lat"],
            "start_lon": stop_a["centroid_lon"],
            "end_lat": stop_b["centroid_lat"],
            "end_lon": stop_b["centroid_lon"],
            "start_time": seg_start_time,
            "end_time": seg_end_time,
        })

    return segments


# ---------------------------------------------------------------------------
# Place merging — collapse co-located stops into one place
# ---------------------------------------------------------------------------

def merge_stops(stops, merge_radius=None):
    """
    Group time-ordered stops by physical proximity into distinct places.
    Two stops are the same place if their centroids are within merge_radius metres.

    Returns:
        places        — list of place dicts:
                        { centroid_lat, centroid_lon, visits: [stop, ...], visit_count }
        stop_to_place — list[int] mapping stops[i] -> places index
    """
    if merge_radius is None:
        merge_radius = STOP_RADIUS_METRES

    places = []
    stop_to_place = []

    for stop in stops:
        # Find the nearest existing place within merge_radius
        matched_idx = None
        best_dist = float("inf")
        for pi, place in enumerate(places):
            d = haversine(stop["centroid_lat"], stop["centroid_lon"],
                          place["centroid_lat"], place["centroid_lon"])
            if d <= merge_radius and d < best_dist:
                best_dist = d
                matched_idx = pi

        if matched_idx is not None:
            place = places[matched_idx]
            place["visits"].append(stop)
            place["visit_count"] = len(place["visits"])
            # Update centroid as mean of all visit centroids
            place["centroid_lat"] = sum(v["centroid_lat"] for v in place["visits"]) / place["visit_count"]
            place["centroid_lon"] = sum(v["centroid_lon"] for v in place["visits"]) / place["visit_count"]
            stop_to_place.append(matched_idx)
        else:
            places.append({
                "centroid_lat": stop["centroid_lat"],
                "centroid_lon": stop["centroid_lon"],
                "visits": [stop],
                "visit_count": 1,
            })
            stop_to_place.append(len(places) - 1)

    return places, stop_to_place


def label_places(places):
    """
    Assign Home/School/Area labels to merged places, ranked by visit_count.
    Returns a list of label strings in the same order as places.
    """
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    by_freq = sorted(range(len(places)), key=lambda i: -places[i]["visit_count"])
    labels = [""] * len(places)
    letter_idx = 0

    for rank, pi in enumerate(by_freq):
        if rank == 0:
            labels[pi] = "Home"
        elif rank == 1:
            morning_weekday = sum(
                1 for v in places[pi]["visits"]
                if isinstance(v["start_time"], datetime)
                and v["start_time"].weekday() < 5
                and v["start_time"].hour < 10
            )
            if morning_weekday > 0:
                labels[pi] = "School"
            else:
                labels[pi] = f"Area {letters[letter_idx]}"
                letter_idx += 1
        else:
            labels[pi] = f"Area {letters[letter_idx]}"
            letter_idx = min(letter_idx + 1, len(letters) - 1)

    return labels


# ---------------------------------------------------------------------------
# Label stops (legacy — kept for backward compatibility)
# ---------------------------------------------------------------------------

def label_stops(stops, existing_points_by_stop=None):
    """
    Wraps merge_stops + label_places.
    Returns dict mapping (rounded_lat, rounded_lon) -> label string.
    """
    if not stops:
        return {}
    places, stop_to_place = merge_stops(stops)
    place_labels = label_places(places)
    labels = {}
    for i, stop in enumerate(stops):
        pi = stop_to_place[i]
        key = (round(places[pi]["centroid_lat"], 3), round(places[pi]["centroid_lon"], 3))
        labels[key] = place_labels[pi]
    return labels


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def run_segmentation(user_id, db_session):
    """
    Fetch all GPS points for user, run full segmentation pipeline,
    save Segment rows. Returns count of segments created/updated.
    """
    from models import GpsPoint, Segment

    raw_points = (
        db_session.query(GpsPoint)
        .filter_by(user_id=user_id)
        .order_by(GpsPoint.recorded_at)
        .all()
    )

    if not raw_points:
        return 0

    points = [
        {"lat": p.latitude, "lon": p.longitude, "recorded_at": p.recorded_at}
        for p in raw_points
    ]

    stops = detect_stops(points, dwell_minutes=DWELL_MINUTES)
    if len(stops) < 2:
        return 0

    # Merge co-located stops (e.g. Home → School → Home gives 2 places, not 3)
    places, stop_to_place = merge_stops(stops)
    place_labels = label_places(places)

    count = 0
    for stop_idx in range(len(stops) - 1):
        stop_a = stops[stop_idx]
        stop_b = stops[stop_idx + 1]

        place_a_idx = stop_to_place[stop_idx]
        place_b_idx = stop_to_place[stop_idx + 1]

        # Skip trivial round-trips that merged to the same place
        if place_a_idx == place_b_idx:
            continue

        duration_min = (stop_b["start_time"] - stop_a["end_time"]).total_seconds() / 60
        dist_m = haversine(stop_a["centroid_lat"], stop_a["centroid_lon"],
                           stop_b["centroid_lat"], stop_b["centroid_lon"])

        if duration_min < MIN_SEGMENT_MINUTES or dist_m < MIN_SEGMENT_METRES:
            continue

        place_a = places[place_a_idx]
        place_b = places[place_b_idx]
        start_label = place_labels[place_a_idx]
        end_label   = place_labels[place_b_idx]

        seg_date    = stop_a["end_time"].date() if isinstance(stop_a["end_time"], datetime) else stop_a["end_time"]
        seg_start_t = stop_a["end_time"].time() if isinstance(stop_a["end_time"], datetime) else stop_a["end_time"]
        seg_end_t   = stop_b["start_time"].time() if isinstance(stop_b["start_time"], datetime) else stop_b["start_time"]

        start_lat = round(place_a["centroid_lat"], 4)
        start_lon = round(place_a["centroid_lon"], 4)
        end_lat   = round(place_b["centroid_lat"], 4)
        end_lon   = round(place_b["centroid_lon"], 4)

        existing = (
            db_session.query(Segment)
            .filter_by(user_id=user_id, start_label=start_label, end_label=end_label)
            .first()
        )

        if existing:
            existing.occurrence_count += 1
        else:
            new_seg = Segment(
                user_id=user_id,
                start_lat=start_lat,
                start_lon=start_lon,
                end_lat=end_lat,
                end_lon=end_lon,
                start_label=start_label,
                end_label=end_label,
                date=seg_date,
                start_time=seg_start_t,
                end_time=seg_end_t,
                occurrence_count=1,
            )
            db_session.add(new_seg)
            count += 1

    db_session.commit()
    return count
