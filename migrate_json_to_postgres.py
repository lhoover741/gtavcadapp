import json
import os
from flask import Flask
from database import configure_database, db
from models import Complaint, Application

app = Flask(__name__)

connected = configure_database(app)

if not connected:
    raise RuntimeError('DATABASE_URL not configured')

COMPLAINTS_FILE = 'complaints_data.json'
APPLICATIONS_FILE = 'applications_data.json'

with app.app_context():
    db.create_all()

    if os.path.exists(COMPLAINTS_FILE):
        with open(COMPLAINTS_FILE, 'r') as f:
            complaints = json.load(f)

        for c in complaints:
            exists = Complaint.query.filter_by(complaint_id=c.get('id')).first()
            if exists:
                continue

            complaint = Complaint(
                complaint_id=c.get('id'),
                complaint_discord=c.get('complaintDiscord'),
                reported_name=c.get('reportedName'),
                complaint_type=c.get('complaintType'),
                incident_date=c.get('incidentDate'),
                incident_location=c.get('incidentLocation'),
                witnesses=c.get('witnesses'),
                evidence_link=c.get('evidenceLink'),
                description=c.get('description'),
                resolution=c.get('resolution'),
                status=c.get('status', 'Open'),
                staff_notes=c.get('staffNotes', '')
            )

            db.session.add(complaint)

    if os.path.exists(APPLICATIONS_FILE):
        with open(APPLICATIONS_FILE, 'r') as f:
            applications = json.load(f)

        for a in applications:
            exists = Application.query.filter_by(application_id=a.get('id')).first()
            if exists:
                continue

            application = Application(
                application_id=a.get('id'),
                app_discord=a.get('appDiscord'),
                app_character=a.get('appCharacter'),
                application_type=a.get('applicationType'),
                age_confirmation=a.get('ageConfirmation'),
                experience=a.get('experience'),
                role_reason=a.get('roleReason'),
                availability=a.get('availability'),
                status=a.get('status', 'Pending'),
                staff_notes=a.get('staffNotes', '')
            )

            db.session.add(application)

    db.session.commit()

    print('Migration completed successfully.')
