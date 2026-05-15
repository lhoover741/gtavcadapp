import logging
from functools import wraps
from flask_caching import Cache
from sqlalchemy import func

logger = logging.getLogger(__name__)

cache = Cache(config={'CACHE_TYPE': 'simple'})

def paginate_query(query, page=1, per_page=20):
    """Paginate a SQLAlchemy query."""
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()

    return {
        'items': items,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page,
    }

def cached_query(timeout=300):
    """Decorator to cache query results."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            cache_key = f"query_{f.__name__}_{str(args)}_{str(kwargs)}"
            result = cache.get(cache_key)
            if result is None:
                result = f(*args, **kwargs)
                cache.set(cache_key, result, timeout=timeout)
            return result
        return decorated_function
    return decorator

def optimize_civilian_query(db):
    """Optimize civilian queries with eager loading."""
    from models import Civilian
    return Civilian.query.options(
        db.joinedload(Civilian.vehicles) if hasattr(Civilian, 'vehicles') else None
    )

def get_statistics(db):
    """Get system statistics with optimized queries."""
    from models import Civilian, Vehicle, Arrest, Warrant, Bolo

    stats = {
        'total_civilians': db.session.query(func.count(Civilian.id)).scalar() or 0,
        'total_vehicles': db.session.query(func.count(Vehicle.id)).scalar() or 0,
        'total_arrests': db.session.query(func.count(Arrest.id)).scalar() or 0,
        'active_warrants': db.session.query(func.count(Warrant.id)).filter(
            Warrant.warrant_status == 'Active'
        ).scalar() or 0,
        'active_bolos': db.session.query(func.count(Bolo.id)).filter(
            Bolo.status == 'Active'
        ).scalar() or 0,
    }

    return stats
