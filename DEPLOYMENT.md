# NThaCityRP Deployment Guide

## Overview
NThaCityRP is a Role-Playing Computer-Aided Dispatch (CAD) system designed for GTA V roleplay servers. This guide covers deployment, configuration, and duplication for new server instances.

## Prerequisites
- Python 3.8+
- PostgreSQL 12+
- Railway account (recommended) or other PostgreSQL host
- Cloudflare Pages account (for frontend)

## Quick Deployment

### 1. Database Setup
```bash
# Create a new PostgreSQL database
# Use Railway, Heroku Postgres, or any PostgreSQL provider
```

### 2. Environment Variables
Create a `.env` file with:
```env
DATABASE_URL=postgresql://username:password@host:port/database
FLASK_SECRET=your-secure-random-secret-here
FLASK_ENV=production
```

### 3. First-Time Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Run database migrations
python -m flask db upgrade

# Create first admin user
curl -X POST http://localhost:5000/api/bootstrap/first-admin \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "securepassword123"}'
```

### 4. Start the Application
```bash
python server.py
```

## Configuration

### Server Configuration
Use the admin panel to configure:
- Server name and branding
- Departments and ranks
- Penal codes and statutes
- Call types and categories

### Multi-Server Support
Each server instance can be configured independently:
- Set unique `SERVER_ID` in environment
- Configure server-specific departments
- Customize penal codes and procedures

## Duplication Process

### For New Server Instances
1. Copy the entire codebase
2. Create new database
3. Update environment variables
4. Run migrations: `flask db upgrade`
5. Bootstrap first admin user
6. Configure server-specific settings
7. Deploy frontend to Cloudflare Pages

### Database Considerations
- All data is PostgreSQL-backed
- No hardcoded assumptions about NThaCityRP
- Configurable departments, ranks, and procedures
- Safe to duplicate without conflicts

## Deployment Options

### Railway (Recommended)
1. Create new Railway project
2. Add PostgreSQL database
3. Deploy from GitHub or CLI
4. Set environment variables in Railway dashboard

### Docker
```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["python", "server.py"]
```

### Manual Server
- Ubuntu 20.04+ recommended
- Install PostgreSQL and Python
- Use systemd for process management
- Configure nginx reverse proxy

## Troubleshooting

### Common Issues
- **Migration errors**: Run `flask db upgrade` after database changes
- **Admin access**: Use `/api/bootstrap/first-admin` if no admins exist
- **Database connection**: Verify `DATABASE_URL` format
- **Static files**: Ensure frontend is deployed to Cloudflare Pages

### Health Checks
- Visit `/api/health` for system status
- Use `/api/diagnostics` (admin only) for detailed info

## Security Notes
- Change default admin password immediately
- Use strong `FLASK_SECRET`
- Enable HTTPS in production
- Regularly update dependencies