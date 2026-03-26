import os
import secrets
import logging
import bcrypt
from datetime import datetime
from flask import Flask, jsonify, request, render_template
from dotenv import load_dotenv
from models import db, User, GpsPoint, Segment, SharedRoute, UserContact
from segmentation import run_segmentation

load_dotenv()

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///pathpal.db")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

with app.app_context():
    db.create_all()


def get_user_by_token(token):
    return User.query.filter_by(token=token).first()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/devui")
def devui():
    return render_template("devui.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "version": "1.0"})


# ---------------------------------------------------------------------------
# Step 4 — Register
# ---------------------------------------------------------------------------

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username:
        return jsonify({"error": "username is required"}), 400
    if not password:
        return jsonify({"error": "password is required"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "username already taken"}), 409

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    token = secrets.token_urlsafe(32)
    user = User(username=username, password_hash=password_hash, token=token)
    db.session.add(user)
    db.session.commit()
    return jsonify({"user_token": token, "user_id": user.id})


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username or not password:
        return jsonify({"error": "username and password are required"}), 400

    user = User.query.filter_by(username=username).first()
    if not user or not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        return jsonify({"error": "invalid username or password"}), 401

    return jsonify({"user_token": user.token, "user_id": user.id})


# ---------------------------------------------------------------------------
# Step 5 — GPS Upload
# ---------------------------------------------------------------------------

@app.route("/api/gps", methods=["POST"])
def upload_gps():
    data = request.get_json(silent=True) or {}
    user = get_user_by_token(data.get("user_token"))
    if not user:
        return jsonify({"error": "invalid token"}), 401

    saved = 0
    for pt in data.get("points", []):
        try:
            point = GpsPoint(
                user_id=user.id,
                latitude=float(pt["lat"]),
                longitude=float(pt["lon"]),
                recorded_at=datetime.fromisoformat(pt["ts"]),
            )
            db.session.add(point)
            saved += 1
        except Exception as e:
            logging.warning("Skipping malformed GPS point: %s — %s", pt, e)

    db.session.commit()
    return jsonify({"saved": saved})


# ---------------------------------------------------------------------------
# Step 7 — Segment Now
# ---------------------------------------------------------------------------

@app.route("/api/segment_preview", methods=["POST"])
def segment_preview():
    from segmentation import haversine, label_stops

    data = request.get_json(silent=True) or {}
    user = get_user_by_token(data.get("user_token"))
    if not user:
        return jsonify({"error": "invalid token"}), 401

    dwell_minutes  = float(data.get("dwell_minutes",  os.getenv("SEGMENT_DWELL_MINUTES", 15)))
    stop_radius    = float(data.get("stop_radius",    100))
    min_seg_min    = float(data.get("min_seg_minutes", 3))
    min_seg_m      = float(data.get("min_seg_metres",  200))

    raw_points = (
        GpsPoint.query
        .filter_by(user_id=user.id)
        .order_by(GpsPoint.recorded_at)
        .all()
    )
    points = [
        {"lat": p.latitude, "lon": p.longitude, "recorded_at": p.recorded_at}
        for p in raw_points
    ]

    # Inline stop detection (supports stop_radius override without modifying segmentation.py)
    stops = []
    i = 0
    while i < len(points):
        cluster = [i]
        j = i + 1
        while j < len(points):
            if haversine(points[i]["lat"], points[i]["lon"],
                         points[j]["lat"], points[j]["lon"]) <= stop_radius:
                cluster.append(j)
                j += 1
            else:
                break
        if len(cluster) >= 2:
            t_start = points[cluster[0]]["recorded_at"]
            t_end   = points[cluster[-1]]["recorded_at"]
            duration = (t_end - t_start).total_seconds() / 60
            if duration >= dwell_minutes:
                lats = [points[k]["lat"] for k in cluster]
                lons = [points[k]["lon"] for k in cluster]
                stops.append({
                    "centroid_lat": sum(lats) / len(lats),
                    "centroid_lon": sum(lons) / len(lons),
                    "start_time":   t_start,
                    "end_time":     t_end,
                    "duration_minutes": duration,
                    "point_indices": cluster,
                })
                i = cluster[-1] + 1
                continue
        i += 1

    from segmentation import merge_stops, label_places

    places, stop_to_place = merge_stops(stops, merge_radius=stop_radius)
    place_labels = label_places(places)

    def get_label(stop_idx):
        return place_labels[stop_to_place[stop_idx]]

    stops_out = [
        {
            "centroid_lat":     round(s["centroid_lat"], 4),
            "centroid_lon":     round(s["centroid_lon"], 4),
            "start_time":       s["start_time"].strftime("%H:%M"),
            "end_time":         s["end_time"].strftime("%H:%M"),
            "duration_minutes": round(s["duration_minutes"], 1),
            "point_count":      len(s["point_indices"]),
            "label":            get_label(i),
            "place_index":      stop_to_place[i],
            "point_indices":    s["point_indices"],
        }
        for i, s in enumerate(stops)
    ]

    places_out = [
        {
            "centroid_lat": round(p["centroid_lat"], 4),
            "centroid_lon": round(p["centroid_lon"], 4),
            "label":        place_labels[pi],
            "visit_count":  p["visit_count"],
            "stop_indices": [i for i, stp in enumerate(stop_to_place) if stp == pi],
        }
        for pi, p in enumerate(places)
    ]

    candidates = []
    for idx in range(len(stops) - 1):
        a, b = stops[idx], stops[idx + 1]
        pa_idx, pb_idx = stop_to_place[idx], stop_to_place[idx + 1]
        same_place = (pa_idx == pb_idx)
        dur  = (b["start_time"] - a["end_time"]).total_seconds() / 60
        dist = haversine(a["centroid_lat"], a["centroid_lon"],
                         b["centroid_lat"], b["centroid_lon"])

        if same_place:
            passed = False
            reason = f"same place after merge ({place_labels[pa_idx]})"
        elif dur < min_seg_min:
            passed = False
            reason = f"duration {dur:.1f} min < {min_seg_min} min minimum"
        elif dist < min_seg_m:
            passed = False
            reason = f"distance {dist:.0f} m < {min_seg_m} m minimum"
        else:
            passed = True
            reason = None

        candidates.append({
            "start_lat":        round(places[pa_idx]["centroid_lat"], 4),
            "start_lon":        round(places[pa_idx]["centroid_lon"], 4),
            "end_lat":          round(places[pb_idx]["centroid_lat"], 4),
            "end_lon":          round(places[pb_idx]["centroid_lon"], 4),
            "start_label":      place_labels[pa_idx],
            "end_label":        place_labels[pb_idx],
            "duration_minutes": round(dur, 1),
            "distance_metres":  round(dist, 1),
            "passed":           passed,
            "reject_reason":    reason,
        })

    return jsonify({
        "params": {
            "dwell_minutes":   dwell_minutes,
            "stop_radius":     stop_radius,
            "min_seg_minutes": min_seg_min,
            "min_seg_metres":  min_seg_m,
        },
        "total_points":       len(points),
        "stops_found":        len(stops),
        "places_found":       len(places),
        "stops":              stops_out,
        "merged_places":      places_out,
        "segment_candidates": candidates,
        "segments_passed":    sum(1 for c in candidates if c["passed"]),
        "segments_rejected":  sum(1 for c in candidates if not c["passed"]),
    })


@app.route("/api/segment_now", methods=["POST"])
def segment_now():
    data = request.get_json(silent=True) or {}
    user = get_user_by_token(data.get("user_token"))
    if not user:
        return jsonify({"error": "invalid token"}), 401

    count = run_segmentation(user.id, db.session)
    from matching import run_matching
    run_matching(db.session)
    return jsonify({"segments_created": count})


# ---------------------------------------------------------------------------
# GPS Points
# ---------------------------------------------------------------------------

@app.route("/api/gps_points")
def get_gps_points():
    user = get_user_by_token(request.args.get("token"))
    if not user:
        return jsonify({"error": "invalid token"}), 401

    points = (
        GpsPoint.query
        .filter_by(user_id=user.id)
        .order_by(GpsPoint.recorded_at)
        .all()
    )
    return jsonify({
        "total": len(points),
        "points": [
            {
                "id": p.id,
                "lat": p.latitude,
                "lon": p.longitude,
                "recorded_at": p.recorded_at.isoformat() if p.recorded_at else None,
                "index": i,
            }
            for i, p in enumerate(points)
        ],
    })


# ---------------------------------------------------------------------------
# Stops
# ---------------------------------------------------------------------------

@app.route("/api/stops")
def get_stops():
    from segmentation import detect_stops, merge_stops, label_places, haversine, DWELL_MINUTES

    user = get_user_by_token(request.args.get("token"))
    if not user:
        return jsonify({"error": "invalid token"}), 401

    raw_points = (
        GpsPoint.query
        .filter_by(user_id=user.id)
        .order_by(GpsPoint.recorded_at)
        .all()
    )
    points = [
        {"lat": p.latitude, "lon": p.longitude, "recorded_at": p.recorded_at}
        for p in raw_points
    ]

    stops = detect_stops(points, dwell_minutes=DWELL_MINUTES)
    places, stop_to_place = merge_stops(stops)
    place_labels = label_places(places)

    seen = set()
    names = []
    for i, stop in enumerate(stops):
        label = place_labels[stop_to_place[i]]
        if label not in seen:
            seen.add(label)
            names.append(label)

    return jsonify({"stops": names})


# ---------------------------------------------------------------------------
# Step 8 — Get Segments
# ---------------------------------------------------------------------------

@app.route("/api/segments")
def get_segments():
    user = get_user_by_token(request.args.get("token"))
    if not user:
        return jsonify({"error": "invalid token"}), 401

    segments = (
        Segment.query
        .filter_by(user_id=user.id)
        .order_by(Segment.date.desc(), Segment.start_time.desc())
        .all()
    )
    return jsonify({
        "segments": [
            {
                "id": s.id,
                "start_label": s.start_label,
                "end_label": s.end_label,
                "date": s.date.isoformat() if s.date else None,
                "start_time": s.start_time.strftime("%H:%M") if s.start_time else None,
                "end_time": s.end_time.strftime("%H:%M") if s.end_time else None,
                "occurrence_count": s.occurrence_count,
                "start_lat": s.start_lat,
                "start_lon": s.start_lon,
                "end_lat": s.end_lat,
                "end_lon": s.end_lon,
            }
            for s in segments
        ]
    })


# ---------------------------------------------------------------------------
# Step 10 — Shared Routes
# ---------------------------------------------------------------------------

@app.route("/api/shared")
def get_shared():
    user = get_user_by_token(request.args.get("token"))
    if not user:
        return jsonify({"error": "invalid token"}), 401

    user_segment_ids = {s.id for s in Segment.query.filter_by(user_id=user.id).all()}

    shared_routes = SharedRoute.query.filter(
        (SharedRoute.segment_id_a.in_(user_segment_ids)) |
        (SharedRoute.segment_id_b.in_(user_segment_ids))
    ).all()

    # Group by the user's segment id → collect matched other-user segment ids
    from collections import defaultdict
    matches = defaultdict(set)
    for sr in shared_routes:
        if sr.segment_id_a in user_segment_ids:
            other_seg = Segment.query.get(sr.segment_id_b)
            if other_seg:
                matches[sr.segment_id_a].add(other_seg.user_id)
        if sr.segment_id_b in user_segment_ids:
            other_seg = Segment.query.get(sr.segment_id_a)
            if other_seg:
                matches[sr.segment_id_b].add(other_seg.user_id)

    # For each matched segment, also track the best similarity score
    max_scores = defaultdict(float)
    for sr in shared_routes:
        if sr.segment_id_a in user_segment_ids:
            max_scores[sr.segment_id_a] = max(max_scores[sr.segment_id_a], sr.similarity_score)
        if sr.segment_id_b in user_segment_ids:
            max_scores[sr.segment_id_b] = max(max_scores[sr.segment_id_b], sr.similarity_score)

    result = []
    for seg_id, other_users in matches.items():
        seg = Segment.query.get(seg_id)
        result.append({
            "segment_id": seg_id,
            "start_label": seg.start_label,
            "end_label": seg.end_label,
            "match_count": len(other_users),
            "best_score": round(max_scores[seg_id], 3),
            "canonical_start_lat": seg.start_lat,
            "canonical_start_lon": seg.start_lon,
            "canonical_end_lat": seg.end_lat,
            "canonical_end_lon": seg.end_lon,
        })

    # Return top 3 by best similarity score, highest first
    result.sort(key=lambda x: x["best_score"], reverse=True)
    return jsonify({"shared": result[:3]})


# ---------------------------------------------------------------------------
# Step 11 — People on Route
# ---------------------------------------------------------------------------

@app.route("/api/people")
def get_people():
    user = get_user_by_token(request.args.get("token"))
    if not user:
        return jsonify({"error": "invalid token"}), 401

    segment_id = request.args.get("segment_id", type=int)
    seg = Segment.query.get(segment_id)
    if not seg or seg.user_id != user.id:
        return jsonify({"error": "forbidden"}), 403

    shared_routes = SharedRoute.query.filter(
        (SharedRoute.segment_id_a == segment_id) |
        (SharedRoute.segment_id_b == segment_id)
    ).all()

    people = []
    for sr in shared_routes:
        other_seg_id = sr.segment_id_b if sr.segment_id_a == segment_id else sr.segment_id_a
        other_seg = Segment.query.get(other_seg_id)
        if not other_seg or other_seg.user_id == user.id:
            continue
        other_user = User.query.get(other_seg.user_id)
        if not other_user or not other_user.is_visible:
            continue
        contacts = {
            c.contact_type: c.contact_value
            for c in UserContact.query.filter_by(user_id=other_user.id).all()
        }
        people.append({
            "display_name": other_user.display_name or other_user.username,
            "contacts": contacts,
            "days_on_route": other_seg.occurrence_count,
        })

    return jsonify({"people": people})


# ---------------------------------------------------------------------------
# Step 12 — Profile Update
# ---------------------------------------------------------------------------

@app.route("/api/profile", methods=["POST"])
def update_profile():
    data = request.get_json(silent=True) or {}
    user = get_user_by_token(data.get("user_token"))
    if not user:
        return jsonify({"error": "invalid token"}), 401

    if "display_name" in data:
        user.display_name = data["display_name"]
    if "instagram" in data:
        user.instagram = data["instagram"]
    if "is_visible" in data:
        user.is_visible = data["is_visible"]

    db.session.commit()
    return jsonify({"updated": True})


# ---------------------------------------------------------------------------
# Contact Update
# ---------------------------------------------------------------------------

@app.route("/api/contact", methods=["POST"])
def update_contact():
    data = request.get_json(silent=True) or {}
    user = get_user_by_token(data.get("user_token"))
    if not user:
        return jsonify({"error": "invalid token"}), 401

    contacts = data.get("contacts")
    if not isinstance(contacts, list):
        return jsonify({"error": "contacts must be a list"}), 400

    for entry in contacts:
        contact_type = str(entry.get("type", "")).strip().lower()
        contact_value = str(entry.get("value", "")).strip()
        if not contact_type:
            continue

        existing = UserContact.query.filter_by(
            user_id=user.id, contact_type=contact_type
        ).first()

        if not contact_value:
            # Empty value = delete this contact type
            if existing:
                db.session.delete(existing)
        elif existing:
            existing.contact_value = contact_value
        else:
            db.session.add(UserContact(
                user_id=user.id,
                contact_type=contact_type,
                contact_value=contact_value,
            ))

    db.session.commit()
    return jsonify({"updated": True})


# ---------------------------------------------------------------------------
# Clear GPS Points
# ---------------------------------------------------------------------------

@app.route("/api/gps", methods=["DELETE"])
def clear_gps_points():
    data = request.get_json(silent=True) or {}
    user = get_user_by_token(data.get("user_token"))
    if not user:
        return jsonify({"error": "invalid token"}), 401

    deleted = GpsPoint.query.filter_by(user_id=user.id).delete()
    db.session.commit()
    return jsonify({"deleted": deleted})


# ---------------------------------------------------------------------------
# Step 13 — Delete Data
# ---------------------------------------------------------------------------

@app.route("/api/data", methods=["DELETE"])
def delete_data():
    data = request.get_json(silent=True) or {}
    user = get_user_by_token(data.get("user_token"))
    if not user:
        return jsonify({"error": "invalid token"}), 401

    user_segment_ids = [s.id for s in Segment.query.filter_by(user_id=user.id).all()]

    SharedRoute.query.filter(
        (SharedRoute.segment_id_a.in_(user_segment_ids)) |
        (SharedRoute.segment_id_b.in_(user_segment_ids))
    ).delete(synchronize_session="fetch")

    Segment.query.filter_by(user_id=user.id).delete()
    GpsPoint.query.filter_by(user_id=user.id).delete()

    db.session.commit()
    return jsonify({"deleted": True})


if __name__ == "__main__":
    app.run(debug=True)
