# NThaCityRP Security Guide

## Overview
This document outlines security measures, best practices, and considerations for deploying NThaCityRP safely.

## Authentication & Authorization

### User Accounts
- Centralized user management with hashed passwords
- Role-based access control (RBAC)
- Session-based authentication
- No localStorage dependency for sensitive data

### Admin Access
- First admin created via bootstrap endpoint
- Hashed password storage using PBKDF2
- Admin sessions expire appropriately
- Audit logging for admin actions

### API Security
- All mutating routes require authentication
- Role decorators enforce permissions
- Standardized error responses (no stack traces)
- Rate limiting on sensitive endpoints

## Data Protection

### Database Security
- PostgreSQL with proper connection handling
- No plaintext password storage
- Parameterized queries prevent SQL injection
- Database migrations track schema changes

### Session Security
- Secure session cookies (HttpOnly, Secure in production)
- Session fixation protection
- Automatic session cleanup

## Production Hardening

### Environment Variables
Required:
- `DATABASE_URL`: PostgreSQL connection string
- `FLASK_SECRET`: 32+ character random secret

Optional:
- `ADMIN_PASSWORD_HASH`: Pre-hashed admin password
- `FLASK_ENV=production`: Enables production mode

### Logging
- Production mode reduces verbosity
- No sensitive data in logs
- Error handlers prevent stack trace exposure
- Audit logs track important actions

### Network Security
- CORS configuration (if needed)
- HTTPS enforcement recommended
- Rate limiting prevents abuse
- Input validation on all endpoints

## Deployment Security

### Railway/Cloud Deployment
- Environment variables stored securely
- Database access restricted
- Automatic SSL/TLS
- Isolated execution environment

### Access Control
- Admin panel requires authentication
- API endpoints have appropriate guards
- Public routes are read-only where possible
- Bootstrap endpoints have safeguards

## Security Checklist

### Pre-Deployment
- [ ] Strong `FLASK_SECRET` set (32+ chars)
- [ ] `DATABASE_URL` configured securely
- [ ] No hardcoded credentials in code
- [ ] Dependencies updated and audited
- [ ] Admin password changed from default

### Production Setup
- [ ] HTTPS enabled
- [ ] Session cookies secure
- [ ] Logging configured appropriately
- [ ] Health endpoints monitored
- [ ] Regular backups configured

### Ongoing Maintenance
- [ ] Monitor for security updates
- [ ] Review access logs regularly
- [ ] Rotate secrets periodically
- [ ] Audit user access and roles

## Incident Response

### Compromise Indicators
- Unexpected database connections
- Unusual admin activity
- Failed authentication spikes
- Unauthorized configuration changes

### Response Steps
1. Immediately disable compromised accounts
2. Change all secrets and passwords
3. Review recent changes and logs
4. Restore from clean backup if needed
5. Update dependencies and patches

## Compliance Notes
- Designed for roleplay server use
- Not intended for real law enforcement
- User data handling follows privacy best practices
- Audit trails maintained for accountability