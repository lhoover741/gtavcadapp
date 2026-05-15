#!/usr/bin/env python
import argparse
from getpass import getpass

from server import app
from database import db
from models import User
from security_service import hash_password
from server import invalidate_user_sessions


def reset_password(email, password=None, show_hash=False):
    with app.app_context():
        user = User.query.filter(User.email.ilike(email)).first()
        if not user:
            raise SystemExit(f"User not found: {email}")

        if not password:
            password = getpass('New password: ')
        if len(password) < 8:
            raise SystemExit('Password must be at least 8 characters long')

        new_hash = hash_password(password)
        user.password_hash = new_hash
        user.role = 'PlatformOwner'
        if hasattr(user, 'platform_role'):
            user.platform_role = 'PlatformOwner'
        user.active = True
        invalidate_user_sessions(user.id)
        db.session.commit()

        print(f"PlatformOwner password reset successfully for {email}")
        if show_hash:
            print(f"hash={new_hash}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Manage PlatformOwner account')
    sub = parser.add_subparsers(dest='command', required=True)

    reset = sub.add_parser('reset-password')
    reset.add_argument('--email', required=True)
    reset.add_argument('--password', default=None)
    reset.add_argument('--show-hash', action='store_true')

    args = parser.parse_args()
    if args.command == 'reset-password':
        reset_password(args.email, args.password, args.show_hash)
