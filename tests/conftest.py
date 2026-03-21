import pytest
from datetime import datetime, timedelta
from app import app as flask_app
from models import db as _db, User, Segment, SharedRoute
from segmentation import run_segmentation


@pytest.fixture
def app():
    flask_app.config["TESTING"] = True
    flask_app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    with flask_app.app_context():
        _db.create_all()
        yield flask_app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def registered_user(client):
    res = client.post("/api/register", json={"username": "alice"})
    return res.get_json()


def _make_route_points(user_id, base_hour=8, lat_offset=0.0):
    base = datetime(2026, 3, 18, base_hour, 0, 0)
    home = [
        {"lat": 35.676 + lat_offset, "lon": 139.650,
         "ts": (base + timedelta(minutes=i)).isoformat()}
        for i in range(20)
    ]
    travel = [
        {"lat": 35.676 + lat_offset + i * 0.001, "lon": 139.650 + i * 0.001,
         "ts": (base + timedelta(minutes=20 + i)).isoformat()}
        for i in range(10)
    ]
    school = [
        {"lat": 35.686 + lat_offset, "lon": 139.660,
         "ts": (base + timedelta(minutes=30 + i)).isoformat()}
        for i in range(20)
    ]
    return home + travel + school


@pytest.fixture
def uploaded_gps_points(client, registered_user):
    token = registered_user["user_token"]
    points = _make_route_points(None)
    client.post("/api/gps", json={"user_token": token, "points": points})
    return points


@pytest.fixture
def seeded_segments(app, registered_user, uploaded_gps_points):
    with app.app_context():
        from models import User
        user = User.query.filter_by(token=registered_user["user_token"]).first()
        run_segmentation(user.id, _db.session)
    return True


@pytest.fixture
def two_users_with_matched_routes(app, client):
    r1 = client.post("/api/register", json={"username": "alice"}).get_json()
    r2 = client.post("/api/register", json={"username": "bob"}).get_json()
    tok_a, tok_b = r1["user_token"], r2["user_token"]

    client.post("/api/gps", json={"user_token": tok_a, "points": _make_route_points(None, lat_offset=0.000)})
    client.post("/api/gps", json={"user_token": tok_b, "points": _make_route_points(None, lat_offset=0.001)})

    client.post("/api/segment_now", json={"user_token": tok_a})
    client.post("/api/segment_now", json={"user_token": tok_b})

    from matching import run_matching
    with app.app_context():
        run_matching(_db.session)

    with app.app_context():
        user_a = User.query.filter_by(token=tok_a).first()
        seg_a = Segment.query.filter_by(user_id=user_a.id).first()

    return {
        "user_a_token": tok_a,
        "user_b_token": tok_b,
        "user_a_segment_id": seg_a.id,
    }


@pytest.fixture
def two_users_with_similar_segments(app):
    with app.app_context():
        import secrets
        from datetime import time, date
        from models import User, Segment

        u1 = User(username="alice", token=secrets.token_urlsafe(32))
        u2 = User(username="bob", token=secrets.token_urlsafe(32))
        _db.session.add_all([u1, u2])
        _db.session.flush()

        s1 = Segment(
            user_id=u1.id,
            start_lat=35.676, start_lon=139.650,
            end_lat=35.681, end_lon=139.655,
            start_label="Home", end_label="School",
            date=date(2026, 3, 18),
            start_time=time(8, 0), end_time=time(8, 30),
            occurrence_count=1,
        )
        s2 = Segment(
            user_id=u2.id,
            start_lat=35.6762, start_lon=139.6501,
            end_lat=35.6811, end_lon=139.6551,
            start_label="Home", end_label="School",
            date=date(2026, 3, 18),
            start_time=time(8, 5), end_time=time(8, 35),
            occurrence_count=1,
        )
        _db.session.add_all([s1, s2])
        _db.session.commit()
        return {"user_a_id": u1.id, "user_b_id": u2.id}
