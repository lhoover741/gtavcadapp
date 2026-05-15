#!/usr/bin/env python3
"""
Setup script for generating admin password hash.
Run this to generate a hash for the ADMIN_PASSWORD_HASH environment variable.
"""

import os
import sys
from security_service import hash_password

def main():
    if len(sys.argv) != 2:
        print("Usage: python setup_admin_password.py <password>")
        print("This will output the hashed password for use in ADMIN_PASSWORD_HASH env var.")
        sys.exit(1)

    password = sys.argv[1]
    hashed = hash_password(password)
    print(f"Hashed password: {hashed}")
    print("Set this as the ADMIN_PASSWORD_HASH environment variable.")

if __name__ == "__main__":
    main()