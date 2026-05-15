"""Authentication compatibility helpers.

This module exists as a stable import surface for auth functions.
"""

from security_service import hash_password, verify_password

__all__ = ["hash_password", "verify_password"]
