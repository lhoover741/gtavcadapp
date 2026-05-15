from flask import Flask
from database import configure_database, db
import models

app = Flask(__name__)

connected = configure_database(app)

if not connected:
    raise RuntimeError('DATABASE_URL not configured')

with app.app_context():
    db.create_all()
    print('Database tables created successfully.')
