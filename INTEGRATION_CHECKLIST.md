# GTAVCAD Phase 4: Integration Checklist

**This document provides the exact code changes needed to integrate multi-tenant functionality into existing code.**

---

## STEP 1: Integrate Community Middleware into server.py

### Location: server.py (after app initialization)

**Current:**
```python
app = Flask(__name__, static_folder='.', static_url_path='')
# ... config ...

# Configure secure session cookies
app.config['SESSION_COOKIE_HTTPONLY'] = True
# ...
```

**Add After:**
```python
# ----------------------------------------
# MULTI-TENANT CONTEXT
# ----------------------------------------
from community_service import community_context_middleware
from community_routes import register_community_routes

@app.before_request
def inject_community_context():
    """Inject current community context into request."""
    community_context_middleware()

# Register community management API
register_community_routes(app)

logger.info('✓ Community context middleware registered')
logger.info('✓ Community management API registered')
```

### Verify
- [ ] `community_context_middleware` imported
- [ ] `register_community_routes` imported
- [ ] `@app.before_request` registered
- [ ] Routes registered with `register_community_routes(app)`

---

## STEP 2: Add Missing Imports to server.py

### Location: server.py (update import section)

**Current:**
```python
from models import (
    User, Config, Complaint, Application, Civilian, Vehicle, License,
    Warrant, Arrest, Incident, Evidence, TrafficStop, Call911,
    ActivityLog, Bolo, OfficerSession, Alert, RadioLog,
    ServerStatus, Inmate, Hearing, DispatchCall,
    KnownAssociate, Business, Citation, JailBooking,
    UseOfForceReport, OfficerNote, CaseFile,
    AIGenerationLog, AuditLog,
    Community, CommunityMember, CommunityInvite,
)
```

**Already done! ✓**

---

## STEP 3: Add Community Filter to All GET Endpoints

### Pattern:
```python
# BEFORE:
civilians = Civilian.query.all()

# AFTER:
from flask import g
civilians = Civilian.query.filter_by(community_id=g.community_id).all()
```

### Priority Endpoints:
- [ ] `GET /api/civilians` - All civilian records
- [ ] `GET /api/arrests` - All arrest records
- [ ] `GET /api/warrants` - All warrants
- [ ] `GET /api/bolos` - All BOLOs
- [ ] `GET /api/dispatch-calls` - All dispatch calls
- [ ] `GET /api/inmates` - All inmates
- [ ] `GET /api/vehicles` - All vehicles
- [ ] `GET /api/businesses` - All businesses
- [ ] `GET /api/citations` - All citations
- [ ] `GET /api/dmv` - DMV records
- [ ] `GET /api/court/hearings` - Court hearings
- [ ] ... (all other GET endpoints)

### Example Implementation:

```python
@app.route('/api/civilians', methods=['GET'])
@require_auth
def get_civilians():
    """Get all civilians for current community."""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 50, type=int)
    
    from flask import g
    from sqlalchemy import desc
    
    # SCOPED QUERY: Only civilians in current community
    query = Civilian.query.filter_by(community_id=g.community_id)
    
    # Apply filters
    if request.args.get('name'):
        name = f"%{request.args.get('name')}%"
        query = query.filter(
            (Civilian.first_name.ilike(name)) | (Civilian.last_name.ilike(name))
        )
    
    # Paginate
    paginated = query.order_by(desc(Civilian.created_at)).paginate(
        page=page, per_page=limit, error_out=False
    )
    
    return jsonify({
        'success': True,
        'civilians': [c.to_dict() for c in paginated.items],
        'page': page,
        'total': paginated.total,
    })
```

---

## STEP 4: Add Community ID to All POST/PUT Endpoints

### Pattern:
```python
# BEFORE:
civilian = Civilian(id=generate_id('CIV'), first_name=data['first_name'], ...)
db.session.add(civilian)

# AFTER:
from flask import g
civilian = Civilian(
    civilian_id=generate_id('CIV'),
    community_id=g.community_id,  # ← Add this line!
    first_name=data['first_name'],
    ...
)
db.session.add(civilian)
```

### Critical: NEVER trust frontend community_id

```python
# ❌ WRONG - Security vulnerability!
civilian = Civilian(
    community_id=request.json.get('community_id'),  # User could set any community!
)

# ✅ CORRECT - Backend-derived
civilian = Civilian(
    community_id=g.community_id,  # From authenticated middleware
)
```

### Priority Endpoints:
Apply to all POST/PUT endpoints that create/modify records:
- [ ] `POST /api/civilians` - Create civilian
- [ ] `PUT /api/civilians/<id>` - Update civilian
- [ ] `POST /api/arrests` - Create arrest
- [ ] `POST /api/warrants` - Create warrant
- [ ] `POST /api/dispatch-calls` - Create call
- [ ] ... (all write endpoints)

### Example:

```python
@app.route('/api/civilians', methods=['POST'])
@require_auth
@community_member_required_scoped
def create_civilian():
    """Create new civilian record."""
    data = request.get_json() or {}
    from flask import g
    
    # Validate input
    if not data.get('first_name') or not data.get('last_name'):
        return jsonify({'error': 'First and last name required'}), 400
    
    try:
        # Create record with backend-derived community_id
        civilian = Civilian(
            civilian_id=generate_id('CIV'),
            community_id=g.community_id,  # ← From middleware, not frontend!
            first_name=data['first_name'].strip(),
            last_name=data['last_name'].strip(),
            date_of_birth=data.get('date_of_birth'),
            gender=data.get('gender'),
            phone_number=data.get('phone_number'),
            address=data.get('address'),
            # ... other fields
        )
        db.session.add(civilian)
        db.session.commit()
        
        # Log action
        log_audit_action(
            actor_user_id=session.get('user_id'),
            action='create_civilian',
            record_type='Civilian',
            record_id=civilian.civilian_id,
            after_state=civilian.to_dict(),
            community_id=g.community_id,  # ← Include community_id in logs!
        )
        
        return jsonify({
            'success': True,
            'message': 'Civilian created',
            'civilian': civilian.to_dict(),
        }), 201
        
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error creating civilian: {e}')
        return jsonify({'error': str(e)}), 500
```

---

## STEP 5: Update RBAC Decorators

### Pattern:

```python
# OLD (Global role-based):
@admin_required
@police_required
@dispatch_required

# NEW (Community-scoped):
@community_admin_required_scoped
@community_police_required_scoped
@community_dispatch_required_scoped
```

### Priority Routes:
- [ ] Admin endpoints → `@community_admin_required_scoped`
- [ ] Police operations → `@community_police_required_scoped`
- [ ] Dispatch operations → `@community_dispatch_required_scoped`
- [ ] Judge operations → `@community_judge_required_scoped`
- [ ] DMV operations → `@community_dmv_required_scoped`
- [ ] Any authenticated → `@community_member_required_scoped`

### Example:

```python
# BEFORE:
@app.route('/api/admin/config', methods=['PUT'])
@admin_required
def update_config():
    # Could be any admin, any data
    ...

# AFTER:
from community_service import community_admin_required_scoped

@app.route('/api/admin/config', methods=['PUT'])
@require_auth
@community_admin_required_scoped
def update_config():
    # Must be admin IN current community
    from flask import g
    # Update only this community's config
    ...
```

---

## STEP 6: Update Audit Logging

### Pattern:

```python
def log_audit_action(actor_user_id, action, record_type, record_id, 
                     before_state=None, after_state=None, community_id=None):
    """Log action for audit trail."""
    from flask import request, g
    import json
    
    # IMPORTANT: Always include community_id
    community_id = community_id or g.community_id
    
    audit = AuditLog(
        log_id=generate_id('AUDIT'),
        community_id=community_id,  # ← Add this!
        actor=f'user_{actor_user_id}',
        actor_role=g.current_role if hasattr(g, 'current_role') else 'Unknown',
        action=action,
        record_type=record_type,
        record_id=record_id,
        before_state=json.dumps(before_state) if before_state else None,
        after_state=json.dumps(after_state) if after_state else None,
        ip_address=request.remote_addr,
    )
    db.session.add(audit)
    db.session.commit()
```

### Apply To:
- [ ] All create operations
- [ ] All update operations
- [ ] All delete operations
- [ ] Permission denials
- [ ] Sensitive reads

---

## STEP 7: Handle Searches Across Communities

### Pattern: Search is Community-Specific

```python
@app.route('/api/search', methods=['GET'])
@require_auth
def search():
    """Search civilians, arrests, etc."""
    query_term = request.args.get('q', '').strip()
    from flask import g
    
    if not query_term or len(query_term) < 2:
        return jsonify({'error': 'Query too short'}), 400
    
    search_term = f"%{query_term}%"
    
    # Search ONLY in current community
    civilians = Civilian.query.filter(
        Civilian.community_id == g.community_id,  # ← Community filter!
        (Civilian.first_name.ilike(search_term)) | 
        (Civilian.last_name.ilike(search_term))
    ).limit(10).all()
    
    arrests = Arrest.query.filter(
        Arrest.community_id == g.community_id,  # ← Community filter!
        Arrest.suspect_name.ilike(search_term)
    ).limit(10).all()
    
    return jsonify({
        'civilians': [c.to_dict() for c in civilians],
        'arrests': [a.to_dict() for a in arrests],
    })
```

---

## STEP 8: Configuration Lookup

### Pattern: Community-Scoped Config

```python
def get_config(key, community_id=None):
    """Get config value, checking community-scoped then global."""
    from flask import g
    
    community_id = community_id or g.community_id
    
    # 1. Try community-scoped
    config = Config.query.filter_by(
        key=key,
        community_id=community_id
    ).first()
    
    if config:
        return config.value
    
    # 2. Fall back to global
    config = Config.query.filter_by(
        key=key,
        community_id=None  # Global config
    ).first()
    
    if config:
        return config.value
    
    # 3. Return default
    defaults = {
        'server_name': 'GTAVCAD Community',
        'departments': ['Police', 'Fire', 'Medical'],
        # ...
    }
    return defaults.get(key)

# Usage:
server_name = get_config('server_name')  # Gets community-scoped or global
```

---

## STEP 9: Handle 404s for Cross-Community Requests

### Pattern:

```python
@app.route('/api/civilians/<civilian_id>', methods=['GET'])
@require_auth
def get_civilian(civilian_id):
    """Get civilian by ID."""
    from flask import g
    
    # CRITICAL: Filter by BOTH ID and community!
    civilian = Civilian.query.filter_by(
        civilian_id=civilian_id,
        community_id=g.community_id  # ← Prevents cross-community access!
    ).first()
    
    if not civilian:
        # Return 404 whether record doesn't exist OR user not in community
        # (Don't leak info about other communities)
        return jsonify({'error': 'Civilian not found'}), 404
    
    return jsonify({'civilian': civilian.to_dict()})
```

### Why?
- If user tries to access civilian from other community, should get 404
- Don't return "unauthorized" (would leak existence of other communities)
- Just return 404 (not found in this community's scope)

---

## STEP 10: Update Error Responses

### Pattern:

```python
def ensure_community_context():
    """Validate community context for every request."""
    from flask import g
    
    if not hasattr(g, 'community_id') or not g.community_id:
        return jsonify({
            'error': 'Community context required',
            'message': 'No community selected or invalid community',
        }), 400
    
    return None

# Use in endpoints:
@app.route('/api/admin/settings', methods=['GET'])
@require_auth
def admin_settings():
    error = ensure_community_context()
    if error:
        return error
    
    # Proceed with endpoint logic
    ...
```

---

## STEP 11: Database Query Patterns

### Safe Patterns

```python
# ✅ Single record with community scope
record = Model.query.filter_by(
    id='record_xyz',
    community_id=g.community_id
).first()

# ✅ Multiple records scoped
records = Model.query.filter_by(
    community_id=g.community_id,
    status='Active'
).all()

# ✅ Count in community
count = Model.query.filter_by(
    community_id=g.community_id
).count()

# ✅ Aggregation per community
from sqlalchemy import func
stats = db.session.query(
    Model.status,
    func.count(Model.id)
).filter_by(community_id=g.community_id).group_by(Model.status).all()
```

### UNSAFE Patterns ❌

```python
# ❌ NEVER: Unscoped query
Model.query.all()

# ❌ NEVER: Filter without community
Model.query.filter_by(status='Active').all()

# ❌ NEVER: Trust user input for community_id
Model.query.filter_by(community_id=request.json.get('community_id')).all()

# ❌ NEVER: Use global config when community config exists
Config.query.filter_by(key='departments').first()
# INSTEAD:
Config.query.filter_by(key='departments', community_id=g.community_id).first()
```

---

## STEP 12: Testing Checklist

After each change:

- [ ] Query returns data ONLY for current community
- [ ] Cross-community access returns 404 (not 403)
- [ ] Write operations set correct community_id
- [ ] RBAC decorators enforce correct role + community
- [ ] Audit logs include community_id
- [ ] Error messages don't leak other communities
- [ ] Pagination works within community scope
- [ ] Searches return only community's data
- [ ] Decorators reject unauthorized users properly

---

## STEP 13: Approval Checklist

**Before merging changes:**

- [ ] All GET queries scoped by community_id
- [ ] All POST/PUT set community_id from g.community_id (not request)
- [ ] All DELETE operations scoped
- [ ] RBAC decorators are community-scoped version
- [ ] Audit logs include community_id
- [ ] Cross-community access returns 404
- [ ] No hardcoded community IDs except default 'nthacityrp'
- [ ] Tests pass with multiple communities
- [ ] Isolation validator shows all green
- [ ] Manual multi-community test passes

---

## STEP 14: Deployment

### Pre-Deployment Testing

```bash
# Run isolation tests
python tenant_isolation_validator.py

# Check for query patterns
grep -r "\.query\.all()" --include="*.py" | grep -v "test_" | grep -v "#"
grep -r "\.filter_by(" --include="*.py" | grep -v "community_id" | wc -l

# (Should return 0 unscoped queries)
```

### Gradual Rollout

**Phase 1:** Deploy with `MULTI_TENANT_ENABLED=false`
- All queries default to 'nthacityrp'
- Existing functionality works
- New community API available but optional

**Phase 2:** Verify all endpoints scoped
- Gradually enable community scoping
- Test each change in staging
- Monitor for errors

**Phase 3:** Set `MULTI_TENANT_ENABLED=true`
- Enforce strict community scoping
- Reject any unscoped queries
- Production-ready multi-tenant system

---

## QUICK REFERENCE: Code Snippets

### Add to Top of Routes File
```python
from flask import g, session, request, jsonify
from community_service import get_current_community_id, community_admin_required_scoped
from security_service import community_member_required_scoped, community_admin_required_scoped
```

### Scope GET Query
```python
query = Model.query.filter_by(community_id=g.community_id)
```

### Set Community in Write
```python
record = Model(
    community_id=g.community_id,
    # ... other fields
)
```

### Protect Endpoint
```python
@app.route('/api/path', methods=['GET'])
@require_auth
@community_member_required_scoped
def my_route():
    # User is authenticated member of community
```

### Log Action
```python
log_audit_action(
    actor_user_id=session.get('user_id'),
    action='create_civilian',
    community_id=g.community_id,
    # ...
)
```

---

## COMPLETION STATUS

When you've completed all steps:

- [ ] Server integration done
- [ ] Community middleware active
- [ ] Community routes registered
- [ ] All GET queries scoped
- [ ] All write operations correct
- [ ] RBAC decorators updated
- [ ] Audit logging includes community
- [ ] Tests all passing
- [ ] Isolation validator green
- [ ] Manual testing complete
- [ ] Ready for production

**Next:** Monitor in production for any isolation violations.
