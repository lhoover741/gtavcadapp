"""Canonical GTAVCAD platform and default tenant branding constants."""

import os

PLATFORM_NAME = 'GTAVCAD'
PLATFORM_DOMAIN = os.getenv('PLATFORM_DOMAIN', 'gtavcad.app')
PLATFORM_TAGLINE = 'Multi-Community RP/CAD Platform'
PLATFORM_CTA = 'Create or Join a Community'

DEFAULT_COMMUNITY_ID = os.getenv('DEFAULT_COMMUNITY_ID', 'demo-community')
DEFAULT_COMMUNITY_NAME = os.getenv('DEFAULT_COMMUNITY_NAME', 'Demo Community')
DEFAULT_COMMUNITY_SLUG = os.getenv('DEFAULT_COMMUNITY_SLUG', 'demo-community')
DEFAULT_COMMUNITY_CAD_NAME = os.getenv('DEFAULT_COMMUNITY_CAD_NAME', 'Demo Community CAD')

DEFAULT_COMMUNITY_DEPARTMENTS = [
    'LSPD',
    'BCSO',
    'SWAT',
    'Dispatch',
    'Traffic Division',
    'Gang Enforcement',
    'K9 Unit',
]
