# NThaCityRP Duplication Guide

## Overview
This guide explains how to safely duplicate NThaCityRP for new server instances, ensuring no conflicts or data leakage between deployments.

## Why Duplication-Ready?

NThaCityRP Phase 3 implements:
- **No hardcoded assumptions**: All server-specific data is configurable
- **Database isolation**: Each instance uses separate PostgreSQL database
- **User account separation**: Users are scoped to individual servers
- **Configurable branding**: Server name, departments, and procedures are customizable
- **Automated bootstrap**: First-time setup creates necessary admin accounts

## Duplication Process

### Step 1: Codebase Copy
```bash
# Clone or copy the entire repository
git clone https://github.com/your-org/nthacityrp.git new-server-instance
cd new-server-instance
```

### Step 2: Database Setup
```bash
# Create a NEW PostgreSQL database
# IMPORTANT: Never reuse databases between server instances
createdb new_server_db

# Or use Railway/Heroku to create a new database instance
```

### Step 3: Environment Configuration
```bash
# Copy and modify .env file
cp .env.example .env

# Edit .env with new values
DATABASE_URL=postgresql://user:pass@host:port/new_server_db
FLASK_SECRET=your-new-32-char-secret-here
```

### Step 4: Database Initialization
```bash
# Install dependencies
pip install -r requirements.txt

# Run migrations to create schema
python -m flask db upgrade

# Verify database is ready
curl http://localhost:5000/api/health
```

### Step 5: Admin Bootstrap
```bash
# Create first admin user for this server instance
curl -X POST http://localhost:5000/api/bootstrap/first-admin \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "secure-admin-password-123",
    "email": "admin@newserver.com"
  }'
```

### Step 6: Server Configuration
```bash
# Login as admin and configure server-specific settings
# Use /api/admin/config endpoints to customize:
# - Server name and branding
# - Departments and officer ranks
# - Penal codes and procedures
# - Call types and categories
```

### Step 7: Frontend Deployment
```bash
# Deploy frontend to Cloudflare Pages (or your hosting)
# Update any hardcoded API URLs if necessary
# Ensure CORS is properly configured
```

## Configuration Checklist

### Server Identity
- [ ] Set unique server name
- [ ] Configure server ID
- [ ] Update branding/colors if needed

### Departments & Roles
- [ ] Define police departments
- [ ] Set officer rank hierarchy
- [ ] Configure role permissions

### Operational Data
- [ ] Customize penal codes
- [ ] Set call types
- [ ] Define evidence categories
- [ ] Configure vehicle categories

### User Management
- [ ] Create additional admin users
- [ ] Set up role assignments
- [ ] Configure user permissions

## Data Isolation Verification

### Database Separation
- [ ] Confirm separate DATABASE_URL
- [ ] Verify no shared tables
- [ ] Check user account isolation

### Configuration Independence
- [ ] Each server has unique config
- [ ] No shared configuration values
- [ ] Independent customization

### Session Security
- [ ] Separate session secrets
- [ ] No cross-server session sharing
- [ ] Isolated authentication

## Testing Duplication

### Functional Tests
- [ ] Admin login works
- [ ] User creation functions
- [ ] CAD data loading
- [ ] API endpoints respond
- [ ] Configuration changes apply

### Data Integrity
- [ ] No data leakage between instances
- [ ] User accounts are separate
- [ ] Configuration changes don't affect other servers

### Performance
- [ ] Database queries are efficient
- [ ] API response times acceptable
- [ ] Memory usage reasonable

## Common Duplication Issues

### Database Conflicts
**Problem**: Attempting to reuse database
**Solution**: Always create new database instance

### Configuration Bleeding
**Problem**: Shared configuration between servers
**Solution**: Use instance-specific config keys

### Session Confusion
**Problem**: Users logged into wrong server
**Solution**: Ensure separate FLASK_SECRET per instance

### API URL Issues
**Problem**: Frontend pointing to wrong backend
**Solution**: Update frontend API endpoints

## Scaling Considerations

### Multiple Servers
For server networks:
- Use consistent penal codes where possible
- Standardize department names
- Share call type definitions
- Maintain separate user databases

### Load Balancing
- Deploy multiple backend instances
- Use shared Redis for sessions (future)
- Load balance API requests
- Keep databases separate

## Backup & Recovery

### Regular Backups
```bash
# Database backups
pg_dump new_server_db > backup_$(date +%Y%m%d).sql

# Configuration export
curl http://localhost:5000/api/admin/config > config_backup.json
```

### Recovery Process
1. Restore database from backup
2. Recreate environment variables
3. Run migrations if needed
4. Verify configuration
5. Test functionality

## Support & Maintenance

### Updates
- Pull latest code changes
- Run database migrations
- Test configuration compatibility
- Update dependencies

### Monitoring
- Use `/api/health` endpoint
- Monitor database connections
- Check error logs
- Review user activity

## Success Criteria

Duplication is successful when:
- ✅ New server starts without errors
- ✅ Admin accounts are isolated
- ✅ Configuration is independent
- ✅ No data conflicts between instances
- ✅ All functionality works as expected
- ✅ Frontend connects to correct backend
- ✅ Users cannot access other servers' data