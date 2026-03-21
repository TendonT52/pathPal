from datetime import datetime, timedelta


def test_haversine():
    from segmentation import haversine
    d = haversine(35.6812, 139.7671, 35.6896, 139.7006)
    assert 6000 < d < 6700


def test_stop_detection():
    from segmentation import detect_stops

    base = datetime(2026, 3, 18, 8, 0, 0)
    points = [
        {"lat": 35.6762 + i * 0.00001, "lon": 139.6503, "recorded_at": base + timedelta(minutes=i)}
        for i in range(20)
    ]
    stops = detect_stops(points, dwell_minutes=15)
    assert len(stops) == 1


def test_segment_extraction():
    from segmentation import detect_stops, extract_segments

    base = datetime(2026, 3, 18, 8, 0, 0)
    stop_a = [{"lat": 35.676, "lon": 139.650, "recorded_at": base + timedelta(minutes=i)} for i in range(20)]
    travel = [{"lat": 35.676 + i * 0.001, "lon": 139.650 + i * 0.001, "recorded_at": base + timedelta(minutes=20 + i)} for i in range(5)]
    stop_b = [{"lat": 35.681, "lon": 139.655, "recorded_at": base + timedelta(minutes=25 + i)} for i in range(20)]

    all_points = stop_a + travel + stop_b
    stops = detect_stops(all_points, dwell_minutes=15)
    segments = extract_segments(all_points, stops)
    assert len(segments) == 1
    assert segments[0]["start_time"] < segments[0]["end_time"]
