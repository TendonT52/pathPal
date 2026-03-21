import math
import os
from collections import defaultdict
from datetime import time as time_type
from dotenv import load_dotenv
from segmentation import haversine

load_dotenv()

MATCH_RADIUS = float(os.getenv("MATCH_RADIUS_METERS", 300))
SCORE_THRESHOLD = float(os.getenv("MATCH_SCORE_THRESHOLD", 0.75))


def get_time_bucket(t):
    """Return time-of-day bucket for a time or datetime object."""
    hour = t.hour if hasattr(t, "hour") else 0
    if 5 <= hour < 11:
        return "Morning"
    elif 11 <= hour < 17:
        return "Afternoon"
    elif 17 <= hour < 22:
        return "Evening"
    else:
        return "Night"


def score_pair(seg_a, seg_b, radius_m=MATCH_RADIUS):
    """
    Score similarity of two segments (dicts with start_lat/lon, end_lat/lon, start_time).
    Returns float 0.0–1.0.
    """
    start_dist = haversine(seg_a["start_lat"], seg_a["start_lon"],
                           seg_b["start_lat"], seg_b["start_lon"])
    end_dist = haversine(seg_a["end_lat"], seg_a["end_lon"],
                         seg_b["end_lat"], seg_b["end_lon"])

    start_score = max(0.0, 1.0 - start_dist / radius_m)
    end_score = max(0.0, 1.0 - end_dist / radius_m)

    time_bonus = 0.0
    if seg_a.get("start_time") and seg_b.get("start_time"):
        if get_time_bucket(seg_a["start_time"]) == get_time_bucket(seg_b["start_time"]):
            time_bonus = 0.2

    return min(1.0, 0.45 * start_score + 0.45 * end_score + 0.1 * time_bonus)


def run_matching(db_session):
    """
    Compare all segments across different users.
    Insert SharedRoute rows for pairs scoring >= threshold.
    Returns count of new SharedRoute rows inserted.
    """
    from models import Segment, SharedRoute

    all_segments = db_session.query(Segment).all()

    # Build grid buckets keyed by (round(start_lat,3), round(start_lon,3))
    buckets = defaultdict(list)
    for seg in all_segments:
        key = (round(seg.start_lat, 3), round(seg.start_lon, 3))
        buckets[key].append(seg)

    # Collect existing pairs to avoid duplicates
    existing_pairs = set()
    for sr in db_session.query(SharedRoute).all():
        existing_pairs.add((min(sr.segment_id_a, sr.segment_id_b),
                            max(sr.segment_id_a, sr.segment_id_b)))

    count = 0
    checked_pairs = set()

    # Each bucket cell is 0.001° ≈ 111m. Expand search to cover MATCH_RADIUS in all directions.
    grid_r = math.ceil(MATCH_RADIUS / 111) + 1
    steps = [i * 0.001 for i in range(-grid_r, grid_r + 1)]

    for key, segs in buckets.items():
        # Include same-bucket segments, then all neighbouring buckets within the search radius
        neighbour_segs = list(segs)
        for dlat in steps:
            for dlon in steps:
                if dlat == 0.0 and dlon == 0.0:
                    continue
                nk = (round(key[0] + dlat, 3), round(key[1] + dlon, 3))
                neighbour_segs.extend(buckets.get(nk, []))

        for i, seg_a in enumerate(segs):
            for seg_b in neighbour_segs:
                if seg_a.id == seg_b.id:
                    continue
                if seg_a.user_id == seg_b.user_id:
                    continue
                pair = (min(seg_a.id, seg_b.id), max(seg_a.id, seg_b.id))
                if pair in checked_pairs or pair in existing_pairs:
                    continue
                checked_pairs.add(pair)

                a_dict = {
                    "start_lat": seg_a.start_lat, "start_lon": seg_a.start_lon,
                    "end_lat": seg_a.end_lat, "end_lon": seg_a.end_lon,
                    "start_time": seg_a.start_time,
                }
                b_dict = {
                    "start_lat": seg_b.start_lat, "start_lon": seg_b.start_lon,
                    "end_lat": seg_b.end_lat, "end_lon": seg_b.end_lon,
                    "start_time": seg_b.start_time,
                }
                score = score_pair(a_dict, b_dict)
                if score >= SCORE_THRESHOLD:
                    sr = SharedRoute(
                        segment_id_a=seg_a.id,
                        segment_id_b=seg_b.id,
                        similarity_score=score,
                        canonical_start_lat=(seg_a.start_lat + seg_b.start_lat) / 2,
                        canonical_start_lon=(seg_a.start_lon + seg_b.start_lon) / 2,
                        canonical_end_lat=(seg_a.end_lat + seg_b.end_lat) / 2,
                        canonical_end_lon=(seg_a.end_lon + seg_b.end_lon) / 2,
                    )
                    db_session.add(sr)
                    existing_pairs.add(pair)
                    count += 1

    db_session.commit()
    return count
