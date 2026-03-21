from datetime import datetime, timezone
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False)
    instagram = db.Column(db.String(120))
    display_name = db.Column(db.String(120))
    is_visible = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    gps_points = db.relationship("GpsPoint", backref="user", lazy=True)
    segments = db.relationship("Segment", backref="user", lazy=True)


class GpsPoint(db.Model):
    __tablename__ = "gps_points"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    recorded_at = db.Column(db.DateTime, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Segment(db.Model):
    __tablename__ = "segments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    start_lat = db.Column(db.Float, nullable=False)
    start_lon = db.Column(db.Float, nullable=False)
    end_lat = db.Column(db.Float, nullable=False)
    end_lon = db.Column(db.Float, nullable=False)
    start_label = db.Column(db.String(100))
    end_label = db.Column(db.String(100))
    date = db.Column(db.Date)
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    occurrence_count = db.Column(db.Integer, default=1, nullable=False)


class UserContact(db.Model):
    __tablename__ = "user_contacts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    contact_type = db.Column(db.String(50), nullable=False)   # e.g. "instagram", "phone", "line"
    contact_value = db.Column(db.String(200), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "contact_type", name="uq_user_contact_type"),
    )


class SharedRoute(db.Model):
    __tablename__ = "shared_routes"

    id = db.Column(db.Integer, primary_key=True)
    segment_id_a = db.Column(db.Integer, db.ForeignKey("segments.id"), nullable=False)
    segment_id_b = db.Column(db.Integer, db.ForeignKey("segments.id"), nullable=False)
    similarity_score = db.Column(db.Float, nullable=False)
    canonical_start_lat = db.Column(db.Float)
    canonical_start_lon = db.Column(db.Float)
    canonical_end_lat = db.Column(db.Float)
    canonical_end_lon = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


if __name__ == "__main__":
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///pathpal.db"
    db.init_app(app)
    with app.app_context():
        db.create_all()
    print("Tables created.")
