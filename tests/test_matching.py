from datetime import time


def test_score_identical_segments():
    from matching import score_pair

    seg_a = {"start_lat": 35.676, "start_lon": 139.650, "end_lat": 35.681, "end_lon": 139.655, "start_time": time(8, 0)}
    seg_b = {"start_lat": 35.676, "start_lon": 139.650, "end_lat": 35.681, "end_lon": 139.655, "start_time": time(8, 5)}
    score = score_pair(seg_a, seg_b, radius_m=300)
    assert score >= 0.95


def test_score_distant_segments():
    from matching import score_pair

    seg_a = {"start_lat": 35.676, "start_lon": 139.650, "end_lat": 35.681, "end_lon": 139.655, "start_time": time(8, 0)}
    seg_b = {"start_lat": 35.900, "start_lon": 139.900, "end_lat": 35.950, "end_lon": 139.950, "start_time": time(8, 0)}
    score = score_pair(seg_b, seg_a, radius_m=300)
    assert score < 0.1


def test_run_matching_creates_shared_route(app, two_users_with_similar_segments):
    from matching import run_matching
    from models import db, SharedRoute

    with app.app_context():
        count = run_matching(db.session)
        assert count >= 1
        routes = SharedRoute.query.all()
        assert len(routes) >= 1
        assert routes[0].similarity_score >= 0.75
