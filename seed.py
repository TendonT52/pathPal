#!/usr/bin/env python3
"""
seed.py — Populate PathPal with realistic Bang Na / ICS Bangkok mock data.

Idempotent: skips seeding if any users already exist.
Run: python seed.py
"""

import os
import sys
import random
import secrets
import bcrypt
from datetime import datetime, timedelta, date

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()

from app import app, db
from models import User, GpsPoint, Segment, SharedRoute, UserContact
from segmentation import run_segmentation
from matching import run_matching


# ---------------------------------------------------------------------------
# GPS generation helpers
# ---------------------------------------------------------------------------

def gen_stop(lat, lon, start_time, duration_min, jitter=0.00025):
    pts = []
    for i in range(max(int(duration_min), 1)):
        pts.append({
            "lat": lat + random.uniform(-jitter, jitter),
            "lon": lon + random.uniform(-jitter, jitter),
            "recorded_at": start_time + timedelta(minutes=i),
        })
    return pts


def gen_travel(from_lat, from_lon, to_lat, to_lon, start_time, duration_min):
    n = max(int(duration_min), 2)
    pts = []
    for i in range(n):
        frac = i / (n - 1)
        pts.append({
            "lat": from_lat + frac * (to_lat - from_lat),
            "lon": from_lon + frac * (to_lon - from_lon),
            "recorded_at": start_time + timedelta(minutes=i),
        })
    return pts


def make_day(waypoints, day_date, start_hour, start_minute=0, day_jitter=0.00018):
    """
    waypoints: list of { lat, lon, dwell, travel_after }
    """
    d = day_date
    t = datetime(d.year, d.month, d.day, start_hour, start_minute)
    pts = []

    jitted = [
        (
            wp["lat"] + random.uniform(-day_jitter, day_jitter),
            wp["lon"] + random.uniform(-day_jitter, day_jitter),
        )
        for wp in waypoints
    ]

    for idx, wp in enumerate(waypoints):
        lat, lon = jitted[idx]
        if wp["dwell"] > 0:
            pts += gen_stop(lat, lon, t, wp["dwell"])
            t += timedelta(minutes=wp["dwell"])
        if idx + 1 < len(waypoints) and wp.get("travel_after", 0) > 0:
            next_lat, next_lon = jitted[idx + 1]
            pts += gen_travel(lat, lon, next_lat, next_lon, t, wp["travel_after"])
            t += timedelta(minutes=wp["travel_after"])

    return pts


# ---------------------------------------------------------------------------
# Weekday dates: Mon–Fri, 16–20 March 2026
# ---------------------------------------------------------------------------
WORK_DAYS = [
    date(2026, 3, 16),
    date(2026, 3, 17),
    date(2026, 3, 18),
    date(2026, 3, 19),
    date(2026, 3, 20),
]


# ---------------------------------------------------------------------------
# Five Bang Na personas — two matching groups:
#
#  GROUP A — ICS Bangkok corridor (3 people, all match each other)
#   Nong  home (13.6700, 100.5992)   café (13.6785, 100.5980)   ICS (13.6849, 100.6128)
#   Praew home (13.6703, 100.5995)   café (13.6788, 100.5977)   ICS (13.6852, 100.6126)
#   Wan   home (13.6701, 100.5994)   café (13.6787, 100.5978)   ICS (13.6850, 100.6127)
#
#   All three share Home→Café and Café→ICS segments.
#   Nong and Praew additionally share ICS→Lunch.
#   → /api/people for Nong's "Home→Café" returns BOTH Praew and Wan.
#
#  GROUP B — BITEC / Srinakarin corridor (2 people)
#   Pete  home (13.6810, 100.5975)  café (13.6820, 100.6000)  office (13.6828, 100.6120)
#   Keng  home (13.6813, 100.5978)  café (13.6823, 100.6003)  office (13.6830, 100.6122)
#
# Pair distances ≈ 25–47 m → similarity scores 0.78–0.88
# ---------------------------------------------------------------------------

USERS = [
    # ── 1. Siriporn "Nong" Charoensuk ──────────────────────────────────────
    # Year-10 ICS student. Lives Udomsuk. Café stop before school,
    # then hangs at The Mall Bang Na after classes.
    # 5 legs.
    {
        "username":     "nong_siriporn",
        "password":     "nong1234",
        "display_name": "Siriporn Charoensuk",
        "instagram":    "nong.siriporn",
        "start_hour":   7,
        "route": [
            {"lat": 13.6700, "lon": 100.5992, "dwell": 30, "travel_after": 15},   # Udomsuk condo
            {"lat": 13.6785, "lon": 100.5980, "dwell": 30, "travel_after": 20},   # Café near Bang Na BTS
            {"lat": 13.6849, "lon": 100.6128, "dwell": 250, "travel_after": 8},   # ICS Bangkok
            {"lat": 13.6830, "lon": 100.6110, "dwell": 60, "travel_after": 10},   # Lunch soi near ICS
            {"lat": 13.6801, "lon": 100.6069, "dwell": 90, "travel_after": 20},   # The Mall Bang Na
            {"lat": 13.6700, "lon": 100.5992, "dwell": 20, "travel_after": 0},    # Home
        ],
    },

    # ── 2. Thanapat "Pete" Wongkasem ───────────────────────────────────────
    # IT project manager at BITEC/Srinakarin office park. Lives Bang Na.
    # Morning coffee ritual. Evening: Mega Bang Na.
    # 6 legs.
    {
        "username":     "pete_wongkasem",
        "password":     "pete1234",
        "display_name": "Thanapat Wongkasem",
        "instagram":    "pete.wongkasem",
        "start_hour":   6,
        "start_minute": 30,
        "route": [
            {"lat": 13.6810, "lon": 100.5975, "dwell": 25, "travel_after": 10},   # Bang Na apartment
            {"lat": 13.6820, "lon": 100.6000, "dwell": 30, "travel_after": 20},   # Coffee near Bang Na BTS
            {"lat": 13.6828, "lon": 100.6120, "dwell": 210, "travel_after": 8},   # BITEC / Srinakarin office
            {"lat": 13.6840, "lon": 100.6135, "dwell": 50, "travel_after": 8},    # Office canteen lunch
            {"lat": 13.6828, "lon": 100.6120, "dwell": 240, "travel_after": 25},  # Afternoon at office
            {"lat": 13.6490, "lon": 100.6665, "dwell": 90, "travel_after": 30},   # Mega Bang Na — dinner + shop
            {"lat": 13.6810, "lon": 100.5975, "dwell": 20, "travel_after": 0},    # Home
        ],
    },

    # ── 3. Wanida "Wan" Buranasiri ─────────────────────────────────────────
    # School nurse at ICS Bangkok. Lives Udomsuk (≈25 m from Nong).
    # Uses the SAME morning café as Nong and Praew.
    # After school: runs to pharmacy for medical supplies, back to ICS,
    # then stops at the Bang Na wet market on the way home.
    # 5 legs (6 waypoints, ICS visited twice).
    {
        "username":     "wan_buranasiri",
        "password":     "wan12345",
        "display_name": "Wanida Buranasiri",
        "instagram":    "wan.nurse.bkk",
        "start_hour":   6,
        "start_minute": 45,
        "route": [
            {"lat": 13.6701, "lon": 100.5994, "dwell": 20, "travel_after": 15},   # Udomsuk condo (≈25 m from Nong)
            {"lat": 13.6787, "lon": 100.5978, "dwell": 20, "travel_after": 20},   # Same café corridor (≈31 m from Nong's)
            {"lat": 13.6850, "lon": 100.6127, "dwell": 270, "travel_after": 12},  # ICS Bangkok – school nurse (≈16 m from Nong's)
            {"lat": 13.6810, "lon": 100.6050, "dwell": 40, "travel_after": 12},   # Pharmacy – resupply medical kit
            {"lat": 13.6850, "lon": 100.6127, "dwell": 200, "travel_after": 18},  # ICS – afternoon clinic
            {"lat": 13.6820, "lon": 100.6010, "dwell": 50, "travel_after": 22},   # Bang Na wet market (groceries)
            {"lat": 13.6701, "lon": 100.5994, "dwell": 20, "travel_after": 0},    # Home
        ],
    },

    # ── 4. Kittipong "Keng" Rattanakosin ───────────────────────────────────
    # Freelance UX designer. Lives Bang Na (≈47 m from Pete).
    # Same morning coffee spot as Pete (≈47 m). Client meetings at same
    # BITEC cluster. Different evening: creative pop-up market near On Nut.
    # 6 legs.
    {
        "username":     "keng_design",
        "password":     "keng1234",
        "display_name": "Kittipong Rattanakosin",
        "instagram":    "keng.rattanakosin",
        "start_hour":   8,
        "route": [
            {"lat": 13.6813, "lon": 100.5978, "dwell": 30, "travel_after": 10},   # Bang Na condo (≈47 m from Pete)
            {"lat": 13.6823, "lon": 100.6003, "dwell": 30, "travel_after": 20},   # Same coffee corridor as Pete (≈47 m)
            {"lat": 13.6830, "lon": 100.6122, "dwell": 180, "travel_after": 8},   # Co-working / client, BITEC area (≈47 m from Pete)
            {"lat": 13.6843, "lon": 100.6133, "dwell": 50, "travel_after": 8},    # Lunch near BITEC (≈40 m from Pete's canteen)
            {"lat": 13.6830, "lon": 100.6122, "dwell": 165, "travel_after": 30},  # Back to co-working
            {"lat": 13.6624, "lon": 100.5895, "dwell": 60, "travel_after": 20},   # Creative pop-up / art market (different from Pete)
            {"lat": 13.6813, "lon": 100.5978, "dwell": 20, "travel_after": 0},    # Home
        ],
    },

    # ── 5. Praewpan "Praew" Suksawat ───────────────────────────────────────
    # Year-10 ICS student — Nong's classmate. Lives Udomsuk (≈47 m from Nong).
    # Same café, same school. After school: different lunch soi nearby,
    # then a tutoring café in the Srinakarin area before heading home.
    # 5 legs.
    {
        "username":     "praew_suksawat",
        "password":     "praew123",
        "display_name": "Praewpan Suksawat",
        "instagram":    "praew.p.bkk",
        "start_hour":   7,
        "start_minute": 15,
        "route": [
            {"lat": 13.6703, "lon": 100.5995, "dwell": 20, "travel_after": 15},   # Udomsuk condo (≈47 m from Nong)
            {"lat": 13.6788, "lon": 100.5977, "dwell": 30, "travel_after": 20},   # Same café corridor (≈47 m from Nong's)
            {"lat": 13.6852, "lon": 100.6126, "dwell": 240, "travel_after": 8},   # ICS Bangkok (≈40 m from Nong's gate)
            {"lat": 13.6833, "lon": 100.6108, "dwell": 60, "travel_after": 18},   # Lunch soi (≈40 m from Nong's)
            {"lat": 13.6920, "lon": 100.6095, "dwell": 70, "travel_after": 30},   # Tutoring café, Srinakarin area
            {"lat": 13.6703, "lon": 100.5995, "dwell": 20, "travel_after": 0},    # Home
        ],
    },
]


# ---------------------------------------------------------------------------
# Main seed logic
# ---------------------------------------------------------------------------

def seed():
    with app.app_context():
        if User.query.count() > 0:
            print("Database already has users — skipping seed.")
            return

        print("Seeding database with Bang Na / ICS Bangkok mock data...\n")
        random.seed(42)

        for spec in USERS:
            pw_hash = bcrypt.hashpw(spec["password"].encode(), bcrypt.gensalt()).decode()
            token = secrets.token_urlsafe(32)
            user = User(
                username=spec["username"],
                password_hash=pw_hash,
                token=token,
                display_name=spec["display_name"],
                instagram=spec["instagram"],
                is_visible=True,
            )
            db.session.add(user)
            db.session.flush()

            db.session.add(UserContact(
                user_id=user.id,
                contact_type="instagram",
                contact_value=spec["instagram"],
            ))

            total_pts = 0
            for work_date in WORK_DAYS:
                day_pts = make_day(
                    spec["route"],
                    work_date,
                    start_hour=spec["start_hour"],
                    start_minute=spec.get("start_minute", 0),
                )
                for pt in day_pts:
                    db.session.add(GpsPoint(
                        user_id=user.id,
                        latitude=pt["lat"],
                        longitude=pt["lon"],
                        recorded_at=pt["recorded_at"],
                    ))
                total_pts += len(day_pts)

            db.session.commit()

            n_legs = len(spec["route"]) - 1
            print(f"  {spec['display_name']:<30}  {n_legs} legs  gps_points={total_pts}")

            segs = run_segmentation(user.id, db.session)
            print(f"    → {segs} segments created")

        matches = run_matching(db.session)
        print(f"\nMatching: {matches} shared routes found.")

        # Summary
        print("\n── Users & Segments ───────────────────────────────────────────────")
        for u in User.query.all():
            segs = Segment.query.filter_by(user_id=u.id).all()
            print(f"  [{u.id}] {u.display_name:<30}  segs={len(segs)}")
            for s in segs:
                print(f"        {s.start_label:15} → {s.end_label:15}  (×{s.occurrence_count})")

        print("\n── Who shares with whom ───────────────────────────────────────────")
        for u in User.query.all():
            seg_ids = {s.id for s in Segment.query.filter_by(user_id=u.id).all()}
            shared_routes = SharedRoute.query.filter(
                (SharedRoute.segment_id_a.in_(seg_ids)) |
                (SharedRoute.segment_id_b.in_(seg_ids))
            ).all()
            others = {}
            for sr in shared_routes:
                sa = db.session.get(Segment, sr.segment_id_a)
                sb = db.session.get(Segment, sr.segment_id_b)
                other_uid = sb.user_id if sa.user_id == u.id else sa.user_id
                others[other_uid] = others.get(other_uid, 0) + 1
            if others:
                names = ", ".join(
                    f"{db.session.get(User, uid).display_name} ({n} legs)"
                    for uid, n in others.items()
                )
                print(f"  {u.display_name:<30} shares with: {names}")
            else:
                print(f"  {u.display_name:<30} no shared routes")

        print("\n── Top 3 Shared Routes (by score) ─────────────────────────────────")
        top3 = (
            SharedRoute.query
            .order_by(SharedRoute.similarity_score.desc())
            .limit(3)
            .all()
        )
        for sr in top3:
            sa = db.session.get(Segment, sr.segment_id_a)
            sb = db.session.get(Segment, sr.segment_id_b)
            ua = db.session.get(User, sa.user_id)
            ub = db.session.get(User, sb.user_id)
            print(
                f"  {ua.display_name:<24} {sa.start_label}→{sa.end_label}"
                f"  ↔  {ub.display_name:<24} {sb.start_label}→{sb.end_label}"
                f"  score={sr.similarity_score:.3f}"
            )

        print("\nSeed complete.")


if __name__ == "__main__":
    seed()
