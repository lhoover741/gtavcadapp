# NThaCityRP Configuration Guide

## Overview
NThaCityRP uses a flexible configuration system that allows each server instance to be customized for different roleplay communities.

## Configuration System

### Database-Backed Config
Configuration is stored in the `config` table with the following structure:
- `key`: Configuration key name
- `value`: JSON-encoded configuration value
- `description`: Human-readable description

### Configuration Keys

#### Server Identity
- `server_name`: Display name for the server (default: "NThaCityRP")
- `server_id`: Unique identifier for multi-server setups

#### Departments & Ranks
- `departments`: Array of available departments
  ```json
  ["LSPD", "BCSO", "SWAT", "Dispatch", "Traffic Division"]
  ```
- `officer_ranks`: Array of officer rank hierarchy
  ```json
  ["Officer", "Sergeant", "Lieutenant", "Captain", "Chief"]
  ```

#### Penal Codes
- `penal_codes`: Object mapping codes to descriptions
  ```json
  {
    "1.01": "Reckless Driving",
    "1.02": "Speeding",
    "2.01": "Assault"
  }
  ```

#### Call Types
- `call_types`: Array of available call categories
  ```json
  ["Emergency", "Non-Emergency", "Traffic", "Medical"]
  ```

#### Agency Names
- `agency_names`: Full names for department abbreviations
  ```json
  {
    "LSPD": "Los Santos Police Department",
    "BCSO": "Blaine County Sheriff's Office"
  }
  ```

#### Default Officers
- `default_officers`: Array of default CAD officer units
  ```json
  [
    {
      "id": "1L-01",
      "name": "Chief Unit",
      "status": "Available",
      "department": "LSPD"
    }
  ]
  ```

## Configuration Management

### Admin Interface
Administrators can modify configuration through:
- `/api/admin/config`: List all configuration
- `/api/admin/config/<key>`: Update specific configuration

### Public Access
Some configuration is publicly accessible:
- `/api/config/<key>`: Read public configuration values

### Bootstrap Defaults
Default configuration is automatically created during system bootstrap. Modify as needed for your server.

## Customization Examples

### Law Enforcement Focus
```json
{
  "departments": ["LSPD", "BCSO", "Highway Patrol", "SWAT"],
  "call_types": ["Emergency", "Traffic", "Criminal", "Medical"],
  "penal_codes": {
    "10-01": "Suspicious Person",
    "10-02": "Fight in Progress",
    "10-03": "Shots Fired"
  }
}
```

### Fire/EMS Focus
```json
{
  "departments": ["Fire Department", "EMS", "Police"],
  "call_types": ["Fire", "Medical", "Rescue", "Hazard"],
  "penal_codes": {
    "FIRE-01": "Structure Fire",
    "MED-01": "Medical Emergency",
    "HAZ-01": "Hazardous Materials"
  }
}
```

## Multi-Server Configuration

### Server Isolation
Each server instance should have:
- Unique `server_id`
- Separate database
- Independent configuration
- Isolated user accounts

### Shared Configuration
For server networks, consider:
- Standardized penal codes
- Consistent department names
- Shared call type definitions

## Environment Variables

### Required
- `DATABASE_URL`: PostgreSQL connection string
- `FLASK_SECRET`: Session secret key

### Optional
- `FLASK_ENV`: Set to "production" for production mode
- `ADMIN_PASSWORD_HASH`: Pre-hashed admin password

## Migration & Updates

### Configuration Changes
When updating configuration:
1. Use admin API endpoints
2. Test changes in development first
3. Document customizations
4. Plan rollback procedures

### Schema Updates
Database schema changes use Flask-Migrate:
```bash
# Create migration
flask db migrate -m "Description"

# Apply migration
flask db upgrade

# Rollback if needed
flask db downgrade
```

## Troubleshooting

### Configuration Issues
- Check `/api/health` for system status
- Use `/api/diagnostics` for detailed information
- Verify JSON syntax in configuration values
- Ensure proper permissions for config changes

### Common Problems
- **Invalid JSON**: Configuration values must be valid JSON
- **Permission denied**: Only admins can modify configuration
- **Cache issues**: Restart application after config changes