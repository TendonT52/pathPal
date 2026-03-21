from datetime import datetime, timedelta


# Step 4
def test_register_success(client):
    res = client.post("/api/register", json={"username": "alice"})
    assert res.status_code == 200
    data = res.get_json()
    assert "user_token" in data
    assert len(data["user_token"]) > 10


def test_register_missing_username(client):
    res = client.post("/api/register", json={})
    assert res.status_code == 400


# Step 5
def test_upload_gps(client, registered_user):
    token = registered_user["user_token"]
    res = client.post("/api/gps", json={
        "user_token": token,
        "points": [
            {"lat": 35.6762, "lon": 139.6503, "ts": "2026-03-18T08:01:00"},
            {"lat": 35.6770, "lon": 139.6510, "ts": "2026-03-18T08:02:00"},
        ]
    })
    assert res.status_code == 200
    assert res.get_json()["saved"] == 2


def test_upload_gps_invalid_token(client):
    res = client.post("/api/gps", json={
        "user_token": "bad-token",
        "points": [{"lat": 35.0, "lon": 139.0, "ts": "2026-03-18T08:00:00"}]
    })
    assert res.status_code == 401


# Step 7
def test_segment_now(client, registered_user, uploaded_gps_points):
    res = client.post("/api/segment_now", json={"user_token": registered_user["user_token"]})
    assert res.status_code == 200
    assert res.get_json()["segments_created"] >= 1


# Step 8
def test_get_segments(client, registered_user, seeded_segments):
    token = registered_user["user_token"]
    res = client.get(f"/api/segments?token={token}")
    assert res.status_code == 200
    data = res.get_json()
    assert "segments" in data
    assert len(data["segments"]) >= 1
    seg = data["segments"][0]
    assert "start_label" in seg
    assert "end_label" in seg
    assert "occurrence_count" in seg


# Step 10
def test_get_shared(client, two_users_with_matched_routes):
    user_a_token = two_users_with_matched_routes["user_a_token"]
    res = client.get(f"/api/shared?token={user_a_token}")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["shared"]) >= 1
    assert data["shared"][0]["match_count"] >= 1


# Step 11
def test_get_people_respects_visibility(client, two_users_with_matched_routes):
    client.post("/api/profile", json={
        "user_token": two_users_with_matched_routes["user_b_token"],
        "is_visible": False
    })
    segment_id = two_users_with_matched_routes["user_a_segment_id"]
    token = two_users_with_matched_routes["user_a_token"]
    res = client.get(f"/api/people?token={token}&segment_id={segment_id}")
    assert res.status_code == 200
    assert res.get_json()["people"] == []


def test_get_people_visible(client, two_users_with_matched_routes):
    segment_id = two_users_with_matched_routes["user_a_segment_id"]
    token = two_users_with_matched_routes["user_a_token"]
    res = client.get(f"/api/people?token={token}&segment_id={segment_id}")
    assert res.status_code == 200
    assert len(res.get_json()["people"]) >= 1


# Step 12
def test_update_profile(client, registered_user):
    token = registered_user["user_token"]
    res = client.post("/api/profile", json={
        "user_token": token,
        "display_name": "Alice",
        "instagram": "alice_gram",
        "is_visible": False
    })
    assert res.status_code == 200

    from models import User
    user = User.query.filter_by(token=token).first()
    assert user.display_name == "Alice"
    assert user.instagram == "alice_gram"
    assert user.is_visible == False


# Step 13
def test_delete_data(client, registered_user, seeded_segments):
    token = registered_user["user_token"]
    res = client.delete("/api/data", json={"user_token": token})
    assert res.status_code == 200
    assert res.get_json()["deleted"] == True

    from models import User, GpsPoint, Segment
    user = User.query.filter_by(token=token).first()
    assert user is not None
    assert GpsPoint.query.filter_by(user_id=user.id).count() == 0
    assert Segment.query.filter_by(user_id=user.id).count() == 0


# Step 14 — End-to-end
def test_full_flow(client):
    r1 = client.post("/api/register", json={"username": "alice"}).get_json()
    r2 = client.post("/api/register", json={"username": "bob"}).get_json()
    tok_a, tok_b = r1["user_token"], r2["user_token"]

    def make_route(base_hour, lat_offset=0.0):
        base = datetime(2026, 3, 18, base_hour, 0, 0)
        home = [{"lat": 35.676 + lat_offset, "lon": 139.650, "ts": (base + timedelta(minutes=i)).isoformat()} for i in range(20)]
        travel = [{"lat": 35.676 + lat_offset + i * 0.001, "lon": 139.650 + i * 0.001, "ts": (base + timedelta(minutes=20 + i)).isoformat()} for i in range(10)]
        school = [{"lat": 35.686 + lat_offset, "lon": 139.660, "ts": (base + timedelta(minutes=30 + i)).isoformat()} for i in range(20)]
        return home + travel + school

    client.post("/api/gps", json={"user_token": tok_a, "points": make_route(8, lat_offset=0.000)})
    client.post("/api/gps", json={"user_token": tok_b, "points": make_route(8, lat_offset=0.001)})

    client.post("/api/segment_now", json={"user_token": tok_a})
    client.post("/api/segment_now", json={"user_token": tok_b})

    from matching import run_matching
    from models import db
    run_matching(db.session)

    shared = client.get(f"/api/shared?token={tok_a}").get_json()
    assert len(shared["shared"]) >= 1

    seg_id = shared["shared"][0]["segment_id"]
    people = client.get(f"/api/people?token={tok_a}&segment_id={seg_id}").get_json()
    assert len(people["people"]) >= 1
    assert people["people"][0]["display_name"] == "bob"
