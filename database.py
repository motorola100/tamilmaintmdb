from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Movie(db.Model):
    __tablename__ = "movies"

    id          = db.Column(db.Integer, primary_key=True)
    movie_id    = db.Column(db.String(255), unique=True, index=True, nullable=False)
    title       = db.Column(db.String(500), nullable=False)
    poster      = db.Column(db.String(1000))
    screenshots = db.Column(db.JSON)          # list of screenshot URLs
    magnet      = db.Column(db.Text)
    quality     = db.Column(db.String(100))
    details     = db.Column(db.JSON)          # full details list for modal
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_summary(self):
        return {"title": self.title}

    def to_full(self):
        return {
            "title":   self.title,
            "poster":  self.poster,
            "details": self.details or [],
        }