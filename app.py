"""
PlantWise Agricultural Expert System - Backend
Integrates with existing crop_advisory database
Tables: kenya_counties, crop_suitability, crops
"""

import logging
from flask import Flask, render_template, request, flash, redirect, url_for, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import psycopg2
import psycopg2.extras
from psycopg2 import OperationalError as Error
import os
import uuid
import time
import threading
from datetime import timedelta
from dotenv import load_dotenv
from typing import List, Dict, Optional
import json
import requests
import base64
from io import BytesIO
from functools import wraps
from PIL import Image, ImageOps

# ---------------------------------------------------------------------------
# Structured logging — replaces all print() calls for server-side visibility
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('plantwise')

def get_image_type(data: bytes) -> str:
    """Simple replacement for imghdr to detect jpeg/png/webp"""
    if data.startswith(b'\xff\xd8'): return 'jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'png'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP': return 'webp'
    if data.startswith(b'GIF87a') or data.startswith(b'GIF89a'): return 'gif'
    return 'jpeg'  # safe fallback

# Get the directory of this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load environment variables
load_dotenv(os.path.join(BASE_DIR, '.env'))

# OpenRouter AI Vision API Configuration (Free)
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
OPENROUTER_API_URL = 'https://openrouter.ai/api/v1/chat/completions'
OPENROUTER_MODELS = [
    'meta-llama/llama-3.2-11b-vision-instruct:free', # High availability free vision
    'qwen/qwen2.5-vl-72b-instruct:free',             # Best vision quality, free
    'qwen/qwen2.5-vl-32b-instruct:free',             # Slightly smaller, still excellent
    'google/gemini-2.0-flash-exp:free',              # Multimodal speed
    'moonshotai/kimi-vl-a3b-thinking:free',          # Vision + reasoning
    'mistralai/mistral-small-3.1-24b-instruct:free', # Proven vision support
    'openrouter/auto',                               # Smart fallback router
]

app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, 'templates'),
            static_folder=os.path.join(BASE_DIR, 'static'),
            static_url_path='/static')

# ---------------------------------------------------------------------------
# C2 — Secret key: fail loudly if not configured in environment
# ---------------------------------------------------------------------------
_secret_key = os.getenv('SECRET_KEY')
if not _secret_key or _secret_key == 'your-secret-key-change-this-in-production':
    raise RuntimeError(
        "SECRET_KEY is not set or is still the default value. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\" "
        "and add it to your .env file."
    )
app.secret_key = _secret_key

# ---------------------------------------------------------------------------
# M3 — Secure session cookie configuration
# ---------------------------------------------------------------------------
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=False,   # Set True when running behind HTTPS in production
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
    # M4 — Limit uploaded file size to 10 MB
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
    # Required by Flask-WTF CSRF
    WTF_CSRF_TIME_LIMIT=3600,
    TEMPLATES_AUTO_RELOAD=True,
    SEND_FILE_MAX_AGE_DEFAULT=0,
)

# ---------------------------------------------------------------------------
# C4 — CSRF protection for all state-changing forms
# ---------------------------------------------------------------------------
csrf = CSRFProtect(app)

# ---------------------------------------------------------------------------
# C5 — Restrict CORS to API routes only (not wildcard)
# ---------------------------------------------------------------------------
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ---------------------------------------------------------------------------
# M1 — Rate limiting: global 200/min, login 10/min
# ---------------------------------------------------------------------------
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per minute"],
    storage_uri="memory://",
)

# ---------------------------------------------------------------------------
# C3 — Database configuration: no hardcoded fallback for password
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv('DATABASE_URL')  # Railway sets this automatically
DB_CONFIG = {
    'host':     os.getenv('DB_HOST', 'localhost'),
    'user':     os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', ''),  # C3: no hardcoded fallback password
    'dbname':   os.getenv('DB_NAME', 'crop_advisory'),  # NOTE: 'dbname' not 'database'
    'port':     int(os.getenv('DB_PORT', 5432))
}

# Rainfall and Temperature mappings (mm/year and °C)
RAINFALL_RANGES = {
    'Low': {'min': 0, 'max': 600},
    'Medium': {'min': 600, 'max': 1200},
    'High': {'min': 1200, 'max': 3000}
}

TEMPERATURE_RANGES = {
    'Low': {'min': 10, 'max': 20},
    'Medium': {'min': 20, 'max': 28},
    'High': {'min': 28, 'max': 40}
}

# Admin credentials are now stored securely in the admin_users database table.
# Use seed_admin.py to create or reset the initial admin user.



# ============================================================================
# DATABASE CONNECTION FUNCTIONS
# ============================================================================

from psycopg2 import pool
import threading

# Initialize the connection pool globally (but lazily)
db_pool = None
pool_lock = threading.Lock()

def get_db_connection():
    """
    Get a database connection from the connection pool.
    Initializes the pool lazily on the first request to prevent Gunicorn fork deadlocks.
    """
    global db_pool
    try:
        if db_pool is None:
            with pool_lock:
                # Double-check inside lock
                if db_pool is None:
                    if DATABASE_URL:
                        db_pool = psycopg2.pool.ThreadedConnectionPool(1, 15, DATABASE_URL)
                    else:
                        db_pool = psycopg2.pool.ThreadedConnectionPool(1, 15, **DB_CONFIG)
                    logger.info("PostgreSQL connection pool created successfully for this worker")
        
        return db_pool.getconn()
    except Error as e:
        logger.error(f"Error getting connection from PostgreSQL pool: {e}")
        # Emergency fallback
        try:
            if DATABASE_URL:
                return psycopg2.connect(DATABASE_URL)
            else:
                return psycopg2.connect(**DB_CONFIG)
        except Exception as inner_e:
            logger.error(f"Fallback connection also failed: {inner_e}")
            return None


def close_db_connection(connection, cursor=None):
    """
    Safely close the cursor and return the connection to the pool.
    """
    try:
        if cursor:
            cursor.close()
        if connection:
            if db_pool:
                db_pool.putconn(connection)
            elif not connection.closed:
                connection.close()
    except Exception as e:
        logger.error(f"Error returning database connection to pool: {e}")


# ============================================================================
# ADMIN AUTHENTICATION FUNCTIONS
# ============================================================================

def login_required(f):
    """
    Decorator to protect admin routes - requires admin login
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Please log in to access the admin panel.', 'warning')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function


def verify_admin_credentials(username: str, password: str):
    """
    Verify admin login credentials against the admin_users database table.
    Uses Werkzeug's check_password_hash to safely compare hashed passwords.

    Args:
        username: Username submitted via login form
        password: Plain-text password submitted via login form

    Returns:
        Admin user dict if credentials are valid and account is active, else None
    """
    connection = get_db_connection()
    if not connection:
        return None
    try:
        cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT * FROM admin_users WHERE username = %s AND is_active = 1",
            (username,)
        )
        user = cursor.fetchone()
        if user and check_password_hash(user['password_hash'], password):
            # Stamp last login time
            cursor.execute(
                "UPDATE admin_users SET last_login = NOW() WHERE id = %s",
                (user['id'],)
            )
            connection.commit()
            return user
        return None
    except Error as e:
        print(f"Error verifying admin credentials: {e}")
        return None
    finally:
        close_db_connection(connection, cursor)


def log_admin_activity(admin_id, action_type: str, description: str):
    """
    Write an entry to the admin_activity_log table.

    Args:
        admin_id:    ID of the admin performing the action (or None for system)
        action_type: Short category string e.g. 'login', 'crop_add'
        description: Human-readable description of the action
    """
    connection = get_db_connection()
    if not connection:
        return
    try:
        cursor = connection.cursor()
        ip = request.remote_addr if request else None
        cursor.execute(
            """
            INSERT INTO admin_activity_log (admin_id, action_type, description, ip_address)
            VALUES (%s, %s, %s, %s)
            """,
            (admin_id, action_type, description, ip)
        )
        connection.commit()
    except Error as e:
        print(f"Error logging admin activity: {e}")
    finally:
        close_db_connection(connection, cursor)


def get_dashboard_stats() -> Dict:
    """
    Calculate dashboard statistics from database
    
    Returns:
        Dictionary containing dashboard metrics
    """
    connection = get_db_connection()
    if not connection:
        return {
            'total_diagnoses': 0,
            'active_users': 0,
            'crops_count': 0,
            'system_uptime': '99.8%'
        }
    
    try:
        cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Get crops count
        cursor.execute("SELECT COUNT(*) AS cnt FROM crops")
        crops_count = cursor.fetchone()['cnt']

        # Total diagnoses across ALL methods from diagnosis_sessions
        cursor.execute("SELECT COUNT(*) AS cnt FROM diagnosis_sessions")
        total_diagnoses = cursor.fetchone()['cnt']

        # Breakdown by method (knowledge_base, image_analysis, hybrid, etc.)
        cursor.execute("""
            SELECT COALESCE(method, 'unknown') AS method, COUNT(*) AS cnt
            FROM diagnosis_sessions
            GROUP BY method
        """)
        diagnoses_by_method = {row['method']: row['cnt'] for row in cursor.fetchall()}

        # Count how many diagnoses happened today
        cursor.execute("""
            SELECT COUNT(*) AS cnt FROM diagnosis_sessions
            WHERE DATE(created_at) = CURRENT_DATE
        """)
        diagnoses_today = cursor.fetchone()['cnt']

        # Build a human-readable trend label
        if total_diagnoses > 0:
            pct = round((diagnoses_today / total_diagnoses) * 100, 1)
            diagnoses_trend = f'+{diagnoses_today} today ({pct}%)'
        else:
            diagnoses_trend = '0 today'

        # Advisory sessions count
        try:
            cursor.execute("SELECT COUNT(*) AS cnt FROM advisory_sessions")
            advisory_count = cursor.fetchone()['cnt']
        except Error:
            advisory_count = 0

        stats = {
            'total_diagnoses':     total_diagnoses,
            'diagnoses_by_method': diagnoses_by_method,
            'diagnoses_trend':     diagnoses_trend,
            'diagnoses_today':     diagnoses_today,
            'advisory_count':      advisory_count,
            'crops_count':         crops_count,
            'system_uptime':       '99.9%',
        }
        
        return stats
        
    except Error as e:
        print(f"Error fetching dashboard stats: {e}")
        return {
            'total_diagnoses': 0,
            'active_users': 0,
            'crops_count': 0,
            'system_uptime': '99.8%'
        }
    finally:
        close_db_connection(connection, cursor)


# ============================================================================
# HELPER FUNCTIONS FOR RANGE CALCULATIONS
# ============================================================================

def get_rainfall_avg(rainfall_level: str) -> int:
    """
    Convert rainfall level (Low/Medium/High) to average mm/year
    """
    ranges = RAINFALL_RANGES.get(rainfall_level, RAINFALL_RANGES['Medium'])
    return (ranges['min'] + ranges['max']) // 2


def get_temperature_avg(temperature_level: str) -> int:
    """
    Convert temperature level (Low/Medium/High) to average °C
    """
    ranges = TEMPERATURE_RANGES.get(temperature_level, TEMPERATURE_RANGES['Medium'])
    return (ranges['min'] + ranges['max']) // 2


def calculate_suitability_score(soil_ph: float, rainfall_mm: int, temp_c: int,
                                ph_min: float, ph_opt_min: float, ph_opt_max: float, ph_max: float,
                                rain_min: int, rain_opt_min: int, rain_opt_max: int, rain_max: int,
                                temp_min: int, temp_opt_min: int, temp_opt_max: int, temp_max: int) -> int:
    """
    Calculate suitability score (0-100) based on how well conditions match crop requirements
    
    Returns:
        int: Score from 0-100 where 100 is optimal conditions
    """
    # Convert all parameters to float to handle Decimal types from database
    soil_ph = float(soil_ph)
    rainfall_mm = float(rainfall_mm)
    temp_c = float(temp_c)
    ph_min = float(ph_min)
    ph_opt_min = float(ph_opt_min)
    ph_opt_max = float(ph_opt_max)
    ph_max = float(ph_max)
    rain_min = float(rain_min)
    rain_opt_min = float(rain_opt_min)
    rain_opt_max = float(rain_opt_max)
    rain_max = float(rain_max)
    temp_min = float(temp_min)
    temp_opt_min = float(temp_opt_min)
    temp_opt_max = float(temp_opt_max)
    temp_max = float(temp_max)
    
    score = 0
    
    # Soil pH scoring (35 points max)
    if ph_opt_min <= soil_ph <= ph_opt_max:
        score += 35  # Optimal range
    elif ph_min <= soil_ph <= ph_max:
        # In acceptable range but not optimal
        if soil_ph < ph_opt_min:
            distance = ph_opt_min - soil_ph
            max_distance = ph_opt_min - ph_min
        else:
            distance = soil_ph - ph_opt_max
            max_distance = ph_max - ph_opt_max
        
        score += max(0, 35 - int((distance / max_distance) * 20))
    # else: 0 points if outside acceptable range
    
    # Rainfall scoring (35 points max)
    if rain_opt_min <= rainfall_mm <= rain_opt_max:
        score += 35  # Optimal range
    elif rain_min <= rainfall_mm <= rain_max:
        # In acceptable range but not optimal
        if rainfall_mm < rain_opt_min:
            distance = rain_opt_min - rainfall_mm
            max_distance = rain_opt_min - rain_min
        else:
            distance = rainfall_mm - rain_opt_max
            max_distance = rain_max - rain_opt_max
        
        score += max(0, 35 - int((distance / max_distance) * 20))
    
    # Temperature scoring (30 points max)
    if temp_opt_min <= temp_c <= temp_opt_max:
        score += 30  # Optimal range
    elif temp_min <= temp_c <= temp_max:
        # In acceptable range but not optimal
        if temp_c < temp_opt_min:
            distance = temp_opt_min - temp_c
            max_distance = temp_opt_min - temp_min
        else:
            distance = temp_c - temp_opt_max
            max_distance = temp_max - temp_opt_max
        
        score += max(0, 30 - int((distance / max_distance) * 15))
    
    return min(100, max(0, score))


# ============================================================================
# CROP ADVISORY FUNCTIONS & IN-MEMORY REFERENCE CACHE
# ============================================================================

_REF_CACHE_LOCK = threading.Lock()
_REF_CACHE = {
    'counties': {'data': None, 'expires_at': 0},
    'crops': {'data': None, 'expires_at': 0},
    'sub_counties': {}  # {county_lower: {'data': [...], 'expires_at': 0}}
}
_REF_CACHE_TTL = 3600  # 1 hour TTL for static geographical and agronomic taxonomies


def get_all_counties() -> List[str]:
    """
    Fetch all counties from kenya_counties table with in-memory caching
    
    Returns:
        List of county names sorted alphabetically
    """
    now = time.time()
    with _REF_CACHE_LOCK:
        cached = _REF_CACHE['counties']
        if cached['data'] is not None and now < cached['expires_at']:
            return list(cached['data'])

    connection = get_db_connection()
    if not connection:
        logger.error("Failed to connect to database in get_all_counties")
        with _REF_CACHE_LOCK:
            if _REF_CACHE['counties']['data'] is not None:
                return list(_REF_CACHE['counties']['data'])
        return []
    
    cursor = None
    try:
        cursor = connection.cursor()
        query = "SELECT county_name FROM kenya_counties ORDER BY county_name ASC"
        cursor.execute(query)
        counties = [row[0] for row in cursor.fetchall()]
        
        with _REF_CACHE_LOCK:
            _REF_CACHE['counties']['data'] = counties
            _REF_CACHE['counties']['expires_at'] = now + _REF_CACHE_TTL

        return counties
        
    except Error as e:
        logger.error(f"Error fetching counties: {e}")
        return []
    finally:
        close_db_connection(connection, cursor)


def get_all_crops() -> List[str]:
    """
    Fetch all crops from crops table with in-memory caching
    
    Returns:
        List of crop names sorted alphabetically
    """
    now = time.time()
    with _REF_CACHE_LOCK:
        cached = _REF_CACHE['crops']
        if cached['data'] is not None and now < cached['expires_at']:
            return list(cached['data'])

    connection = get_db_connection()
    if not connection:
        logger.error("Failed to connect to database in get_all_crops")
        with _REF_CACHE_LOCK:
            if _REF_CACHE['crops']['data'] is not None:
                return list(_REF_CACHE['crops']['data'])
        return []
    
    cursor = None
    try:
        cursor = connection.cursor()
        query = "SELECT crop_name FROM crops ORDER BY crop_name ASC"
        cursor.execute(query)
        crops = [row[0] for row in cursor.fetchall()]
        
        with _REF_CACHE_LOCK:
            _REF_CACHE['crops']['data'] = crops
            _REF_CACHE['crops']['expires_at'] = now + _REF_CACHE_TTL

        return crops
        
    except Error as e:
        logger.error(f"Error fetching crops: {e}")
        return []
    finally:
        close_db_connection(connection, cursor)


def get_sub_counties_by_county(county: str) -> List[str]:
    """
    Fetch sub-counties for a specific county from sub_counties table with in-memory caching
    
    Args:
        county: County name to filter by
        
    Returns:
        List of sub-county names sorted alphabetically
    """
    if not county:
        return []

    c_key = county.strip().lower()
    now = time.time()
    with _REF_CACHE_LOCK:
        cached = _REF_CACHE['sub_counties'].get(c_key)
        if cached is not None and cached['data'] is not None and now < cached['expires_at']:
            return list(cached['data'])

    connection = get_db_connection()
    if not connection:
        logger.error("Failed to connect to database in get_sub_counties_by_county")
        return []
    
    cursor = None
    try:
        cursor = connection.cursor()
        query = """
            SELECT sc.sub_county_name 
            FROM sub_counties sc
            INNER JOIN kenya_counties kc ON sc.county_id = kc.county_id
            WHERE LOWER(kc.county_name) = LOWER(%s)
            ORDER BY sc.sub_county_name ASC
        """
        cursor.execute(query, (county,))
        sub_counties = [row[0] for row in cursor.fetchall()]
        
        with _REF_CACHE_LOCK:
            _REF_CACHE['sub_counties'][c_key] = {
                'data': sub_counties,
                'expires_at': now + _REF_CACHE_TTL
            }

        return sub_counties
        
    except Error as e:
        logger.error(f"Error fetching sub-counties: {e}")
        return []
    finally:
        close_db_connection(connection, cursor)


def get_crop_recommendations(county_id: int, county_name: str,
                              soil_ph: float, rainfall: str,
                              temperature: str,
                              ph_confidence: str = 'county_default') -> List[Dict]:

    rainfall_mm   = get_rainfall_avg(rainfall)
    temp_c        = get_temperature_avg(temperature)
    ph_tolerance  = 0.4 if ph_confidence == 'county_default' else 0.0

    connection = get_db_connection()
    if not connection:
        return []

    try:
        cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        query = """
        SELECT
            c.crop_id,
            c.crop_name,
            c.scientific_name,
            c.description,
            c.season,
            c.growth_period_days,
            c.yield_potential,
            c.crop_image_url,
            cs.ph_min,
            cs.ph_optimal_min,
            cs.ph_optimal_max,
            cs.ph_max,
            cs.rain_min_mm,
            cs.rain_optimal_min_mm,
            cs.rain_optimal_max_mm,
            cs.rain_max_mm,
            cs.temp_min_c,
            cs.temp_optimal_min_c,
            cs.temp_optimal_max_c,
            cs.temp_max_c,
            COALESCE(ccs.suitability_modifier, 1.00) AS county_modifier,
            COALESCE(ccs.growing_notes, '')           AS growing_notes,
            COALESCE(ccs.commonly_grown, 1)           AS commonly_grown
        FROM crop_suitability cs
        INNER JOIN crops c
            ON c.crop_id = cs.crop_id
        LEFT JOIN crop_county_suitability ccs
            ON  ccs.crop_id   = c.crop_id
            AND ccs.county_id = %s
        WHERE
            %s BETWEEN cs.ph_min  - %s AND cs.ph_max  + %s
            AND %s BETWEEN cs.rain_min_mm  AND cs.rain_max_mm
            AND %s BETWEEN cs.temp_min_c   AND cs.temp_max_c
            AND COALESCE(ccs.suitability_modifier, 1.00) >= 0.30
            AND COALESCE(ccs.commonly_grown, 1) = 1
        """

        cursor.execute(query, (
            county_id,
            soil_ph, ph_tolerance, ph_tolerance,
            rainfall_mm,
            temp_c
        ))
        results = cursor.fetchall()
        print(f"[ADVISORY] {len(results)} crops matched for "
              f"{county_name} pH={soil_ph} Rain={rainfall} Temp={temperature}")

        scored_crops = []
        for row in results:
            base_score      = calculate_suitability_score(
                soil_ph, rainfall_mm, temp_c,
                row['ph_min'],  row['ph_optimal_min'],
                row['ph_optimal_max'], row['ph_max'],
                row['rain_min_mm'], row['rain_optimal_min_mm'],
                row['rain_optimal_max_mm'], row['rain_max_mm'],
                row['temp_min_c'], row['temp_optimal_min_c'],
                row['temp_optimal_max_c'], row['temp_max_c']
            )
            county_modifier = float(row.get('county_modifier', 1.00))
            adjusted_score  = min(100, int(base_score * county_modifier))

            scored_crops.append({
                'crop':            row,
                'score':           adjusted_score,
                'base_score':      base_score,
                'county_modifier': county_modifier
            })

        scored_crops.sort(key=lambda x: x['score'], reverse=True)
        top_5 = scored_crops[:5]

        recommendations = []
        for idx, item in enumerate(top_5, 1):
            row   = item['crop']
            score = item['score']
            notes = []

            if row['ph_optimal_min'] <= soil_ph <= row['ph_optimal_max']:
                notes.append(f"Soil pH {soil_ph} is optimal for this crop")
            elif row['ph_min'] <= soil_ph <= row['ph_max']:
                notes.append(
                    f"Soil pH {soil_ph} is acceptable "
                    f"(optimal: {row['ph_optimal_min']} to {row['ph_optimal_max']})"
                )

            if row['rain_optimal_min_mm'] <= rainfall_mm <= row['rain_optimal_max_mm']:
                notes.append("Rainfall is optimal")
            elif row['rain_min_mm'] <= rainfall_mm <= row['rain_max_mm']:
                notes.append(
                    f"Rainfall is acceptable "
                    f"(optimal: {row['rain_optimal_min_mm']} to "
                    f"{row['rain_optimal_max_mm']} mm/year)"
                )

            if row['temp_optimal_min_c'] <= temp_c <= row['temp_optimal_max_c']:
                notes.append("Temperature is optimal")
            elif row['temp_min_c'] <= temp_c <= row['temp_max_c']:
                notes.append(
                    f"Temperature is acceptable "
                    f"(optimal: {row['temp_optimal_min_c']} to "
                    f"{row['temp_optimal_max_c']}°C)"
                )

            recommendations.append({
                'rank':             idx,
                'id':               row['crop_id'],
                'name':             row['crop_name'],
                'scientific_name':  row.get('scientific_name', ''),
                'description':      row.get('description', ''),
                'season':           row.get('season', 'Year Round'),
                'growth_period':    row.get('growth_period_days', 0),
                'yield_potential':  row.get('yield_potential', 'Medium'),
                'crop_image_url':   row.get('crop_image_url', ''),
                'suitability_score': score,
                'base_score':       item['base_score'],
                'county_modifier':  item['county_modifier'],
                'growing_notes':    row.get('growing_notes', ''),
                'ph_range':         f"{row['ph_min']} to {row['ph_max']}",
                'ph_optimal':       f"{row['ph_optimal_min']} to {row['ph_optimal_max']}",
                'rainfall_range':   f"{row['rain_min_mm']} to {row['rain_max_mm']} mm/year",
                'temp_range':       f"{row['temp_min_c']} to {row['temp_max_c']}°C",
                'notes':            ' | '.join(notes) if notes else
                                    'Conditions are suitable for this crop.',
            })

        return recommendations
        
    except Error as e:
        print(f"Error fetching crop recommendations: {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        close_db_connection(connection, cursor)


def get_alternative_recommendations(county: str, soil_ph: float, rainfall: str, temperature: str) -> List[Dict]:
    """
    Get alternative recommendations with more relaxed constraints
    Used when strict search returns no results
    """
    connection = get_db_connection()
    if not connection:
        return []
    
    try:
        cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        rainfall_mm = get_rainfall_avg(rainfall)
        temp_c = get_temperature_avg(temperature)
        
        # Widen the search ranges
        ph_tolerance = 0.5
        rain_tolerance = 200
        temp_tolerance = 3
        
        query = """
        SELECT 
            c.crop_id,
            c.crop_name,
            c.scientific_name,
            c.description,
            c.season,
            c.growth_period_days,
            c.yield_potential,
            c.crop_image_url,
            cs.ph_min,
            cs.ph_optimal_min,
            cs.ph_optimal_max,
            cs.ph_max,
            cs.rain_min_mm,
            cs.rain_optimal_min_mm,
            cs.rain_optimal_max_mm,
            cs.rain_max_mm,
            cs.temp_min_c,
            cs.temp_optimal_min_c,
            cs.temp_optimal_max_c,
            cs.temp_max_c
        FROM crop_suitability cs
        INNER JOIN crops c ON cs.crop_id = c.crop_id
        WHERE 
            %s BETWEEN (cs.ph_min - %s) AND (cs.ph_max + %s)
            AND %s BETWEEN (cs.rain_min_mm - %s) AND (cs.rain_max_mm + %s)
            AND %s BETWEEN (cs.temp_min_c - %s) AND (cs.temp_max_c + %s)
        ORDER BY 
            ABS(%s - (cs.ph_optimal_min + cs.ph_optimal_max) / 2),
            ABS(%s - (cs.rain_optimal_min_mm + cs.rain_optimal_max_mm) / 2)
        LIMIT 5
        """
        
        params = (
            soil_ph, ph_tolerance, ph_tolerance,
            rainfall_mm, rain_tolerance, rain_tolerance,
            temp_c, temp_tolerance, temp_tolerance,
            soil_ph, rainfall_mm
        )
        
        cursor.execute(query, params)
        results = cursor.fetchall()
        
        recommendations = []
        for idx, row in enumerate(results, 1):
            recommendations.append({
                'rank': idx,
                'id': row['crop_id'],
                'name': row['crop_name'],
                'scientific_name': row.get('scientific_name', ''),
                'description': row.get('description', ''),
                'season': row.get('season', 'Year Round'),
                'growth_period': row.get('growth_period_days', 0),
                'yield_potential': row.get('yield_potential', 'Medium'),
                'crop_image_url': row.get('crop_image_url', ''),
                'suitability_score': 50,
                'is_optimal': False,
                'ph_range': f"{row['ph_min']} - {row['ph_max']}",
                'ph_optimal': f"{row['ph_optimal_min']} - {row['ph_optimal_max']}",
                'rainfall_range': f"{row['rain_min_mm']} - {row['rain_max_mm']} mm/year",
                'temp_range': f"{row['temp_min_c']} - {row['temp_max_c']}°C",
                'notes': '[!] Alternative recommendation: Your conditions are slightly outside the ideal range for this crop.',
                'is_alternative': True
            })
        
        return recommendations
        
    except Error as e:
        print(f"Error fetching alternative recommendations: {e}")
        return []
    finally:
        close_db_connection(connection, cursor)


# ============================================================================
# OPENROUTER AI VISION API INTEGRATION (FREE)
# ============================================================================

def call_plant_id_api(image_files: List, crop_name: str = None, kb_diseases: List[Dict] = None) -> Dict:
    """
    Call OpenRouter AI Vision API for crop disease identification.
    Uses KB diseases as context to guide visual confirmation (guided + independent analysis).
    Tries multiple free models with automatic fallback.
    """
    if not OPENROUTER_API_KEY:
        return {'success': False, 'error': 'OpenRouter API key not configured. Add OPENROUTER_API_KEY to your .env file.'}

    try:
        # Build image parts
        base64_images = []
        for i, image_file in enumerate(image_files):
            image_data = image_file.read()

            # Detect actual image type rather than guessing from header bytes
            img_type = get_image_type(image_data)

            base64_str = base64.b64encode(image_data).decode('utf-8')
            base64_images.append(f"data:image/{img_type};base64,{base64_str}")
            image_file.seek(0)
            print(f"[OpenRouter] Image {i+1}: type={img_type}, size={len(image_data)} bytes")

        # Build KB context block from passed-in diseases
        kb_context = ""
        if kb_diseases:
            disease_lines = "\n".join([
                f"- {d['name']} ({d.get('scientific_name', '')}): {d.get('description', '')[:100]}"
                for d in kb_diseases[:8]
            ])
            kb_context = f"""
The crop is {crop_name}. Based on agronomic records for this region,
the most probable diseases to look for are:

{disease_lines}

Your primary task is to visually confirm or rule out each of these.
You may also identify additional conditions you observe that are not on this list.
"""

        prompt = f"""You are an expert agricultural plant pathologist
specialising in East African smallholder crops, particularly in the Kenyan context.
{kb_context}
Your task has two parts:

PART 1 - GUIDED ANALYSIS:
Examine the image and visually confirm or rule out each disease in the list above.
For each one you can confirm, provide your visual evidence and confidence score.

PART 2 - INDEPENDENT ANALYSIS:
Separately examine the image without reference to the list above.
If you observe symptoms that suggest a disease NOT in the provided list,
identify it based on common crop diseases affecting {crop_name or 'this crop'} in Kenya.
Use your knowledge of diseases prevalent in Kenyan smallholder farming contexts,
including those reported by KALRO, FAO East Africa advisories, and regional
agricultural extension literature.

Combine both analyses into a single ranked result list.

Respond ONLY with valid JSON in this exact format, no markdown:
{{
  "is_healthy": true or false,
  "observed_symptoms": "what you actually see in the image",
  "affected_part": "leaf or stem or fruit or root or whole plant",
  "image_quality": "good or poor or unusable",
  "diseases": [
    {{
      "name": "Disease common name",
      "scientific_name": "Latin name or empty string",
      "confidence": 85,
      "visual_evidence": "specific visual symptoms visible in the image",
      "treatment": "practical treatment for Kenyan smallholder context",
      "severity": "low or medium or high",
      "in_kb_list": true or false,
      "source_note": "KB-confirmed or AI-identified or Both"
    }}
  ]
}}

Rules:
- confidence is an integer 0 to 100
- severity is exactly one of: low, medium, high
- in_kb_list is true only if the disease appeared in the list provided above
- source_note must be exactly one of: KB-confirmed, AI-identified, Both
- List up to 4 results ordered by confidence descending
- If image_quality is unusable set all confidence values to 0 and diseases to empty list
- If image_quality is poor set all confidence values below 50
- Return only the JSON object, nothing else"""

        # Build message content
        message_content = [{"type": "text", "text": prompt}]
        for data_uri in base64_images:
            message_content.append({
                "type": "image_url",
                "image_url": {"url": data_uri}
            })

        last_error = "All models unavailable"
        print(f"[OpenRouter] Sending {len(base64_images)} image(s), first URI prefix: {base64_images[0][:45]}")

        for model in OPENROUTER_MODELS:
            print(f"[OpenRouter] Trying {model}...")
            try:
                response = requests.post(
                    OPENROUTER_API_URL,
                    headers={
                        'Authorization': f'Bearer {OPENROUTER_API_KEY}',
                        'Content-Type': 'application/json'
                    },
                    json={
                        'model': model,
                        'messages': [{'role': 'user', 'content': message_content}],
                        'max_tokens': 1200
                    },
                    timeout=30  # Reduced from 60s — fail fast and try next model
                )

                data = response.json()

                if 'choices' not in data:
                    last_error = data.get('error', {}).get('message', 'Unknown error')
                    print(f"[OpenRouter] {model} failed: {last_error}")
                    continue

                raw_text = data['choices'][0]['message']['content']

                if not raw_text:
                    print(f"[OpenRouter] {model} returned empty content - skipping")
                    continue

                raw_text = raw_text.strip()

                # Strip markdown fences if present
                if raw_text.startswith("```"):
                    raw_text = raw_text.split("```")[1]
                    if raw_text.startswith("json"):
                        raw_text = raw_text[4:]
                    raw_text = raw_text.strip()

                parsed = json.loads(raw_text)
                is_healthy      = parsed.get('is_healthy', False)
                raw_diseases    = parsed.get('diseases', [])
                observed_syms   = parsed.get('observed_symptoms', '')
                affected_part   = parsed.get('affected_part', '')
                img_quality     = parsed.get('image_quality', 'good')

                print(f"[OpenRouter] {model} responded. Symptoms: {observed_syms[:80]}")

                diseases = []
                for d in raw_diseases[:4]:
                    confidence  = int(d.get('confidence', 50))
                    in_kb       = d.get('in_kb_list', False)
                    src         = d.get('source_note', 'AI-identified')

                    # Choose treatment note based on source and confidence
                    if confidence < 45 or img_quality == 'poor':
                        treatment_note = (
                            d.get('treatment', '') +
                            " Note: confidence is low. Please verify with a local extension officer."
                        )
                    elif not in_kb:
                        # AI independently identified this — no KB treatment data available
                        treatment_note = (
                            d.get('treatment', 'Consult a local agricultural extension officer.') +
                            " Note: this condition was not found in the local knowledge base. "
                            "Treatment is based on general East African agronomic guidance."
                        )
                    else:
                        treatment_note = d.get('treatment', 'Consult a local agricultural extension officer.')

                    diseases.append({
                        'name':              d.get('name', 'Unknown Disease'),
                        'scientific_name':   d.get('scientific_name', ''),
                        'confidence':        confidence,
                        'low_confidence':    confidence < 45,
                        'in_kb_list':        in_kb,
                        'source_note':       src,
                        'image_quality':     img_quality,
                        'source':            'AI Vision' if not in_kb else 'AI + Knowledge Base',
                        'description':       d.get('visual_evidence', d.get('description', '')),
                        'treatment':         treatment_note,
                        'severity':          d.get('severity', 'medium'),
                        'ai_label':          d.get('scientific_name', '').lower().strip(),
                        'observed_symptoms': observed_syms,
                        'affected_part':     affected_part,
                        'treatment_groups':  {}
                    })

                if not diseases:
                    is_healthy = True
                    diseases = [{
                        'name':             'Healthy Crop',
                        'confidence':       90,
                        'source':           'AI Vision',
                        'description':      'No significant diseases detected. Your crop appears healthy!',
                        'treatment':        'Continue regular care and monitoring.',
                        'severity':         'low',
                        'ai_label':         '',
                        'treatment_groups': {}
                    }]

                print(f"[OpenRouter] Found {len(diseases)} result(s). Healthy: {is_healthy}")
                return {
                    'success':            True,
                    'diseases':           diseases,
                    'is_healthy':         is_healthy,
                    'observed_symptoms':  observed_syms,
                    'uploaded_image_url': base64_images[0] if base64_images else None
                }

            except json.JSONDecodeError as e:
                print(f"[OpenRouter] {model} JSON parse error: {e}")
                continue
            except Exception as e:
                print(f"[OpenRouter] {model} error: {e}")
                continue

        return {'success': False, 'error': 'All AI models are currently unavailable. Please try again shortly.'}

    except Exception as e:
        print(f"[OpenRouter] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}


# ============================================================================
# USER AUTHENTICATION HELPERS & GUARDS
# ============================================================================

def get_current_user():
    """Return current logged-in user dict or None."""
    uid = session.get('user_id')
    if not uid:
        return None
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, full_name, email, phone, role, county_id, sub_county_id, profile_photo FROM users WHERE id=%s AND is_active=TRUE", (uid,))
        return cur.fetchone()
    finally:
        close_db_connection(conn, cur)


def user_login_required(f):
    """Decorator for authenticated farmer/user routes with safe next redirect."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            dest = request.full_path if request.query_string else request.path
            dest = dest.rstrip('?')
            if not dest.startswith('/') or dest.startswith('//'):
                dest = '/dashboard'
            flash('Please create an account or sign in to access this module.', 'info')
            return redirect(url_for('user_login', next=dest))
        return f(*args, **kwargs)
    return decorated


@app.context_processor
def inject_user():
    """Inject current authenticated user into all templates."""
    return {'current_user': get_current_user(), 'user': get_current_user()}


@app.context_processor
def inject_localization():
    """Inject language dictionary for English & Kiswahili localization."""
    current_lang = session.get('lang', 'en')
    translations = {
        'en': {
            'dashboard': 'Dashboard',
            'my_farms': 'My Farms',
            'crops_planning': 'Crops & Planning',
            'marketplace': 'Marketplace',
            'ai_assistant': 'AI Assistant',
            'advisory': 'Crop Advisory',
            'diagnosis': 'Crop Diagnosis',
            'weather_alerts': 'Weather Alerts',
            'resources': 'Resources',
            'profile': 'Profile',
            'sign_out': 'Sign Out',
            'welcome': 'Welcome',
            'search_placeholder': 'Search crops, diseases, or get advice...',
            'lang_name': 'English',
            'other_lang': 'sw',
            'other_lang_name': 'Kiswahili',
        },
        'sw': {
            'dashboard': 'Dashibodi',
            'my_farms': 'Mashamba Yangu',
            'crops_planning': 'Mazao na Mipango',
            'marketplace': 'Soko la Mazao',
            'ai_assistant': 'Msaidizi wa AI',
            'advisory': 'Ushauri wa Mazao',
            'diagnosis': 'Utambuzi wa Magonjwa',
            'weather_alerts': 'Tahadhari za Hewa',
            'resources': 'Mwongozo na Rasilimali',
            'profile': 'Wasifu Wangu',
            'sign_out': 'Toka',
            'welcome': 'Karibu',
            'search_placeholder': 'Tafuta mazao, magonjwa, au pata ushauri...',
            'lang_name': 'Kiswahili',
            'other_lang': 'en',
            'other_lang_name': 'English',
        }
    }
    return {
        'current_lang': current_lang,
        't': translations.get(current_lang, translations['en'])
    }


# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route('/set-language/<lang>')
def set_language(lang):
    """Toggle language between English and Kiswahili"""
    if lang in ('en', 'sw'):
        session['lang'] = lang
    next_url = request.referrer or url_for('user_dashboard')
    if not next_url.startswith('/') or next_url.startswith('//'):
        next_url = url_for('user_dashboard')
    return redirect(next_url)


@app.route('/healthz')
def healthz():
    """Liveness & readiness probe for load balancers and orchestrators"""
    db_ok = False
    conn = get_db_connection()
    cur = None
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            db_ok = bool(cur.fetchone())
        except Exception as e:
            logger.error(f"Healthcheck DB query failed: {e}")
        finally:
            close_db_connection(conn, cur)
    
    if db_ok:
        return jsonify({'status': 'healthy', 'db': 'connected'}), 200
    return jsonify({'status': 'unhealthy', 'db': 'disconnected'}), 503


@app.route('/')
def index():
    """Home page route"""
    return render_template('index.html')


@app.route('/test-form')
def test_form():
    """Simple test form without JavaScript"""
    return render_template('test_form.html')


@app.route('/advisory', methods=['GET', 'POST'])
def advisory():
    """
    Crop Advisory page route
    """
    counties = get_all_counties()
    
    if not counties:
        flash('Unable to load counties from database. Please check your database connection.', 'error')
        return render_template('advisory.html', counties=[])
    
    if request.method == 'POST':
        try:
            print("\n" + "="*70)
            print("ADVISORY FORM SUBMISSION DEBUG")
            print("="*70)
            
            # Extract form data
            county = request.form.get('county', '').strip()
            soil_ph_str = request.form.get('soil_ph', '').strip()
            rainfall = request.form.get('rainfall', '').strip()
            temperature = request.form.get('temperature', '').strip()
            
            print(f"Form data received:")
            print(f"  County: '{county}'")
            print(f"  Soil pH: '{soil_ph_str}'")
            print(f"  Rainfall: '{rainfall}'")
            print(f"  Temperature: '{temperature}'")
            
            # Validation
            if not all([county, soil_ph_str, rainfall, temperature]):
                print("[ERROR] Missing required fields")
                flash('Please fill in all required fields.', 'error')
                return render_template('advisory.html', counties=counties)
            
            try:
                soil_ph = float(soil_ph_str)
                print(f"Converted soil_ph to float: {soil_ph}")
            except ValueError:
                print("[ERROR] Invalid soil pH value")
                flash('Soil pH must be a valid number.', 'error')
                return render_template('advisory.html', counties=counties)
            
            if not (3.0 <= soil_ph <= 10.0):
                print(f"[ERROR] Soil pH {soil_ph} out of range")
                flash('Soil pH must be between 3.0 and 10.0', 'error')
                return render_template('advisory.html', counties=counties)
            
            valid_levels = ['Low', 'Medium', 'High']
            if rainfall not in valid_levels or temperature not in valid_levels:
                print(f"[ERROR] Invalid rainfall or temperature level")
                flash('Please select valid rainfall and temperature levels.', 'error')
                return render_template('advisory.html', counties=counties)
            
            print("[OK] All validations passed")
            print(f"Calling get_crop_recommendations...")

            # Resolve county_id from name (required by get_crop_recommendations)
            _county_id = None
            _conn_lookup = get_db_connection()
            if _conn_lookup:
                try:
                    _cur_lookup = _conn_lookup.cursor()
                    _cur_lookup.execute(
                        "SELECT county_id FROM kenya_counties WHERE county_name = %s",
                        (county,)
                    )
                    _row = _cur_lookup.fetchone()
                    if _row:
                        _county_id = _row[0]
                except Exception as _e:
                    print(f"[ADVISORY] County lookup error: {_e}")
                finally:
                    close_db_connection(_conn_lookup, _cur_lookup)

            if _county_id is None:
                flash(f'County "{county}" not found in database.', 'error')
                return render_template('advisory.html', counties=counties)

            # Get recommendations
            recommended_crops = get_crop_recommendations(
                county_id=_county_id,
                county_name=county,
                soil_ph=soil_ph,
                rainfall=rainfall,
                temperature=temperature
            )
            
            print(f"[OK] Received {len(recommended_crops)} recommendations")
            
            # Fallback to alternatives if no results
            if not recommended_crops:
                print("[INFO] No exact matches, trying alternatives...")
                recommended_crops = get_alternative_recommendations(
                    county, soil_ph, rainfall, temperature
                )
                
                if recommended_crops:
                    flash('No exact matches found. Showing alternative crops.', 'info')
                else:
                    flash('No suitable crops found for your conditions.', 'warning')
            else:
                flash(f'Found {len(recommended_crops)} suitable crops!', 'success')
            
            # Store in session for results page
            session['recommended_crops'] = recommended_crops
            session['user_input'] = {
                'county': county,
                'soil_ph': soil_ph,
                'rainfall': rainfall,
                'temperature': temperature
            }
            
            print(f"[OK] Rendering results.html with {len(recommended_crops)} crops")
            print("="*70 + "\n")
            
            # ── Log this advisory session to the database ─────────────────
            try:
                _conn2 = get_db_connection()
                if _conn2:
                    _cur2 = _conn2.cursor()
                    _sub_county = request.form.get('sub_county', '')
                    _cur2.execute("""
                        INSERT INTO advisory_sessions
                            (county, sub_county, soil_ph, rainfall, temperature, result_count, ip_address)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (county, _sub_county, soil_ph, rainfall, temperature,
                          len(recommended_crops), request.remote_addr))
                    _conn2.commit()
                    close_db_connection(_conn2, _cur2)
                    print(f"[ADVISORY] Session logged: {county}, crops={len(recommended_crops)}")
            except Exception as _adv_e:
                print(f"[ADVISORY] Could not log session: {_adv_e}")


            return render_template('results.html', recommended_crops=recommended_crops)
            
        except Exception as e:
            print(f"[EXCEPTION] Error in advisory route: {e}")
            import traceback
            traceback.print_exc()
            flash('An error occurred. Please try again.', 'error')
            return render_template('advisory.html', counties=counties)
    
    return render_template('advisory.html', counties=counties)


@app.route('/diagnosis')
def diagnosis():
    """Disease diagnosis page"""
    crops = get_all_crops()
    
    if not crops:
        flash('Unable to load crops from database. Please check your database connection.', 'error')
    
    return render_template('diagnosis.html', crops=crops)


@app.route('/diagnosis/results')
def diagnosis_results():
    """Disease diagnosis results page with DB session lookup."""
    session_ref = request.args.get('ref') or session.get('diagnosis_session_ref')
    results = None

    if session_ref:
        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SELECT results_data FROM diagnosis_sessions WHERE session_ref=%s", (session_ref,))
                row = cur.fetchone()
                if row and row.get('results_data'):
                    results = json.loads(row['results_data'])
            except Exception as e:
                logger.error(f"Error fetching diagnosis results from DB: {e}")
            finally:
                close_db_connection(conn, cur)

    if not results:
        results = session.get('diagnosis_results', {})

    if not results or not results.get('diseases'):
        flash('No diagnosis results found. Please complete a diagnosis first.', 'warning')
        return redirect(url_for('diagnosis'))

    return render_template('diagnosis_results.html',
                           crop=results.get('crop', 'Unknown'),
                           method=results.get('method', 'unknown'),
                           plant_part=results.get('plant_part', ''),
                           diseases=results.get('diseases', []),
                           uploaded_image_url=results.get('uploaded_image_url'),
                           session_ref=results.get('session_ref', session_ref))


@app.route('/resources')
def resources():
    """Resources page"""
    return render_template('resources.html') if os.path.exists(os.path.join(BASE_DIR, 'templates', 'resources.html')) else redirect(url_for('index'))


# ============================================================================
# ADMIN PANEL ROUTES
# ============================================================================

@app.route('/admin')
def admin():
    """Admin panel entry point - redirects to login or dashboard"""
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('admin_login'))


@app.route('/admin/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=["POST"])
def admin_login():
    """Admin login page"""
    # If already logged in, redirect to dashboard
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))
    
    # Clear any existing flash messages from other pages (like advisory)
    # to prevent them from showing on the login page
    session.pop('_flashes', None)
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        remember = request.form.get('remember', False)

        user = verify_admin_credentials(username, password)

        if user:
            # Populate session with DB values — no plain-text credentials stored
            session['admin_logged_in'] = True
            session['admin_id']       = user['id']
            session['admin_username'] = user['username']
            session['admin_email']    = user['email']
            session['admin_role']     = user['role']

            if remember:
                session.permanent = True

            log_admin_activity(user['id'], 'login', f"Admin '{user['username']}' logged in.")
            flash('Welcome to PlantWise Admin Panel!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            log_admin_activity(None, 'login_failed', f"Failed login attempt for username '{username}'.")
            flash('Invalid username or password. Please try again.', 'error')

    return render_template('admin_login.html')


@app.route('/admin/logout', methods=['GET', 'POST'])
@login_required
def admin_logout():
    """Admin logout — logs the action then clears the session."""
    admin_id   = session.get('admin_id')
    admin_name = session.get('admin_username', 'Unknown')
    log_admin_activity(admin_id, 'logout', f"Admin '{admin_name}' logged out.")
    session.clear()
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('admin_login'))


@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    """Admin dashboard - main overview page"""
    stats = get_dashboard_stats()

    # Pull real recent activity from DB
    recent_activity = []
    connection = get_db_connection()
    if connection:
        try:
            cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT l.action_type, l.description, l.created_at,
                       COALESCE(u.username, 'System') AS actor
                FROM admin_activity_log l
                LEFT JOIN admin_users u ON u.id = l.admin_id
                ORDER BY l.created_at DESC
                LIMIT 10
            """)
            rows = cur.fetchall()
            for r in rows:
                recent_activity.append({
                    'type':   r['action_type'],
                    'user':   r['actor'],
                    'action': r['description'],
                    'time':   r['created_at'].strftime('%d %b %Y %H:%M') if r['created_at'] else ''
                })
        except Error as e:
            print(f"Error fetching activity log: {e}")
        finally:
            close_db_connection(connection, cur)

    return render_template('admin_dashboard.html',
                           stats=stats,
                           recent_activity=recent_activity,
                           admin_username=session.get('admin_username', 'Admin'))


# ============================================================================
# CROP MANAGEMENT ROUTES
# ============================================================================

@app.route('/admin/crops')
@login_required
def admin_crops():
    """List all crops with search and pagination."""
    search  = request.args.get('search', '').strip()
    season  = request.args.get('season', '').strip()
    page    = max(1, int(request.args.get('page', 1)))
    per_page = 10
    offset  = (page - 1) * per_page

    connection = get_db_connection()
    crops = []
    total = 0
    if connection:
        try:
            cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            base_where = "WHERE 1=1"
            params: list = []
            if search:
                base_where += " AND (crop_name LIKE %s OR scientific_name LIKE %s)"
                params += [f"%{search}%", f"%{search}%"]
            if season:
                base_where += " AND season LIKE %s"
                params.append(f"%{season}%")

            cur.execute(f"SELECT COUNT(*) AS cnt FROM crops {base_where}", params)
            total = cur.fetchone()['cnt']

            cur.execute(
                f"SELECT * FROM crops {base_where} ORDER BY crop_name LIMIT %s OFFSET %s",
                params + [per_page, offset]
            )
            crops = cur.fetchall()
        except Error as e:
            logger.error(f"Database error in admin_crops listing: {e}")
            flash('Unable to load crops. Please try again.', 'error')
        finally:
            close_db_connection(connection, cur)

    total_pages = max(1, (total + per_page - 1) // per_page)
    seasons = ['Long Rains', 'Short Rains', 'Long & Short Rains', 'Dry Season', 'Year Round']

    return render_template('admin_crops.html',
                           crops=crops,
                           search=search,
                           season_filter=season,
                           seasons=seasons,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           admin_username=session.get('admin_username', 'Admin'))


@app.route('/admin/crops/add', methods=['GET', 'POST'])
@login_required
def admin_crop_add():
    """Add a new crop to the database."""
    seasons = ['Long Rains', 'Short Rains', 'Long & Short Rains', 'Dry Season', 'Year Round']

    if request.method == 'POST':
        name            = request.form.get('crop_name', '').strip()
        scientific_name = request.form.get('scientific_name', '').strip()
        description     = request.form.get('description', '').strip()
        season          = request.form.get('season', '').strip()
        growth_period   = request.form.get('growth_period_days', '').strip()
        yield_potential = request.form.get('yield_potential', '').strip()
        image_url       = request.form.get('crop_image_url', '').strip()

        if not name:
            flash('Crop name is required.', 'error')
            return render_template('admin_crop_form.html', action='Add', seasons=seasons,
                                   admin_username=session.get('admin_username', 'Admin'))

        connection = get_db_connection()
        if connection:
            try:
                cur = connection.cursor()
                cur.execute("""
                    INSERT INTO crops
                        (crop_name, scientific_name, description, season,
                         growth_period_days, yield_potential, crop_image_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (name, scientific_name or None, description or None, season or None,
                      int(growth_period) if growth_period else None,
                      yield_potential or None, image_url or None))
                connection.commit()
                log_admin_activity(session.get('admin_id'), 'crop_add',
                                   f"Added new crop: {name}")
                flash(f"Crop '{name}' added successfully!", 'success')
                return redirect(url_for('admin_crops'))
            except Error as e:
                flash(f"Error adding crop: {e}", 'error')
            finally:
                close_db_connection(connection, cur)

    return render_template('admin_crop_form.html', action='Add', crop=None, seasons=seasons,
                           admin_username=session.get('admin_username', 'Admin'))


@app.route('/admin/crops/edit/<int:crop_id>', methods=['GET', 'POST'])
@login_required
def admin_crop_edit(crop_id):
    """Edit an existing crop."""
    seasons = ['Long Rains', 'Short Rains', 'Long & Short Rains', 'Dry Season', 'Year Round']
    connection = get_db_connection()
    crop = None

    if connection:
        try:
            cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM crops WHERE crop_id = %s", (crop_id,))
            crop = cur.fetchone()
        except Error as e:
            logger.error(f"Database error fetching crop ID {crop_id} for edit: {e}")
            flash('Unable to load crop. Please try again.', 'error')
            return redirect(url_for('admin_crops'))
        finally:
            close_db_connection(connection, cur)

    if not crop:
        flash('Crop not found.', 'error')
        return redirect(url_for('admin_crops'))

    if request.method == 'POST':
        name            = request.form.get('crop_name', '').strip()
        scientific_name = request.form.get('scientific_name', '').strip()
        description     = request.form.get('description', '').strip()
        season          = request.form.get('season', '').strip()
        growth_period   = request.form.get('growth_period_days', '').strip()
        yield_potential = request.form.get('yield_potential', '').strip()
        image_url       = request.form.get('crop_image_url', '').strip()

        if not name:
            flash('Crop name is required.', 'error')
            return render_template('admin_crop_form.html', action='Edit', crop=crop,
                                   seasons=seasons,
                                   admin_username=session.get('admin_username', 'Admin'))

        conn2 = get_db_connection()
        if conn2:
            try:
                cur2 = conn2.cursor()
                cur2.execute("""
                    UPDATE crops SET
                        crop_name = %s, scientific_name = %s, description = %s,
                        season = %s, growth_period_days = %s, yield_potential = %s,
                        crop_image_url = %s
                    WHERE crop_id = %s
                """, (name, scientific_name or None, description or None, season or None,
                      int(growth_period) if growth_period else None,
                      yield_potential or None, image_url or None, crop_id))
                conn2.commit()
                log_admin_activity(session.get('admin_id'), 'crop_edit',
                                   f"Edited crop ID {crop_id}: {name}")
                flash(f"Crop '{name}' updated successfully!", 'success')
                return redirect(url_for('admin_crops'))
            except Error as e:
                logger.error(f"Error updating crop ID {crop_id}: {e}")
                flash('An error occurred while updating the crop. Please try again.', 'error')
            finally:
                close_db_connection(conn2, cur2)

    return render_template('admin_crop_form.html', action='Edit', crop=crop, seasons=seasons,
                           admin_username=session.get('admin_username', 'Admin'))


@app.route('/admin/crops/delete/<int:crop_id>', methods=['POST'])
@login_required
def admin_crop_delete(crop_id):
    """Delete a crop from the database."""
    connection = get_db_connection()
    if connection:
        try:
            cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT crop_name FROM crops WHERE crop_id = %s", (crop_id,))
            row = cur.fetchone()
            if not row:
                flash('Crop not found.', 'error')
                return redirect(url_for('admin_crops'))
            name = row['crop_name']
            cur.execute("DELETE FROM crops WHERE crop_id = %s", (crop_id,))
            connection.commit()
            log_admin_activity(session.get('admin_id'), 'crop_delete',
                               f"Deleted crop ID {crop_id}: {name}")
            flash(f"Crop '{name}' deleted successfully.", 'success')
        except Error as e:
            logger.error(f"Error deleting crop ID {crop_id}: {e}")
            flash('An error occurred while deleting the crop. Please try again.', 'error')
        finally:
            close_db_connection(connection, cur)
    return redirect(url_for('admin_crops'))


@app.route('/admin/api/crops', methods=['GET'])
@login_required
def admin_api_crops():
    """JSON API: return all crops for AJAX use."""
    connection = get_db_connection()
    crops = []
    if connection:
        try:
            cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM crops ORDER BY crop_name")
            crops = cur.fetchall()
        except Error as e:
            logger.error(f"Error in admin_api_crops: {e}")
            return make_sanitized_error("DATABASE_ERROR", "Unable to load crops list.", 500)
        finally:
            close_db_connection(connection, cur)
    return jsonify({'crops': crops, 'total': len(crops)})



# ============================================================================
# DISEASE MANAGEMENT ROUTES  (Phase 3)
# ============================================================================

@app.route('/admin/diseases')
@login_required
def admin_diseases():
    """List diseases/symptoms from the database."""
    search = request.args.get('search', '').strip()
    connection = get_db_connection()
    diseases = []
    total = 0
    if connection:
        try:
            cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            base_sql = """
                SELECT  d.disease_id,
                        d.name,
                        d.scientific_name,
                        d.category,
                        d.severity,
                        d.description,
                        d.ai_label,
                        STRING_AGG(DISTINCT c.crop_name, ', ')
                            AS affected_crops,
                        COUNT(DISTINCT t.treatment_id) AS treatment_count
                FROM    diseases d
                LEFT JOIN crop_diseases cd ON cd.disease_id = d.disease_id
                LEFT JOIN crops         c  ON c.crop_id     = cd.crop_id
                LEFT JOIN treatments    t  ON t.disease_id  = d.disease_id
            """
            if search:
                cur.execute(
                    base_sql +
                    " WHERE d.name LIKE %s OR d.scientific_name LIKE %s OR d.category LIKE %s"
                    " GROUP BY d.disease_id ORDER BY d.name LIMIT 100",
                    (f"%{search}%", f"%{search}%", f"%{search}%")
                )
            else:
                cur.execute(base_sql + " GROUP BY d.disease_id ORDER BY d.name LIMIT 100")
            diseases = cur.fetchall()
            total = len(diseases)
        except Exception as e:
            logger.error(f"Database error in admin_diseases listing: {e}")
            flash('Unable to load diseases. Please try again.', 'error')
        finally:
            close_db_connection(connection, cur)

    return render_template('admin_diseases.html',
                           diseases=diseases,
                           total=total,
                           search=search,
                           admin_username=session.get('admin_username', 'Admin'))


# ── Admin: Diagnosis Feedback ─────────────────────────────────────────────────
@app.route('/admin/feedback')
@login_required
def admin_feedback():
    """View diagnosis feedback submitted by users from the results page."""
    connection = get_db_connection()
    feedback_rows = []
    total = 0
    correct_count = 0
    incorrect_count = 0

    if connection:
        try:
            cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT  f.id,
                        f.session_ref,
                        COALESCE(f.actual_disease, '') AS disease_name,
                        f.was_correct,
                        f.ip_address,
                        f.created_at
                FROM    diagnosis_feedback f
                ORDER   BY f.created_at DESC
                LIMIT   200
            """)
            feedback_rows = cur.fetchall()
            total         = len(feedback_rows)
            correct_count   = sum(1 for r in feedback_rows if r['was_correct'])
            incorrect_count = sum(1 for r in feedback_rows if not r['was_correct'])
        except Exception as e:
            logger.error(f"Database error in admin_feedback: {e}")
            flash('Unable to load feedback. Please try again.', 'error')
        finally:
            close_db_connection(connection, cur)

    return render_template('admin_feedback.html',
                           feedback_rows=feedback_rows,
                           total=total,
                           correct_count=correct_count,
                           incorrect_count=incorrect_count,
                           accuracy=round(correct_count / total * 100) if total else 0,
                           admin_username=session.get('admin_username', 'Admin'))



@app.route('/admin/users')
@login_required
def admin_users():
    """List all admin users."""
    connection = get_db_connection()
    users = []
    if connection:
        try:
            cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT id, username, email, role, is_active, last_login, created_at FROM admin_users ORDER BY created_at DESC")
            users = cur.fetchall()
        except Error as e:
            logger.error(f"Database error in admin_users listing: {e}")
            flash('Unable to load users. Please try again.', 'error')
        finally:
            close_db_connection(connection, cur)
    return render_template('admin_users.html',
                           users=users,
                           admin_username=session.get('admin_username', 'Admin'),
                           current_user_id=session.get('admin_id'))


@app.route('/admin/users/add', methods=['GET', 'POST'])
@login_required
def admin_user_add():
    """Add a new admin user."""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        role     = request.form.get('role', 'admin').strip()

        if not username or not email or not password:
            flash('Username, email and password are all required.', 'error')
            return render_template('admin_user_form.html', action='Add', user=None,
                                   admin_username=session.get('admin_username', 'Admin'))

        connection = get_db_connection()
        if connection:
            try:
                cur = connection.cursor()
                cur.execute("""
                    INSERT INTO admin_users (username, email, password_hash, role)
                    VALUES (%s, %s, %s, %s)
                """, (username, email, generate_password_hash(password), role))
                connection.commit()
                log_admin_activity(session.get('admin_id'), 'user_add',
                                   f"Added admin user: {username} ({role})")
                flash(f"User '{username}' added successfully!", 'success')
                return redirect(url_for('admin_users'))
            except Error as e:
                logger.error(f"Error adding admin user '{username}': {e}")
                flash('An error occurred while adding the user. Please try again.', 'error')
            finally:
                close_db_connection(connection, cur)

    return render_template('admin_user_form.html', action='Add', user=None,
                           admin_username=session.get('admin_username', 'Admin'))


@app.route('/admin/users/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
def admin_user_edit(user_id):
    """Edit an admin user's details (not password)."""
    connection = get_db_connection()
    user = None
    if connection:
        try:
            cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT id, username, email, role, is_active FROM admin_users WHERE id = %s", (user_id,))
            user = cur.fetchone()
        except Error as e:
            logger.error(f"Database error fetching user ID {user_id} for edit: {e}")
            flash('Unable to load user. Please try again.', 'error')
            return redirect(url_for('admin_users'))
        finally:
            close_db_connection(connection, cur)

    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_users'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email    = request.form.get('email', '').strip()
        role     = request.form.get('role', 'admin').strip()
        new_pass = request.form.get('new_password', '').strip()

        conn2 = get_db_connection()
        if conn2:
            try:
                cur2 = conn2.cursor()
                if new_pass:
                    cur2.execute(
                        "UPDATE admin_users SET username=%s, email=%s, role=%s, password_hash=%s WHERE id=%s",
                        (username, email, role, generate_password_hash(new_pass), user_id)
                    )
                else:
                    cur2.execute(
                        "UPDATE admin_users SET username=%s, email=%s, role=%s WHERE id=%s",
                        (username, email, role, user_id)
                    )
                conn2.commit()
                log_admin_activity(session.get('admin_id'), 'user_edit',
                                   f"Edited user ID {user_id}: {username}")
                flash(f"User '{username}' updated successfully!", 'success')
                return redirect(url_for('admin_users'))
            except Error as e:
                logger.error(f"Error updating user ID {user_id}: {e}")
                flash('An error occurred while updating the user. Please try again.', 'error')
            finally:
                close_db_connection(conn2, cur2)

    return render_template('admin_user_form.html', action='Edit', user=user,
                           admin_username=session.get('admin_username', 'Admin'))


@app.route('/admin/users/toggle/<int:user_id>', methods=['POST'])
@login_required
def admin_user_toggle(user_id):
    """Activate or deactivate an admin user."""
    if user_id == session.get('admin_id'):
        flash('You cannot deactivate yourself.', 'error')
        return redirect(url_for('admin_users'))

    connection = get_db_connection()
    if connection:
        try:
            cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT username, is_active FROM admin_users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if row:
                new_state = 1 if not row['is_active'] else 0
                cur.execute("UPDATE admin_users SET is_active = %s WHERE id = %s", (new_state, user_id))
                connection.commit()
                action_word = 'activated' if new_state else 'deactivated'
                log_admin_activity(session.get('admin_id'), 'user_toggle',
                                   f"User '{row['username']}' {action_word}")
                flash(f"User '{row['username']}' has been {action_word}.", 'success')
        except Error as e:
            logger.error(f"Error toggling user ID {user_id} active state: {e}")
            flash('An error occurred while updating the user. Please try again.', 'error')
        finally:
            close_db_connection(connection, cur)
    return redirect(url_for('admin_users'))


# ============================================================================
# ANALYTICS ROUTE  (Phase 5)
# ============================================================================

@app.route('/admin/analytics')
@login_required
def admin_analytics():
    """Analytics overview page with real DB stats."""
    stats = {}
    activity_by_type = {}
    recent_logins = []

    connection = get_db_connection()
    if connection:
        try:
            cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # Total crops
            cur.execute("SELECT COUNT(*) AS cnt FROM crops")
            stats['total_crops'] = cur.fetchone()['cnt']

            # Total admin users
            cur.execute("SELECT COUNT(*) AS cnt FROM admin_users WHERE is_active = 1")
            stats['active_admin_users'] = cur.fetchone()['cnt']

            # Activity log totals by type
            cur.execute("""
                SELECT action_type, COUNT(*) AS cnt
                FROM admin_activity_log
                GROUP BY action_type
                ORDER BY cnt DESC
            """)
            for row in cur.fetchall():
                activity_by_type[row['action_type']] = row['cnt']
            stats['total_actions'] = sum(activity_by_type.values())

            # Recent logins
            cur.execute("""
                SELECT l.description, l.created_at, l.ip_address,
                       COALESCE(u.username, 'System') AS actor
                FROM admin_activity_log l
                LEFT JOIN admin_users u ON u.id = l.admin_id
                WHERE l.action_type IN ('login', 'login_failed')
                ORDER BY l.created_at DESC LIMIT 15
            """)
            recent_logins = cur.fetchall()

        except Error as e:
            logger.error(f"Database error in admin_analytics: {e}")
            flash('Unable to load analytics. Please try again.', 'error')
        finally:
            close_db_connection(connection, cur)

    return render_template('admin_analytics.html',
                           stats=stats,
                           activity_by_type=activity_by_type,
                           recent_logins=recent_logins,
                           admin_username=session.get('admin_username', 'Admin'))


# ============================================================================
# SYSTEM SETTINGS ROUTES  (Phase 6)
# ============================================================================

@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
def admin_settings():
    """View and update system settings."""
    connection = get_db_connection()
    settings = {}

    if connection:
        try:
            cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT setting_key, setting_val, description FROM system_settings ORDER BY setting_key")
            for row in cur.fetchall():
                settings[row['setting_key']] = {'value': row['setting_val'], 'description': row['description']}
        except Error as e:
            logger.error(f"Database error loading settings: {e}")
            flash('Unable to load settings. Please try again.', 'error')
        finally:
            close_db_connection(connection, cur)

    if request.method == 'POST':
        conn2 = get_db_connection()
        if conn2:
            try:
                cur2 = conn2.cursor()
                for key in request.form:
                    if key.startswith('_'):
                        continue
                    value = request.form.get(key, '').strip()
                    cur2.execute("""
                        INSERT INTO system_settings (setting_key, setting_val, updated_by)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (setting_key)
                        DO UPDATE SET setting_val = EXCLUDED.setting_val, updated_by = EXCLUDED.updated_by
                    """, (key, value, session.get('admin_id')))
                conn2.commit()
                log_admin_activity(session.get('admin_id'), 'settings_update', 'Updated system settings')
                flash('Settings saved successfully!', 'success')
                return redirect(url_for('admin_settings'))
            except Error as e:
                logger.error(f"Database error saving settings: {e}")
                flash('An error occurred while saving settings. Please try again.', 'error')
            finally:
                close_db_connection(conn2, cur2)

    return render_template('admin_settings.html',
                           settings=settings,
                           admin_username=session.get('admin_username', 'Admin'))


# ── pH fuzzy label helper ─────────────────────────────────────────────────────
def get_ph_fuzzy_label(ph: float) -> dict:
    """
    Returns a human-readable fuzzy label and CSS class
    for a given pH value.
    """
    if ph < 4.0:
        return {
            'label':       'Extremely Acidic',
            'description': 'Very few crops tolerate this pH. Heavy liming required.',
            'css_class':   'ph-extremely-acidic',
            'color':       '#c0392b'
        }
    elif ph < 4.5:
        return {
            'label':       'Strongly Acidic',
            'description': 'Severely limits crop choice. Significant liming needed.',
            'css_class':   'ph-strongly-acidic',
            'color':       '#e74c3c'
        }
    elif ph < 5.0:
        return {
            'label':       'Moderately Acidic',
            'description': 'Limits some crops. Liming recommended.',
            'css_class':   'ph-moderately-acidic',
            'color':       '#e67e22'
        }
    elif ph < 5.5:
        return {
            'label':       'Mildly Acidic',
            'description': 'Suitable for acid-tolerant crops like tea. '
                           'Liming improves yield for most cereals.',
            'css_class':   'ph-mildly-acidic',
            'color':       '#f39c12'
        }
    elif ph < 6.0:
        return {
            'label':       'Slightly Acidic',
            'description': 'Good for a wide range of crops. '
                           'Most cereals and legumes perform well.',
            'css_class':   'ph-slightly-acidic',
            'color':       '#f1c40f'
        }
    elif ph < 6.5:
        return {
            'label':       'Near Neutral',
            'description': 'Ideal for most crops. Broad crop selection possible.',
            'css_class':   'ph-near-neutral',
            'color':       '#2ecc71'
        }
    elif ph < 7.0:
        return {
            'label':       'Neutral',
            'description': 'Optimal for the widest range of crops.',
            'css_class':   'ph-neutral',
            'color':       '#27ae60'
        }
    elif ph < 7.5:
        return {
            'label':       'Slightly Alkaline',
            'description': 'Still suitable for many crops. '
                           'Monitor micronutrient availability.',
            'css_class':   'ph-slightly-alkaline',
            'color':       '#3498db'
        }
    elif ph < 8.0:
        return {
            'label':       'Moderately Alkaline',
            'description': 'Limits crop choice. Drought-tolerant crops preferred.',
            'css_class':   'ph-moderately-alkaline',
            'color':       '#2980b9'
        }
    else:
        return {
            'label':       'Strongly Alkaline',
            'description': 'Very limited crop choice. Soil amendment needed.',
            'css_class':   'ph-strongly-alkaline',
            'color':       '#1a5276'
        }


# ── County soil profile endpoint ─────────────────────────────────────────────
@app.route('/api/county-soil-profile', methods=['GET'])
def api_county_soil_profile():
    """
    Returns soil profile and pH fuzzy label for a given county.
    Accepts either county_id or county_name as query parameter.
    """
    county_id   = request.args.get('county_id')
    county_name = request.args.get('county_name', '').strip()

    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': 'Database unavailable'}), 500

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if county_id:
            cur.execute("""
                SELECT csp.*, kc.county_name
                FROM   county_soil_profiles csp
                JOIN   kenya_counties kc 
                    ON kc.county_id = csp.county_id
                WHERE  csp.county_id = %s
            """, (county_id,))
        else:
            cur.execute("""
                SELECT csp.*, kc.county_name
                FROM   county_soil_profiles csp
                JOIN   kenya_counties kc 
                    ON kc.county_id = csp.county_id
                WHERE  kc.county_name = %s
            """, (county_name,))

        profile = cur.fetchone()

        if not profile:
            return jsonify({
                'success': False,
                'error':   'No soil profile found for this county'
            }), 404

        typical_ph  = float(profile['typical_ph'])
        ph_min      = float(profile['ph_min'])
        ph_max      = float(profile['ph_max'])
        fuzzy       = get_ph_fuzzy_label(typical_ph)
        fuzzy_min   = get_ph_fuzzy_label(ph_min)
        fuzzy_max   = get_ph_fuzzy_label(ph_max)

        return jsonify({
            'success':          True,
            'county_id':        profile['county_id'],
            'county_name':      profile['county_name'],
            'typical_ph':       typical_ph,
            'ph_min':           ph_min,
            'ph_max':           ph_max,
            'ph_range_label':   f"{ph_min} to {ph_max}",
            'soil_type':        profile['soil_type'],
            'soil_description': profile['soil_description'],
            'liming_advice':    profile['liming_advice'],
            'source_note':      profile['source_note'],
            'latitude':         float(profile['latitude'])  if profile['latitude']  else None,
            'longitude':        float(profile['longitude']) if profile['longitude'] else None,
            'fuzzy': {
                'typical': fuzzy,
                'min':     fuzzy_min,
                'max':     fuzzy_max,
                'summary': (
                    f"{fuzzy['label']} "
                    f"(range: {fuzzy_min['label']} to {fuzzy_max['label']})"
                )
            }
        })

    except Exception as e:
        logger.error(f"[SOIL] Profile fetch error: {e}")
        return make_sanitized_error("SOIL_FETCH_ERROR", "Unable to load soil profile. Please try again.", 500)
    finally:
        close_db_connection(conn, cur)

@app.route('/api/ecological-conditions', methods=['GET'])
def api_ecological_conditions():
    """
    Server-side weather fetch from Open-Meteo.
    Receives lat/lon or county_id and returns
    categorised rainfall and temperature.
    """
    try:
        county_id = request.args.get('county_id')
        lat_val = request.args.get('lat')
        lon_val = request.args.get('lon')

        lat = float(lat_val) if lat_val is not None and lat_val != '' else None
        lon = float(lon_val) if lon_val is not None and lon_val != '' else None

        if (lat is None or lon is None or (lat == 0 and lon == 0)) and county_id:
            conn = get_db_connection()
            if conn:
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT latitude, longitude FROM county_soil_profiles WHERE county_id=%s", (county_id,))
                    row = cur.fetchone()
                    if row and row[0] is not None and row[1] is not None:
                        lat, lon = float(row[0]), float(row[1])
                finally:
                    close_db_connection(conn, cur)

        lat = lat or 0.0
        lon = lon or 0.0

        weather_data = fetch_ecological_conditions_data(lat, lon)
        return jsonify({
            'success': True,
            **weather_data
        })
    except Exception as e:
        logger.error(f"[WEATHER] Conditions API error: {e}")
        return make_sanitized_error("WEATHER_FETCH_ERROR", "Unable to fetch local weather conditions.", 500)



def fetch_ecological_conditions_data(lat: float, lon: float) -> dict:
    """
    Internal helper to fetch weather from Open-Meteo.
    Tries the live Forecast API first; falls back to the
    Historical Archive API if the forecast endpoint is down.
    """
    from datetime import date, timedelta

    DAILY_VARS = 'precipitation_sum,temperature_2m_max,temperature_2m_min'

    def _parse_and_categorise(daily: dict, source: str) -> dict:
        """Shared logic: turn raw daily arrays into labelled categories."""
        precip = daily.get('precipitation_sum', [])
        t_max  = daily.get('temperature_2m_max', [])
        t_min  = daily.get('temperature_2m_min', [])

        rainfall_30d = round(sum(p for p in precip if p is not None), 1)
        avg_temp = (
            round((sum(t_max) / len(t_max) + sum(t_min) / len(t_min)) / 2, 1)
            if t_max and t_min else 22.0
        )

        if rainfall_30d < 50:
            r_cat, r_label = 'Low',    'Low (below 50mm in last 30 days)'
        elif rainfall_30d < 150:
            r_cat, r_label = 'Medium', 'Medium (50 to 150mm in last 30 days)'
        else:
            r_cat, r_label = 'High',   'High (above 150mm in last 30 days)'

        if avg_temp < 20:
            t_cat, t_label = 'Low',    f'Low (avg {avg_temp}°C, cool highland)'
        elif avg_temp < 28:
            t_cat, t_label = 'Medium', f'Medium (avg {avg_temp}°C, moderate)'
        else:
            t_cat, t_label = 'High',   f'High (avg {avg_temp}°C, warm lowland)'

        return {
            'rainfall_30d_mm':   rainfall_30d,
            'rainfall_category': r_cat,
            'rainfall_label':    r_label,
            'avg_temp_c':        avg_temp,
            'temp_category':     t_cat,
            'temp_label':        t_label,
            'source':            source,
        }

    # ── Primary: live Forecast API ────────────────────────────────────────────
    try:
        print(f"[WEATHER] Trying forecast API for ({lat}, {lon})")
        resp = requests.get(
            'https://api.open-meteo.com/v1/forecast',
            params={
                'latitude':      lat,
                'longitude':     lon,
                'daily':         DAILY_VARS,
                'timezone':      'Africa/Nairobi',
                'past_days':     30,
                'forecast_days': 1,
            },
            timeout=10
        )
        if not resp.ok:
            raise ValueError(f"Forecast API returned HTTP {resp.status_code}")
        result = _parse_and_categorise(resp.json().get('daily', {}), 'Open-Meteo Forecast')
        print(f"[WEATHER] Forecast OK – rain={result['rainfall_category']}, temp={result['temp_category']}")
        return result

    except Exception as e:
        print(f"[WEATHER] Forecast API failed ({e}), trying archive fallback...")

    # ── Fallback: Historical Archive API ─────────────────────────────────────
    try:
        end_date   = date.today() - timedelta(days=1)   # archive lags 1 day
        start_date = end_date - timedelta(days=29)
        print(f"[WEATHER] Archive API: {start_date} -> {end_date}")
        resp = requests.get(
            'https://archive-api.open-meteo.com/v1/archive',
            params={
                'latitude':   lat,
                'longitude':  lon,
                'daily':      DAILY_VARS,
                'timezone':   'Africa/Nairobi',
                'start_date': start_date.isoformat(),
                'end_date':   end_date.isoformat(),
            },
            timeout=15
        )
        if not resp.ok:
            raise ValueError(f"Archive API returned HTTP {resp.status_code}")
        result = _parse_and_categorise(resp.json().get('daily', {}), 'Open-Meteo Archive')
        print(f"[WEATHER] Archive OK – rain={result['rainfall_category']}, temp={result['temp_category']}")
        return result

    except Exception as e:
        print(f"[WEATHER] Archive API also failed: {e}")

    # ── Both failed ───────────────────────────────────────────────────────────
    return {
        'rainfall_30d_mm':   0,
        'rainfall_category': 'Medium',
        'rainfall_label':    'Unknown (fallback)',
        'avg_temp_c':        22.0,
        'temp_category':     'Medium',
        'temp_label':        'Unknown (fallback)',
        'source':            'Error-Fallback',
    }


@app.route('/advisory/results')
def advisory_results():
    """Renders the results page for crop recommendations."""
    return render_template('advisory_results.html')


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/api/counties', methods=['GET'])
def api_get_counties():
    """API endpoint to get all counties"""
    counties = get_all_counties()
    return jsonify({
        'success': True,
        'counties': counties,
        'count': len(counties)
    })


@app.route('/api/sub-counties', methods=['GET'])
def api_get_sub_counties():
    """API endpoint to get sub-counties for a specific county name or county_id"""
    county = request.args.get('county', '').strip()
    county_id = request.args.get('county_id', '').strip()
    
    if not county and not county_id:
        return jsonify({
            'success': False,
            'error': 'County or county_id parameter is required'
        }), 400
    
    if county_id and not county:
        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute("SELECT county_name FROM kenya_counties WHERE county_id = %s", (county_id,))
                row = cur.fetchone()
                if row:
                    county = row[0]
            finally:
                close_db_connection(conn, cur)
    
    sub_counties = get_sub_counties_by_county(county) if county else []
    return jsonify({
        'success': True,
        'sub_counties': sub_counties,
        'county': county,
        'count': len(sub_counties)
    })


@app.route('/api/reverse-geocode', methods=['GET'])
def api_reverse_geocode():
    """
    Server-side reverse geocoding using Nominatim.
    Receives lat/lon and returns matched county and sub-county.
    Runs server-side to avoid browser CORS restrictions.
    """
    try:
        lat = float(request.args.get('lat'))
        lon = float(request.args.get('lon'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid coordinates'}), 400

    try:
        response = requests.get(
            'https://nominatim.openstreetmap.org/reverse',
            params={
                'lat':    lat,
                'lon':    lon,
                'format': 'json',
                'addressdetails': 1
            },
            headers={
                'User-Agent': 'PlantWise/1.0 (agricultural advisory system Kenya)'
            },
            timeout=10
        )

        data    = response.json()
        address = data.get('address', {})

        print(f"[GEOCODE] Raw address from Nominatim: {address}")

        # Nominatim returns Kenyan counties in different fields
        # depending on the specific location
        raw_county = (
            address.get('county')         or
            address.get('state_district') or
            address.get('city')           or
            address.get('region')         or
            ''
        )

        raw_sub_county = (
            address.get('suburb')        or
            address.get('town')          or
            address.get('village')       or
            address.get('municipality')  or
            address.get('city_district') or
            address.get('district')      or
            address.get('neighbourhood') or
            address.get('quarter')       or
            address.get('hamlet')        or
            address.get('road')          or
            address.get('isolated_dwelling') or
            ''
        )

        # Clean up county name
        county = raw_county\
            .replace(' City County', '')\
            .replace(' County', '')\
            .strip()

        sub_county = raw_sub_county.strip()

        # Ensure sub_county is not identical to county (per user requirement)
        if sub_county.lower() == county.lower():
            # Try a deeper fallback if they match
            sub_county = address.get('road') or address.get('suburb') or ''
            if sub_county.lower() == county.lower():
                sub_county = ''

        print(f"[GEOCODE] Parsed county: '{county}', "
              f"sub-county: '{sub_county}'")

        # Try to match against our kenya_counties table
        matched_county    = None
        matched_county_id = None

        if county:
            conn = get_db_connection()
            if conn:
                try:
                    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

                    # Try exact match first
                    cur.execute(
                        """SELECT county_id, county_name 
                           FROM kenya_counties 
                           WHERE county_name = %s""",
                        (county,)
                    )
                    row = cur.fetchone()

                    # Try partial match if exact fails
                    if not row:
                        cur.execute(
                            """SELECT county_id, county_name 
                               FROM kenya_counties 
                               WHERE county_name LIKE %s
                               LIMIT 1""",
                            (f'%{county}%',)
                        )
                        row = cur.fetchone()

                    if row:
                        matched_county    = row['county_name']
                        matched_county_id = row['county_id']
                        print(f"[GEOCODE] Matched to DB county: "
                              f"'{matched_county}' (id={matched_county_id})")
                    else:
                        print(f"[GEOCODE] No DB match for county: '{county}'")

                finally:
                    close_db_connection(conn, cur)

        return jsonify({
            'success':          bool(matched_county),
            'county':           matched_county or county,
            'county_id':        matched_county_id,
            'sub_county':       sub_county,
            'raw_county':       raw_county,
            'raw_sub_county':   raw_sub_county,
            'display_name':     data.get('display_name', ''),
        })

    except Exception as e:
        logger.error(f"[GEOCODE] Reverse geocode error: {e}")
        return make_sanitized_error("GEOCODE_ERROR", "Unable to locate coordinates. Please select your county manually.", 500)


@app.route('/api/recommendations', methods=['POST'])
@csrf.exempt
def api_get_recommendations():
    data          = request.get_json()
    county_name   = data.get('county',        '').strip()
    sub_county    = data.get('sub_county',    '').strip()
    soil_ph_raw   = data.get('soil_ph',       '')
    soil_ph_str   = str(soil_ph_raw).strip()
    ph_confidence = data.get('ph_confidence', 'county_default')
    rainfall      = str(data.get('rainfall',      '')).strip()
    temperature   = str(data.get('temperature',   '')).strip()
    latitude      = data.get('latitude')
    longitude     = data.get('longitude')

    if not county_name:
        return jsonify({'success': False, 'error': 'County is required'}), 400

    try:
        soil_ph = float(soil_ph_str)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid soil pH value'}), 400

    if not (3.0 <= soil_ph <= 10.0):
        return jsonify({'success': False, 'error': 'Soil pH must be between 3.0 and 10.0'}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': 'Database unavailable'}), 500

    county_id    = None
    soil_profile = None

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Resolve county_id from name
        cur.execute(
            "SELECT county_id FROM kenya_counties WHERE county_name = %s",
            (county_name,)
        )
        county_row = cur.fetchone()
        if not county_row:
            return jsonify({
                'success': False,
                'error':   f'County "{county_name}" not found in database'
            }), 400

        county_id = county_row['county_id']

        # Fetch soil profile
        cur.execute(
            "SELECT * FROM county_soil_profiles WHERE county_id = %s",
            (county_id,)
        )
        soil_profile = cur.fetchone()

    except Exception as e:
        logger.error(f"[ADVISORY] County lookup error: {e}")
        return make_sanitized_error("DATABASE_ERROR", "Unable to look up county details.", 500)
    finally:
        close_db_connection(conn, cur)

    # Server-side weather confirmation
    ecological      = None
    conditions_source = 'manual'

    if latitude and longitude:
        try:
            ecological = fetch_ecological_conditions_data(float(latitude), float(longitude))
            if ecological:
                rainfall          = ecological['rainfall_category']
                temperature       = ecological['temp_category']
                conditions_source = 'auto'
                print(f"[ADVISORY] Weather confirmed via direct helper: "
                      f"Rain={rainfall}, Temp={temperature}")
        except Exception as e:
            print(f"[ADVISORY] Direct weather fetch failed: {e}")

    if not all([rainfall, temperature]):
        return jsonify({
            'success': False,
            'error':   'Rainfall and temperature are required'
        }), 400

    # Get recommendations
    recommended_crops = get_crop_recommendations(
        county_id     = county_id,
        county_name   = county_name,
        soil_ph       = soil_ph,
        rainfall      = rainfall,
        temperature   = temperature,
        ph_confidence = ph_confidence
    )

    if not recommended_crops:
        recommended_crops = get_alternative_recommendations(
            county_name, soil_ph, rainfall, temperature
        )

    # Build pH fuzzy info for the response
    ph_fuzzy = get_ph_fuzzy_label(soil_ph)

    # Format soil profile for response
    soil_info = None
    if soil_profile:
        soil_info = {
            'typical_ph':       float(soil_profile['typical_ph']),
            'ph_min':           float(soil_profile['ph_min']),
            'ph_max':           float(soil_profile['ph_max']),
            'soil_type':        soil_profile['soil_type'],
            'soil_description': soil_profile['soil_description'],
            'liming_advice':    soil_profile['liming_advice'],
        }

    return jsonify({
        'success':            True,
        'message':            f'Found {len(recommended_crops)} suitable crops',
        'recommendations':    recommended_crops,
        'count':              len(recommended_crops),
        'ecological_data':    ecological,
        'conditions_source':  conditions_source,
        'soil_profile':       soil_info,
        'ph_used':            soil_ph,
        'ph_confidence':      ph_confidence,
        'ph_fuzzy':           ph_fuzzy,
    })


@app.route('/api/diagnose', methods=['POST'])
@csrf.exempt
def api_diagnose():
    """
    API endpoint for disease diagnosis
    Uses Crop Health API for image-based diagnosis, knowledge base for symptom-based
    """
    try:
        # Get form data
        crop = request.form.get('crop')
        method = request.form.get('method')
        plant_part = request.form.get('plant_part', '')
        symptoms = request.form.get('symptoms', '[]')
        
        print(f"\n[DIAGNOSIS] Crop: {crop}, Method: {method}, Plant Part: {plant_part}")
        
        results = []
        
        # Check if images were uploaded
        uploaded_images = []
        for key in request.files:
            if key.startswith('image_'):
                uploaded_images.append(request.files[key])
        
        # ── M4: Validate uploaded file types ─────────────────────────────
        ALLOWED_IMAGE_SIGNATURES = {
            b'\xff\xd8': 'jpeg',        # JPEG
            b'\x89PNG': 'png',          # PNG
            b'RIFF': 'webp',            # WebP (also needs bytes 8-12 == WEBP)
            b'GIF8': 'gif',             # GIF
        }
        for img_file in uploaded_images:
            header = img_file.read(12)
            img_file.seek(0)
            valid = any(header.startswith(sig) for sig in ALLOWED_IMAGE_SIGNATURES)
            # Special case for WebP (RIFF....WEBP)
            if header.startswith(b'RIFF') and header[8:12] != b'WEBP':
                valid = False
            if not valid:
                return jsonify({'success': False, 'error': 'Invalid file type. Only JPEG, PNG, WebP, and GIF images are accepted.'}), 400

        # ── Route to correct diagnosis engine ────────────────────────────
        if uploaded_images and method == 'hybrid':
            
            # Fetch KB diseases first — these inform the AI what to look for
            kb_results = get_knowledge_base_results(crop)
            print(f"[DIAGNOSIS] Fetched {len(kb_results)} KB diseases for {crop}")
            print(f"[DIAGNOSIS] Sending KB context to AI for guided visual analysis...")

            api_result = call_plant_id_api(
                uploaded_images,
                crop_name=crop,
                kb_diseases=kb_results  # KB informs the AI upfront
            )

            if api_result.get('success'):
                ai_results = api_result.get('diseases', [])

                if api_result.get('is_healthy'):
                    # AI confirmed healthy plant — use its result directly and
                    # suppress all KB disease suggestions so no false positives appear.
                    results = ai_results
                    print(f"[DIAGNOSIS] AI reports healthy plant — KB disease merge suppressed")
                else:
                    # Hybrid merge: AI visual confidence + KB treatment data
                    results = merge_hybrid_results(ai_results, kb_results)
                    print(f"[DIAGNOSIS] Hybrid merge complete: {len(results)} results")
            else:
                error_msg = api_result.get('error', 'Unknown error')
                print(f"[DIAGNOSIS] AI Vision API unavailable ({error_msg}) — falling back to local Knowledge Base")
                results = kb_results
                for r in results:
                    r['source'] = 'Knowledge Base (Offline Mode)'
                    r['source_note'] = 'KB-confirmed'
                api_result = {'success': False, 'fallback': True, 'error': error_msg}
        else:
            # No images uploaded — fall back to knowledge base
            print(f"[DIAGNOSIS] Using knowledge base for {crop}")
            results    = get_knowledge_base_results(crop)
            api_result = {}
        
        # Limit results strictly to top 3 max
        results = results[:3]
        
        # Save image to static folder instead of bloating the session cookie
        image_url = None
        if uploaded_images and method == 'hybrid':
            try:
                uploads_dir = os.path.join(app.static_folder, 'uploads')
                os.makedirs(uploads_dir, exist_ok=True)
                img = uploaded_images[0]
                img.seek(0)
                
                # Sanitize image: strip EXIF GPS/device metadata, normalize orientation, optimize
                pil_img = Image.open(img)
                pil_img = ImageOps.exif_transpose(pil_img)
                if pil_img.mode in ('RGBA', 'P'):
                    pil_img = pil_img.convert('RGB')
                
                # Cap maximum dimension to 1280px for mobile performance
                max_dim = 1280
                if max(pil_img.size) > max_dim:
                    pil_img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                
                filename = f"diag_{uuid.uuid4().hex[:8]}.jpg"
                filepath = os.path.join(uploads_dir, filename)
                pil_img.save(filepath, format='JPEG', quality=85, optimize=True)
                image_url = f"/static/uploads/{filename}"
            except Exception as e:
                logger.warning(f"Could not sanitize/save diagnosis image: {e}")
        
        _session_ref = str(uuid.uuid4())[:16]

        # ── Log diagnosis session and store results payload in DB ─────────────
        try:
            _conn = get_db_connection()
            if _conn:
                _cur = _conn.cursor()
                _method_map = {
                    'hybrid':        'hybrid',
                    'symptom':       'knowledge_base',
                    'knowledge':     'knowledge_base',
                    'knowledge_base': 'knowledge_base',
                }
                _db_method   = _method_map.get(str(method).lower(), 'knowledge_base')
                _top_disease = results[0].get('name', '') if results else ''
                _payload_json = json.dumps({
                    'crop': crop,
                    'method': method,
                    'plant_part': plant_part,
                    'diseases': results,
                    'uploaded_image_url': image_url,
                    'session_ref': _session_ref
                })
                _cur.execute("""
                    INSERT INTO diagnosis_sessions
                        (session_ref, crop_name, method, result_count, top_disease, ip_address, results_data)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (_session_ref, crop, _db_method, len(results), _top_disease, request.remote_addr, _payload_json))
                _conn.commit()
                close_db_connection(_conn, _cur)
                logger.info(f"[DIAGNOSIS] Session logged: ref={_session_ref}, method={_db_method}, results={len(results)}")
        except Exception as _e:
            logger.error(f"[DIAGNOSIS] Could not log session: {_e}")

        session['diagnosis_session_ref'] = _session_ref
        session['diagnosis_results'] = {
            'crop': crop,
            'method': method,
            'plant_part': plant_part,
            'uploaded_image_url': image_url,
            'session_ref': _session_ref
        }

        return jsonify({
            'success': True,
            'ok': True,
            'message': 'Diagnosis complete',
            'session_ref': _session_ref,
            'results': results
        })

    except Exception as e:
        logger.error(f"Error in diagnose API: {e}", exc_info=True)
        return make_sanitized_error("DIAGNOSIS_FAILED", "We couldn't complete that right now. Please try again.", 500)


def get_knowledge_base_results(crop: str) -> List[Dict]:
    """
    Fetch disease diagnoses for `crop` from the MySQL knowledge base.
    Returns a list of dicts compatible with the AI results structure.
    Falls back to an empty list if the DB is unavailable.
    """
    conn = get_db_connection()
    if not conn:
        return []

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get all diseases linked to this crop, ordered by KB confidence
        cur.execute("""
            SELECT  d.disease_id,
                    d.name,
                    d.scientific_name,
                    d.category,
                    d.description,
                    d.severity,
                    d.image_url,
                    d.ai_label,
                    cd.base_confidence
            FROM    crop_diseases  cd
            JOIN    diseases       d  ON d.disease_id = cd.disease_id
            JOIN    crops          c  ON c.crop_id    = cd.crop_id
            WHERE   c.crop_name = %s
            ORDER   BY cd.base_confidence DESC
        """, (crop,))
        diseases = cur.fetchall()

        if not diseases:
            # Crop not in KB — return generic advisory
            return [{
                'name':        'General Plant Disease',
                'confidence':  60,
                'source':      'Knowledge Base',
                'description': 'Unable to identify specifically. Common issues may apply.',
                'treatment':   'Consult a local agricultural extension officer.',
                'treatment_groups': {},
                'severity':    'medium',
                'category':    'unknown',
                'ai_label':    '',
            }]

        results = []
        for d in diseases:
            # Fetch treatments for this disease grouped by type
            cur.execute("""
                SELECT type, title, description, dosage, timing, products
                FROM   treatments
                WHERE  disease_id = %s
                ORDER  BY priority ASC
            """, (d['disease_id'],))
            raw_tx = cur.fetchall()

            tx_groups: Dict[str, list] = {}
            for t in raw_tx:
                tx_groups.setdefault(t['type'], []).append({
                    'title':       t['title'],
                    'description': t['description'],
                    'dosage':      t['dosage'] or '',
                    'timing':      t['timing'] or '',
                    'products':    t['products'] or '',
                })

            # Build flat treatment summary (backward-compat with current template)
            flat_parts = []
            for ttype in ('chemical', 'biological', 'cultural', 'prevention'):
                for t in tx_groups.get(ttype, [])[:1]:
                    flat_parts.append(t['title'])
            treatment_summary = '; '.join(flat_parts) or 'Consult agricultural expert'

            results.append({
                'name':             d['name'],
                'scientific_name':  d['scientific_name'] or '',
                'confidence':       int(d['base_confidence']),
                'source':           'Knowledge Base',
                'description':      d['description'],
                'treatment':        treatment_summary,
                'treatment_groups': tx_groups,
                'severity':         d['severity'],
                'category':         d['category'],
                'ai_label':         d['ai_label'] or '',
                'image_url':        d['image_url'] or '',
            })

        return results

    except Exception as e:
        print(f"[KB] DB query failed: {e}")
        return []
    finally:
        close_db_connection(conn, cur)


def merge_hybrid_results(ai_results: List[Dict], kb_results: List[Dict]) -> List[Dict]:
    """
    Merge AI image analysis results with knowledge base results.
    - KB-confirmed AI results: 70% AI weight, 30% KB weight
    - AI-only fallback results: preserved at full AI confidence
      with a note that KB treatment data is unavailable
    - KB-only diseases not seen by AI: appended with 40% confidence penalty
    """
    merged: Dict[str, Dict] = {}

    # Index KB records for fast lookup
    kb_by_label: Dict[str, Dict] = {}
    kb_by_name:  Dict[str, Dict] = {}
    for kb in kb_results:
        if kb.get('ai_label'):
            kb_by_label[kb['ai_label'].lower()] = kb
        kb_by_name[kb['name'].lower()] = kb

    for ai in ai_results:
        ai_name  = ai.get('name', '').lower()
        ai_label = ai.get('ai_label', '').lower()
        in_kb    = ai.get('in_kb_list', False)

        kb_match = kb_by_label.get(ai_label) or kb_by_name.get(ai_name)

        if kb_match:
            # Standard hybrid blend
            blended_conf = int(ai['confidence'] * 0.70 + kb_match['confidence'] * 0.30)
            merged[ai_name] = {
                **ai,
                'confidence':       min(blended_conf, 99),
                'source':           'AI + Knowledge Base',
                'source_note':      'Both',
                'treatment':        kb_match.get('treatment', ai.get('treatment', '')),
                'treatment_groups': kb_match.get('treatment_groups', {}),
                'severity':         kb_match.get('severity', ai.get('severity', 'medium')),
                'scientific_name':  kb_match.get('scientific_name', ai.get('scientific_name', '')),
            }

        elif not in_kb:
            # AI-identified fallback disease not in KB
            # Preserve at full AI confidence but flag clearly
            merged[ai_name] = {
                **ai,
                'source':      'AI Vision (Kenya Context)',
                'source_note': 'AI-identified',
                # Do not penalise confidence, the AI found this independently
                # Treatment already has the fallback note from call_plant_id_api
            }
            print(f"[HYBRID] Fallback disease preserved: {ai.get('name')} "
                  f"(confidence: {ai['confidence']}%) - not in KB")

        else:
            # in_kb was True but fuzzy match failed
            # Preserve AI result without KB enrichment
            merged[ai_name] = {
                **ai,
                'source':      'AI Vision',
                'source_note': 'KB-confirmed',
            }

    # Append KB-only diseases the AI did not detect
    merged_keys = set(merged.keys())
    for kb in kb_results:
        key = kb['name'].lower()
        if key not in merged_keys:
            merged[key] = {
                **kb,
                'confidence':  int(kb['confidence'] * 0.80),
                'source':      'Knowledge Base',
                'source_note': 'KB-confirmed',
            }

    return sorted(merged.values(), key=lambda x: x['confidence'], reverse=True)


# ============================================================================
# SANITIZED ERROR CONTRACT & HANDLERS (STRICT ZERO-LEAK)
# ============================================================================

def make_sanitized_error(code: str, message: str, status_code: int = 400, ref_code: str = None):
    """Sanitized user-facing error contract. Never leaks stack traces or SQL."""
    is_api = request.path.startswith('/api/') or request.is_json or request.headers.get('Accept') == 'application/json'
    if is_api:
        body = {
            'ok': False,
            'success': False,
            'code': code,
            'error': message,
            'message': message,
        }
        if ref_code:
            body['ref'] = ref_code
        return jsonify(body), status_code
    flash(message, 'error' if status_code >= 400 else 'info')
    return render_template('error.html', code=code, message=message, status_code=status_code, ref_code=ref_code), status_code


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    logger.warning(f"CSRF validation failed for {request.path}: {e.description}")
    return make_sanitized_error(
        code="CSRF_ERROR",
        message="Your session security token expired. Please refresh the page and try again.",
        status_code=400
    )


@app.errorhandler(400)
def handle_bad_request(e):
    return make_sanitized_error(code="BAD_REQUEST", message="Invalid request. Please check your inputs and try again.", status_code=400)


@app.errorhandler(403)
def handle_forbidden(e):
    return make_sanitized_error(code="FORBIDDEN", message="You don't have access to that.", status_code=403)


@app.errorhandler(404)
def handle_not_found(e):
    return make_sanitized_error(code="NOT_FOUND", message="We couldn't find what you were looking for.", status_code=404)


@app.errorhandler(405)
def handle_method_not_allowed(e):
    return make_sanitized_error(code="METHOD_NOT_ALLOWED", message="Action not supported.", status_code=405)


@app.errorhandler(429)
def handle_too_many_requests(e):
    return make_sanitized_error(code="RATE_LIMITED", message="Too many attempts. Please wait a moment and try again.", status_code=429)


@app.errorhandler(500)
def handle_internal_error(e):
    import secrets
    ref = secrets.token_hex(3).upper()
    logger.error(f"[SERVER_ERROR Ref:{ref}] {e}", exc_info=True)
    return make_sanitized_error(code="SERVER_ERROR", message="Something went wrong. Please try again.", status_code=500, ref_code=ref)


@app.errorhandler(Exception)
def handle_unhandled_exception(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    import secrets
    ref = secrets.token_hex(3).upper()
    logger.error(f"[UNHANDLED Ref:{ref}] {e}", exc_info=True)
    return make_sanitized_error(code="SERVER_ERROR", message="Something went wrong. Please try again.", status_code=500, ref_code=ref)


# ============================================================================
# DATABASE TESTING UTILITY
# ============================================================================

def test_database_connection():
    """Test database connection and display table information"""
    print("\n" + "="*70)
    print("PlantWise Database Connection Test")
    print("="*70)
    
    connection = get_db_connection()
    if not connection:
        print("[X] Failed to connect to database")
        print("  Check your .env file configuration")
        return False
    
    try:
        cursor = connection.cursor()
        
        # Test kenya_counties
        cursor.execute("SELECT COUNT(*) FROM kenya_counties")
        county_count = cursor.fetchone()[0]
        print(f"[OK] kenya_counties table: {county_count} counties")
        
        # Test crops
        cursor.execute("SELECT COUNT(*) FROM crops")
        crop_count = cursor.fetchone()[0]
        print(f"[OK] crops table: {crop_count} crops")
        
        # Test crop_suitability
        cursor.execute("SELECT COUNT(*) FROM crop_suitability")
        suitability_count = cursor.fetchone()[0]
        print(f"[OK] crop_suitability table: {suitability_count} suitability records")
        
        # Sample data
        cursor.execute("SELECT county_name FROM kenya_counties LIMIT 5")
        sample_counties = [row[0] for row in cursor.fetchall()]
        print(f"\nSample counties: {', '.join(sample_counties)}")
        
        cursor.execute("SELECT crop_name FROM crops LIMIT 5")
        sample_crops = [row[0] for row in cursor.fetchall()]
        print(f"Sample crops: {', '.join(sample_crops)}")
        
        print("="*70)
        print("[OK] Database connection successful!")
        print("="*70 + "\n")
        return True
        
    except Error as e:
        print(f"[X] Error testing database: {e}")
        print("="*70 + "\n")
        return False
    finally:
        close_db_connection(connection, cursor)


# ============================================================================
# DIAGNOSIS FEEDBACK API
# ============================================================================

@app.route('/api/diagnosis/feedback', methods=['POST'])
@csrf.exempt
def api_diagnosis_feedback():
    """
    Receive a thumbs-up / thumbs-down from the results page.
    Stores into `diagnosis_feedback` table (silently ignores if table missing).
    """
    data = request.get_json(silent=True) or {}
    disease_name = data.get('disease_name', '')
    was_correct  = bool(data.get('was_correct', False))
    session_ref  = data.get('session_ref', '')

    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            # PG schema uses actual_disease (VARCHAR) and was_correct (SMALLINT)
            cur.execute("""
                INSERT INTO diagnosis_feedback
                    (actual_disease, was_correct, session_ref, ip_address)
                VALUES (%s, %s, %s, %s)
            """, (disease_name, 1 if was_correct else 0, session_ref or None, request.remote_addr))
            conn.commit()
        except Exception as e:
            print(f"[FEEDBACK] Could not save feedback: {e}")
        finally:
            close_db_connection(conn, cur)

    return jsonify({'success': True})


# ============================================================================
# ADMIN DASHBOARD API ENDPOINTS
# ============================================================================

@app.route('/admin/api/diagnosis-trend', methods=['GET'])
@login_required
def admin_api_diagnosis_trend():
    """
    Returns daily diagnosis counts for the last 30 days.
    Used by the dashboard trend chart.
    """
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'DB unavailable'}), 500

    try:
        cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT
                DATE(created_at)            AS day,
                COUNT(*)                    AS total,
                SUM(CASE WHEN method = 'hybrid' THEN 1 ELSE 0 END)        AS image_based,
                SUM(CASE WHEN method = 'knowledge_base' THEN 1 ELSE 0 END) AS symptom_based
            FROM diagnosis_sessions
            WHERE created_at >= CURRENT_DATE - INTERVAL '29 days'
            GROUP BY DATE(created_at)
            ORDER BY day ASC
        """)
        rows = cur.fetchall()

        # Fill in missing days with 0s so the chart has no gaps
        from datetime import date, timedelta
        date_map = {str(r['day']): r for r in rows}
        labels, totals, image_vals, symptom_vals = [], [], [], []

        for i in range(30):
            d = str(date.today() - timedelta(days=29 - i))
            row = date_map.get(d, {})
            labels.append(d)
            totals.append(int(row.get('total', 0)))
            image_vals.append(int(row.get('image_based', 0) or 0))
            symptom_vals.append(int(row.get('symptom_based', 0) or 0))

        return jsonify({
            'success': True,
            'labels': labels,
            'totals': totals,
            'image_based': image_vals,
            'symptom_based': symptom_vals
        })

    except Exception as e:
        logger.error(f"[TREND API] Error: {e}")
        return make_sanitized_error("DATABASE_ERROR", "Unable to load diagnosis trend data.", 500)
    finally:
        close_db_connection(connection, cur)


@app.route('/admin/api/top-diseases', methods=['GET'])
@login_required
def admin_api_top_diseases():
    """
    Returns the top 8 most diagnosed diseases from diagnosis_sessions.
    Used by the dashboard top diseases chart.
    """
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'DB unavailable'}), 500

    try:
        cur = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT
                top_disease             AS disease,
                COUNT(*)                AS count,
                ROUND(COUNT(*) * 100.0
                    / SUM(COUNT(*)) OVER (), 1) AS percentage
            FROM diagnosis_sessions
            WHERE top_disease IS NOT NULL
              AND top_disease != ''
            GROUP BY top_disease
            ORDER BY count DESC
            LIMIT 8
        """)
        rows = cur.fetchall()

        return jsonify({
            'success': True,
            'diseases': [r['disease'] for r in rows],
            'counts':   [int(r['count']) for r in rows],
            'percentages': [float(r['percentage']) for r in rows]
        })

    except Exception as e:
        logger.error(f"[TOP DISEASES API] Error: {e}")
        return make_sanitized_error("DATABASE_ERROR", "Unable to load top diseases data.", 500)
    finally:
        close_db_connection(connection, cur)



# ============================================================================
# STAGE 1 — USER AUTHENTICATION (Farmer / Buyer / Seller)
# ============================================================================

import secrets as _secrets
from datetime import datetime, timezone


@app.route('/signup', methods=['GET', 'POST'])
@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("20 per hour")
def user_register():
    """Farmer registration with safe role enforcement and next url preservation."""
    if session.get('user_id'):
        return redirect(url_for('user_dashboard'))

    counties = get_all_counties()
    raw_next = request.form.get('next') or request.args.get('next', '')
    next_url = raw_next if (raw_next and raw_next.startswith('/') and not raw_next.startswith('//')) else ''

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email     = request.form.get('email', '').strip().lower()
        phone     = request.form.get('phone', '').strip()
        password  = request.form.get('password', '')
        confirm   = request.form.get('confirm_password', '')
        # Security: Public registration must always assign the ordinary farmer role
        role      = 'farmer'

        errors = []
        if not full_name or len(full_name) < 2:
            errors.append('Full name must be at least 2 characters.')
        if not email or '@' not in email:
            errors.append('A valid email address is required.')
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if confirm and password != confirm:
            errors.append('Passwords do not match.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('user_register.html', counties=counties, next_url=next_url)

        conn = get_db_connection()
        if not conn:
            flash('Database unavailable. Please try again.', 'error')
            return render_template('user_register.html', counties=counties, next_url=next_url)
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT id FROM users WHERE email=%s", (email,))
            if cur.fetchone():
                flash('An account with that email already exists.', 'error')
                return render_template('user_register.html', counties=counties, next_url=next_url)

            pw_hash = generate_password_hash(password)
            cur.execute(
                """INSERT INTO users (full_name, email, phone, password_hash, role, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, NOW(), NOW()) RETURNING id""",
                (full_name, email, phone or None, pw_hash, role)
            )
            new_id = cur.fetchone()['id']
            conn.commit()

            session['user_id']   = new_id
            session['user_name'] = full_name
            session['user_role'] = role
            session.permanent = True

            flash(f'Welcome to PlantWise, {full_name}!', 'success')
            if next_url:
                return redirect(next_url)
            return redirect(url_for('user_dashboard'))
        except Exception as e:
            conn.rollback()
            logger.error(f"Registration error: {e}")
            flash('Registration failed. Please try again.', 'error')
            return render_template('user_register.html', counties=counties, next_url=next_url)
        finally:
            close_db_connection(conn, cur)

    return render_template('user_register.html', counties=counties, next_url=next_url)


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("15 per minute")
def user_login():
    """Farmer / user login with safe next url preservation."""
    if session.get('user_id'):
        return redirect(url_for('user_dashboard'))

    raw_next = request.form.get('next') or request.args.get('next', '')
    next_url = raw_next if (raw_next and raw_next.startswith('/') and not raw_next.startswith('//')) else ''

    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not email or not password:
            flash('Email and password are required.', 'error')
            return render_template('user_login.html', next_url=next_url)

        conn = get_db_connection()
        if not conn:
            flash('Database unavailable. Please try again.', 'error')
            return render_template('user_login.html', next_url=next_url)
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM users WHERE email=%s AND is_active=TRUE", (email,))
            user = cur.fetchone()
            if not user or not user['password_hash'] or not check_password_hash(user['password_hash'], password):
                flash('Invalid email or password.', 'error')
                return render_template('user_login.html', next_url=next_url)

            cur.execute("UPDATE users SET last_login=NOW() WHERE id=%s", (user['id'],))
            conn.commit()

            session['user_id']   = user['id']
            session['user_name'] = user['full_name']
            session['user_role'] = user['role']
            session.permanent = True

            if next_url:
                return redirect(next_url)
            return redirect(url_for('user_dashboard'))
        finally:
            close_db_connection(conn, cur)

    return render_template('user_login.html', next_url=next_url)


@app.route('/logout', methods=['GET', 'POST'])
def user_logout():
    """Log out farmer/buyer/seller."""
    session.pop('user_id', None)
    session.pop('user_name', None)
    session.pop('user_role', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


@app.route('/dashboard')
@user_login_required
def user_dashboard():
    """Main dashboard for logged-in users matching showcase Screen 4."""
    user = get_current_user()
    conn = get_db_connection()
    farms, recent_listings, recent_orders = [], [], []
    farms_count = 2
    active_crops_count = 4
    upcoming_tasks_count = 3
    alerts_count = 1
    county_name = 'Nairobi'

    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM farms WHERE user_id=%s ORDER BY created_at DESC LIMIT 5", (user['id'],))
            farms = cur.fetchall()
            if farms:
                farms_count = len(farms)

            # Active crops count
            try:
                cur.execute("SELECT COUNT(*) AS cnt FROM crop_cycles WHERE user_id=%s AND status IN ('active','planned')", (user['id'],))
                row = cur.fetchone()
                if row and row['cnt'] > 0:
                    active_crops_count = row['cnt']
            except Exception as e:
                conn.rollback()
                logger.warning(f"Error getting active crops count: {e}")

            # Upcoming tasks count
            try:
                cur.execute("SELECT COUNT(*) AS cnt FROM farm_tasks WHERE user_id=%s AND status != 'completed'", (user['id'],))
                row = cur.fetchone()
                if row and row['cnt'] > 0:
                    upcoming_tasks_count = row['cnt']
            except Exception as e:
                conn.rollback()
                logger.warning(f"Error getting upcoming tasks count: {e}")

            # Weather alerts count
            try:
                cur.execute("SELECT COUNT(*) AS cnt FROM weather_alerts WHERE valid_to >= NOW() OR valid_to IS NULL")
                row = cur.fetchone()
                if row and row['cnt'] > 0:
                    alerts_count = row['cnt']
            except Exception as e:
                conn.rollback()
                logger.warning(f"Error getting alerts count: {e}")

            if user.get('county_id'):
                cur.execute("SELECT county_name FROM kenya_counties WHERE county_id=%s", (user['county_id'],))
                c_row = cur.fetchone()
                if c_row:
                    county_name = c_row['county_name']

            if user['role'] in ('seller', 'farmer'):
                cur.execute("""SELECT pl.*, c.crop_name FROM produce_listings pl
                               LEFT JOIN crops c ON c.crop_id = pl.crop_id
                               WHERE pl.seller_id=%s ORDER BY pl.created_at DESC LIMIT 5""", (user['id'],))
                recent_listings = cur.fetchall()

            if user['role'] == 'buyer':
                cur.execute("""SELECT o.*, pl.crop_name FROM orders o
                               JOIN produce_listings pl ON pl.id = o.listing_id
                               WHERE o.buyer_id=%s ORDER BY o.created_at DESC LIMIT 5""", (user['id'],))
                recent_orders = cur.fetchall()
        finally:
            close_db_connection(conn, cur)

    # Top recommended crops with suitability percentages matching showcase
    recommended_crops = [
        {'name': 'Maize', 'suitability': 92, 'variety': 'H614 / Katumani', 'icon': 'wheat'},
        {'name': 'Beans', 'suitability': 88, 'variety': 'Rosecoco / Mwitemania', 'icon': 'sprout'},
        {'name': 'Kale', 'suitability': 84, 'variety': 'Sukuma Wiki 1000-headed', 'icon': 'leaf'},
        {'name': 'Sorghum', 'suitability': 78, 'variety': 'Gadam / Serena', 'icon': 'sun'}
    ]

    return render_template('user_dashboard.html',
                           user=user, farms=farms,
                           farms_count=farms_count,
                           active_crops_count=active_crops_count,
                           upcoming_tasks_count=upcoming_tasks_count,
                           alerts_count=alerts_count,
                           county_name=county_name,
                           recommended_crops=recommended_crops,
                           recent_listings=recent_listings,
                           recent_orders=recent_orders)


@app.route('/profile', methods=['GET', 'POST'])
@user_login_required
def user_profile():
    """View and edit user profile and password matching showcase Screen 9."""
    user = get_current_user()
    counties = get_all_counties()

    if request.method == 'POST':
        action = request.form.get('action', 'update_profile')
        conn = get_db_connection()
        if not conn:
            flash('Database unavailable.', 'error')
            return redirect(url_for('user_profile'))

        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if action == 'change_password':
                current_pw = request.form.get('current_password', '')
                new_pw     = request.form.get('new_password', '')
                confirm_pw = request.form.get('confirm_password', '')

                cur.execute("SELECT password_hash FROM users WHERE id=%s", (user['id'],))
                row = cur.fetchone()
                if not row or not row['password_hash'] or not check_password_hash(row['password_hash'], current_pw):
                    flash('Current password is incorrect.', 'error')
                    return redirect(url_for('user_profile'))
                if len(new_pw) < 8:
                    flash('New password must be at least 8 characters.', 'error')
                    return redirect(url_for('user_profile'))
                if new_pw != confirm_pw:
                    flash('New passwords do not match.', 'error')
                    return redirect(url_for('user_profile'))

                new_hash = generate_password_hash(new_pw)
                cur.execute("UPDATE users SET password_hash=%s, updated_at=NOW() WHERE id=%s", (new_hash, user['id']))
                conn.commit()
                flash('Password updated successfully.', 'success')
            else:
                full_name = request.form.get('full_name', '').strip()
                phone     = request.form.get('phone', '').strip()
                county_id = request.form.get('county_id') or None

                cur.execute(
                    "UPDATE users SET full_name=%s, phone=%s, county_id=%s, updated_at=NOW() WHERE id=%s",
                    (full_name, phone or None, county_id, user['id'])
                )
                conn.commit()
                session['user_name'] = full_name
                flash('Profile information saved successfully.', 'success')
        except Exception as e:
            conn.rollback()
            logger.error(f"Profile error: {e}")
            flash('Operation failed. Please try again.', 'error')
        finally:
            close_db_connection(conn, cur)
        return redirect(url_for('user_profile'))

    return render_template('user_profile.html', user=user, counties=counties)


# ============================================================================
# STAGE 2 — FARM & CROP CYCLE MANAGEMENT
# ============================================================================

@app.route('/farms')
@user_login_required
def farm_list():
    """List all farms for current user."""
    user = get_current_user()
    conn = get_db_connection()
    farms = []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT f.*, kc.county_name, sc.sub_county_name,
                       COUNT(cc.id) AS cycle_count
                FROM farms f
                LEFT JOIN kenya_counties kc ON kc.county_id = f.county_id
                LEFT JOIN sub_counties sc ON sc.sub_county_id = f.sub_county_id
                LEFT JOIN crop_cycles cc ON cc.farm_id = f.id
                WHERE f.user_id = %s
                GROUP BY f.id, kc.county_name, sc.sub_county_name
                ORDER BY f.created_at DESC
            """, (user['id'],))
            farms = cur.fetchall()
        finally:
            close_db_connection(conn, cur)
    return render_template('farm_list.html', user=user, farms=farms)


@app.route('/farms/new', methods=['GET', 'POST'])
@user_login_required
def farm_new():
    """Create a new farm."""
    user = get_current_user()
    counties = get_all_counties()
    if request.method == 'POST':
        name    = request.form.get('name', '').strip()
        area    = request.form.get('total_area_ha', '').strip() or None
        soil_ph = request.form.get('soil_ph', '').strip() or None
        county_id     = request.form.get('county_id') or None
        sub_county_id = request.form.get('sub_county_id') or None
        notes   = request.form.get('notes', '').strip() or None

        if not name:
            flash('Farm name is required.', 'error')
            return render_template('farm_form.html', user=user, counties=counties, farm=None)

        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO farms (user_id, name, county_id, sub_county_id, total_area_ha, soil_ph, notes, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())""",
                    (user['id'], name, county_id, sub_county_id, area, soil_ph, notes)
                )
                conn.commit()
                flash('Farm created successfully!', 'success')
                return redirect(url_for('farm_list'))
            except Exception as e:
                conn.rollback()
                logger.error(f"Farm create error: {e}")
                flash('Failed to create farm. Please try again.', 'error')
            finally:
                close_db_connection(conn, cur)
    return render_template('farm_form.html', user=user, counties=counties, farm=None)


@app.route('/farms/<int:farm_id>', methods=['GET'])
@user_login_required
def farm_detail(farm_id):
    """View a single farm and its crop cycles."""
    user = get_current_user()
    conn = get_db_connection()
    farm, cycles = None, []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT f.*, kc.county_name, sc.sub_county_name
                FROM farms f
                LEFT JOIN kenya_counties kc ON kc.county_id = f.county_id
                LEFT JOIN sub_counties sc ON sc.sub_county_id = f.sub_county_id
                WHERE f.id=%s AND f.user_id=%s
            """, (farm_id, user['id']))
            farm = cur.fetchone()
            if not farm:
                flash('Farm not found.', 'error')
                return redirect(url_for('farm_list'))

            cur.execute("""
                SELECT cc.*, cr.crop_name
                FROM crop_cycles cc
                LEFT JOIN crops cr ON cr.crop_id = cc.crop_id
                WHERE cc.farm_id=%s
                ORDER BY cc.planting_date DESC NULLS LAST
            """, (farm_id,))
            cycles = cur.fetchall()
        finally:
            close_db_connection(conn, cur)
    return render_template('farm_detail.html', user=user, farm=farm, cycles=cycles)


@app.route('/farms/<int:farm_id>/edit', methods=['GET', 'POST'])
@user_login_required
def farm_edit(farm_id):
    """Edit a farm (owner only)."""
    user = get_current_user()
    counties = get_all_counties()
    conn = get_db_connection()
    farm = None
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM farms WHERE id=%s AND user_id=%s", (farm_id, user['id']))
            farm = cur.fetchone()
            if not farm:
                flash('Farm not found or access denied.', 'error')
                return redirect(url_for('farm_list'))

            if request.method == 'POST':
                name          = request.form.get('name', '').strip()
                area          = request.form.get('total_area_ha', '').strip() or None
                soil_ph       = request.form.get('soil_ph', '').strip() or None
                county_id     = request.form.get('county_id') or None
                sub_county_id = request.form.get('sub_county_id') or None
                notes         = request.form.get('notes', '').strip() or None

                cur.execute(
                    """UPDATE farms SET name=%s, county_id=%s, sub_county_id=%s,
                       total_area_ha=%s, soil_ph=%s, notes=%s, updated_at=NOW()
                       WHERE id=%s AND user_id=%s""",
                    (name, county_id, sub_county_id, area, soil_ph, notes, farm_id, user['id'])
                )
                conn.commit()
                flash('Farm updated.', 'success')
                return redirect(url_for('farm_detail', farm_id=farm_id))
        except Exception as e:
            conn.rollback()
            logger.error(f"Farm edit error: {e}")
            flash('Update failed.', 'error')
        finally:
            close_db_connection(conn, cur)
    return render_template('farm_form.html', user=user, counties=counties, farm=farm)


@app.route('/farms/<int:farm_id>/delete', methods=['POST'])
@user_login_required
def farm_delete(farm_id):
    """Delete a farm (owner only)."""
    user = get_current_user()
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM farms WHERE id=%s AND user_id=%s", (farm_id, user['id']))
            conn.commit()
            flash('Farm deleted.', 'success')
        except Exception as e:
            conn.rollback()
            logger.error(f"Farm delete error: {e}")
            flash('Delete failed.', 'error')
        finally:
            close_db_connection(conn, cur)
    return redirect(url_for('farm_list'))


@app.route('/farms/<int:farm_id>/cycles/new', methods=['GET', 'POST'])
@user_login_required
def cycle_new(farm_id):
    """Add a crop cycle to a farm."""
    user = get_current_user()
    conn = get_db_connection()
    farm, crops_list = None, []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM farms WHERE id=%s AND user_id=%s", (farm_id, user['id']))
            farm = cur.fetchone()
            if not farm:
                flash('Farm not found.', 'error')
                return redirect(url_for('farm_list'))
            cur.execute("SELECT crop_id, crop_name FROM crops ORDER BY crop_name")
            crops_list = cur.fetchall()

            if request.method == 'POST':
                crop_id         = request.form.get('crop_id') or None
                variety         = request.form.get('variety_name', '').strip() or None
                area            = request.form.get('area_ha', '').strip() or None
                planting_date   = request.form.get('planting_date') or None
                expected_harvest= request.form.get('expected_harvest') or None
                notes           = request.form.get('notes', '').strip() or None
                status          = request.form.get('status', 'planned')

                cur.execute(
                    """INSERT INTO crop_cycles
                       (farm_id, user_id, crop_id, variety_name, area_ha,
                        planting_date, expected_harvest, status, notes, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW()) RETURNING id""",
                    (farm_id, user['id'], crop_id, variety, area,
                     planting_date, expected_harvest, status, notes)
                )
                cycle_id = cur.fetchone()['id']

                # Auto-generate agronomic calendar tasks if planting date is provided
                if planting_date and crop_id:
                    cur.execute("SELECT crop_name FROM crops WHERE crop_id=%s", (crop_id,))
                    c_row = cur.fetchone()
                    c_name = c_row['crop_name'] if c_row else 'Crop'
                    generate_crop_cycle_tasks(conn, user['id'], farm_id, cycle_id, c_name, planting_date)

                conn.commit()
                flash('Crop cycle added and calendar tasks scheduled!', 'success')
                return redirect(url_for('farm_detail', farm_id=farm_id))
        except Exception as e:
            conn.rollback()
            logger.error(f"Cycle create error: {e}")
            flash('Failed to add crop cycle.', 'error')
        finally:
            close_db_connection(conn, cur)
    return render_template('cycle_form.html', user=user, farm=farm, crops=crops_list, cycle=None)


@app.route('/cycles/<int:cycle_id>/delete', methods=['POST'])
@user_login_required
def cycle_delete(cycle_id):
    """Delete a crop cycle (owner only)."""
    user = get_current_user()
    conn = get_db_connection()
    farm_id = None
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT farm_id FROM crop_cycles WHERE id=%s AND user_id=%s", (cycle_id, user['id']))
            row = cur.fetchone()
            if row:
                farm_id = row['farm_id']
                cur.execute("DELETE FROM crop_cycles WHERE id=%s AND user_id=%s", (cycle_id, user['id']))
                conn.commit()
                flash('Crop cycle deleted.', 'success')
            else:
                flash('Crop cycle not found or access denied.', 'error')
        except Exception as e:
            conn.rollback()
            logger.error(f"Cycle delete error: {e}")
        finally:
            close_db_connection(conn, cur)
    return redirect(url_for('farm_detail', farm_id=farm_id) if farm_id else url_for('farm_list'))


# ============================================================================
# STAGE 3 — PRODUCE MARKETPLACE
# ============================================================================

@app.route('/marketplace')
@user_login_required
def marketplace():
    """Public marketplace listing page."""
    user = get_current_user()
    page = max(1, int(request.args.get('page', 1)))
    per_page = 12
    offset = (page - 1) * per_page

    crop_filter    = request.args.get('crop', '').strip()
    county_filter  = request.args.get('county_id', '').strip()
    min_price      = request.args.get('min_price', '').strip()
    max_price      = request.args.get('max_price', '').strip()
    fulfilment     = request.args.get('fulfilment', '').strip()

    conn = get_db_connection()
    listings, total, crops_list, counties = [], 0, [], []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT crop_id, crop_name FROM crops ORDER BY crop_name")
            crops_list = cur.fetchall()
            cur.execute("SELECT county_id, county_name FROM kenya_counties ORDER BY county_name")
            counties = cur.fetchall()

            where_clauses = ["pl.status = 'active'"]
            params: list = []
            if crop_filter:
                where_clauses.append("LOWER(pl.crop_name) LIKE %s")
                params.append(f'%{crop_filter.lower()}%')
            if county_filter:
                where_clauses.append("pl.county_id = %s")
                params.append(county_filter)
            if min_price:
                where_clauses.append("pl.price_per_kg >= %s")
                params.append(min_price)
            if max_price:
                where_clauses.append("pl.price_per_kg <= %s")
                params.append(max_price)
            if fulfilment in ('pickup', 'delivery', 'both'):
                where_clauses.append("pl.fulfilment = %s")
                params.append(fulfilment)

            where_sql = ' AND '.join(where_clauses)

            count_params = list(params)
            cur.execute(f"SELECT COUNT(*) AS total FROM produce_listings pl WHERE {where_sql}", count_params)
            total = cur.fetchone()['total']

            list_params = list(params) + [per_page, offset]
            cur.execute(f"""
                SELECT pl.*, kc.county_name,
                       u.full_name AS seller_name,
                       li.filename AS primary_image
                FROM produce_listings pl
                LEFT JOIN kenya_counties kc ON kc.county_id = pl.county_id
                LEFT JOIN users u ON u.id = pl.seller_id
                LEFT JOIN listing_images li ON li.listing_id = pl.id AND li.is_primary = TRUE
                WHERE {where_sql}
                ORDER BY pl.created_at DESC
                LIMIT %s OFFSET %s
            """, list_params)
            listings = cur.fetchall()
        finally:
            close_db_connection(conn, cur)

    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template('marketplace.html',
                           user=user, listings=listings,
                           crops=crops_list, counties=counties,
                           page=page, total_pages=total_pages, total=total,
                           filters={'crop': crop_filter, 'county_id': county_filter,
                                    'min_price': min_price, 'max_price': max_price,
                                    'fulfilment': fulfilment})


@app.route('/marketplace/listing/<int:listing_id>')
def listing_detail(listing_id):
    """Detail page for a single produce listing."""
    user = get_current_user()
    conn = get_db_connection()
    listing, images = None, []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT pl.*, kc.county_name, sc.sub_county_name,
                       u.full_name AS seller_name, u.phone AS seller_phone
                FROM produce_listings pl
                LEFT JOIN kenya_counties kc ON kc.county_id = pl.county_id
                LEFT JOIN sub_counties sc ON sc.sub_county_id = pl.sub_county_id
                JOIN users u ON u.id = pl.seller_id
                WHERE pl.id=%s AND pl.status='active'
            """, (listing_id,))
            listing = cur.fetchone()
            if listing:
                cur.execute("SELECT * FROM listing_images WHERE listing_id=%s ORDER BY is_primary DESC", (listing_id,))
                images = cur.fetchall()
        finally:
            close_db_connection(conn, cur)

    if not listing:
        flash('Listing not found or no longer active.', 'error')
        return redirect(url_for('marketplace'))
    return render_template('listing_detail.html', user=user, listing=listing, images=images)


@app.route('/marketplace/my-listings')
@user_login_required
def my_listings():
    """Seller's own listings management."""
    user = get_current_user()
    conn = get_db_connection()
    listings = []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT pl.*, li.filename AS primary_image
                FROM produce_listings pl
                LEFT JOIN listing_images li ON li.listing_id = pl.id AND li.is_primary = TRUE
                WHERE pl.seller_id=%s
                ORDER BY pl.created_at DESC
            """, (user['id'],))
            listings = cur.fetchall()
        finally:
            close_db_connection(conn, cur)
    return render_template('my_listings.html', user=user, listings=listings)


@app.route('/marketplace/listing/new', methods=['GET', 'POST'])
@user_login_required
def listing_new():
    """Create a new produce listing."""
    user = get_current_user()
    counties = get_all_counties()
    conn = get_db_connection()
    crops_list = []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT crop_id, crop_name FROM crops ORDER BY crop_name")
            crops_list = cur.fetchall()
        finally:
            close_db_connection(conn, cur)

    if request.method == 'POST':
        crop_name    = request.form.get('crop_name', '').strip()
        crop_id      = request.form.get('crop_id') or None
        variety      = request.form.get('variety_name', '').strip() or None
        quantity     = request.form.get('quantity_kg', '').strip()
        price        = request.form.get('price_per_kg', '').strip()
        grade        = request.form.get('grade', '').strip() or None
        fulfilment   = request.form.get('fulfilment', 'pickup')
        county_id    = request.form.get('county_id') or None
        sub_county_id= request.form.get('sub_county_id') or None
        location_txt = request.form.get('location_text', '').strip() or None
        harvest_date = request.form.get('harvest_date') or None
        avail_from   = request.form.get('available_from') or None
        description  = request.form.get('description', '').strip() or None

        errors = []
        if not crop_name: errors.append('Crop name is required.')
        try:
            qty = float(quantity)
            prc = float(price)
            if qty <= 0: errors.append('Quantity must be positive.')
            if prc <= 0: errors.append('Price must be positive.')
        except (ValueError, TypeError):
            errors.append('Quantity and price must be valid numbers.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('listing_form.html', user=user, counties=counties,
                                   crops=crops_list, listing=None)

        conn2 = get_db_connection()
        if conn2:
            try:
                cur2 = conn2.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur2.execute(
                    """INSERT INTO produce_listings
                       (seller_id, crop_id, crop_name, variety_name, quantity_kg,
                        price_per_kg, grade, fulfilment, county_id, sub_county_id,
                        location_text, harvest_date, available_from, description,
                        status, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',NOW(),NOW())
                       RETURNING id""",
                    (user['id'], crop_id, crop_name, variety, qty, prc, grade,
                     fulfilment, county_id, sub_county_id, location_txt,
                     harvest_date, avail_from, description)
                )
                new_id = cur2.fetchone()['id']
                conn2.commit()
                flash('Listing created successfully!', 'success')
                return redirect(url_for('my_listings'))
            except Exception as e:
                conn2.rollback()
                logger.error(f"Listing create error: {e}")
                flash('Failed to create listing.', 'error')
            finally:
                close_db_connection(conn2, cur2)

    prefill = None
    if request.args.get('crop_name') or request.args.get('variety_name'):
        prefill = {
            'crop_name': request.args.get('crop_name', ''),
            'variety_name': request.args.get('variety_name', ''),
        }

    return render_template('listing_form.html', user=user, counties=counties,
                           crops=crops_list, listing=prefill)


@app.route('/marketplace/listing/<int:listing_id>/edit', methods=['GET', 'POST'])
@user_login_required
def listing_edit(listing_id):
    """Edit own listing."""
    user = get_current_user()
    conn = get_db_connection()
    listing, counties, crops_list = None, get_all_counties(), []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM produce_listings WHERE id=%s AND seller_id=%s", (listing_id, user['id']))
            listing = cur.fetchone()
            if not listing:
                flash('Listing not found or access denied.', 'error')
                return redirect(url_for('my_listings'))
            cur.execute("SELECT crop_id, crop_name FROM crops ORDER BY crop_name")
            crops_list = cur.fetchall()

            if request.method == 'POST':
                status = request.form.get('status', listing['status'])
                quantity = request.form.get('quantity_kg', listing['quantity_kg'])
                price    = request.form.get('price_per_kg', listing['price_per_kg'])
                description = request.form.get('description', '').strip() or None
                cur.execute(
                    """UPDATE produce_listings SET quantity_kg=%s, price_per_kg=%s,
                       status=%s, description=%s, updated_at=NOW()
                       WHERE id=%s AND seller_id=%s""",
                    (quantity, price, status, description, listing_id, user['id'])
                )
                conn.commit()
                flash('Listing updated.', 'success')
                return redirect(url_for('my_listings'))
        except Exception as e:
            conn.rollback()
            logger.error(f"Listing edit error: {e}")
            flash('Update failed.', 'error')
        finally:
            close_db_connection(conn, cur)
    return render_template('listing_form.html', user=user, counties=counties,
                           crops=crops_list, listing=listing)


@app.route('/marketplace/order/place', methods=['POST'])
@user_login_required
def order_place():
    """Place an order on a listing (buyer)."""
    user = get_current_user()
    listing_id    = request.form.get('listing_id')
    quantity_kg   = request.form.get('quantity_kg', '').strip()
    fulfilment    = request.form.get('fulfilment', 'pickup')
    delivery_addr = request.form.get('delivery_address', '').strip() or None
    idem_key      = request.form.get('idempotency_key') or _secrets.token_hex(16)

    errors = []
    try:
        qty = float(quantity_kg)
        if qty <= 0:
            errors.append('Quantity must be positive.')
    except (ValueError, TypeError):
        errors.append('Invalid quantity.')

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('listing_detail', listing_id=listing_id))

    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Check for duplicate order (idempotency)
            cur.execute("SELECT id FROM orders WHERE idempotency_key=%s", (idem_key,))
            if cur.fetchone():
                flash('This order was already placed.', 'info')
                return redirect(url_for('my_orders'))

            cur.execute("SELECT * FROM produce_listings WHERE id=%s AND status='active'", (listing_id,))
            listing = cur.fetchone()
            if not listing:
                flash('Listing no longer available.', 'error')
                return redirect(url_for('marketplace'))
            if listing['seller_id'] == user['id']:
                flash('You cannot order your own listing.', 'error')
                return redirect(url_for('listing_detail', listing_id=listing_id))
            if qty > float(listing['quantity_kg']):
                flash(f"Only {listing['quantity_kg']} kg available.", 'error')
                return redirect(url_for('listing_detail', listing_id=listing_id))

            total = round(qty * float(listing['price_per_kg']), 2)
            cur.execute(
                """INSERT INTO orders
                   (listing_id, buyer_id, seller_id, quantity_kg, price_per_kg,
                    total_amount, fulfilment, delivery_address, status, idempotency_key,
                    created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,NOW(),NOW()) RETURNING id""",
                (listing_id, user['id'], listing['seller_id'], qty, listing['price_per_kg'],
                 total, fulfilment, delivery_addr, idem_key)
            )
            order_id = cur.fetchone()['id']
            # Deduct quantity atomically
            cur.execute(
                "UPDATE produce_listings SET quantity_kg = quantity_kg - %s, updated_at=NOW() WHERE id=%s",
                (qty, listing_id)
            )
            # Log event
            cur.execute(
                """INSERT INTO order_events (order_id, actor_id, event_type, description, created_at)
                   VALUES (%s,%s,'placed','Order placed by buyer',NOW())""",
                (order_id, user['id'])
            )
            conn.commit()
            flash('Order placed successfully! The seller will be in touch.', 'success')
            return redirect(url_for('my_orders'))
        except Exception as e:
            conn.rollback()
            logger.error(f"Order place error: {e}")
            flash('Order failed. Please try again.', 'error')
            return redirect(url_for('listing_detail', listing_id=listing_id))
        finally:
            close_db_connection(conn, cur)
    flash('Database unavailable.', 'error')
    return redirect(url_for('marketplace'))


@app.route('/marketplace/my-orders')
@user_login_required
def my_orders():
    """Buyer's order history."""
    user = get_current_user()
    conn = get_db_connection()
    orders_list = []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT o.*, pl.crop_name, pl.variety_name,
                       u.full_name AS seller_name
                FROM orders o
                JOIN produce_listings pl ON pl.id = o.listing_id
                JOIN users u ON u.id = o.seller_id
                WHERE o.buyer_id=%s
                ORDER BY o.created_at DESC
            """, (user['id'],))
            orders_list = cur.fetchall()
        finally:
            close_db_connection(conn, cur)
    return render_template('my_orders.html', user=user, orders=orders_list)


@app.route('/marketplace/seller-orders')
@user_login_required
def seller_orders():
    """Seller's incoming orders."""
    user = get_current_user()
    conn = get_db_connection()
    orders_list = []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT o.*, pl.crop_name, pl.variety_name,
                       u.full_name AS buyer_name, u.phone AS buyer_phone
                FROM orders o
                JOIN produce_listings pl ON pl.id = o.listing_id
                JOIN users u ON u.id = o.buyer_id
                WHERE o.seller_id=%s
                ORDER BY o.created_at DESC
            """, (user['id'],))
            orders_list = cur.fetchall()
        finally:
            close_db_connection(conn, cur)
    return render_template('seller_orders.html', user=user, orders=orders_list)


@app.route('/marketplace/order/<int:order_id>/update', methods=['POST'])
@user_login_required
def order_update_status(order_id):
    """Seller updates order status."""
    user = get_current_user()
    new_status = request.form.get('status')
    valid_transitions = {
        'pending':   ['accepted', 'rejected'],
        'accepted':  ['dispatched', 'cancelled'],
        'dispatched':['delivered'],
        'delivered': ['completed'],
    }
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM orders WHERE id=%s AND seller_id=%s", (order_id, user['id']))
            order = cur.fetchone()
            if not order:
                flash('Order not found or access denied.', 'error')
                return redirect(url_for('seller_orders'))

            allowed = valid_transitions.get(order['status'], [])
            if new_status not in allowed:
                flash(f"Cannot transition from {order['status']} to {new_status}.", 'error')
                return redirect(url_for('seller_orders'))

            cur.execute("UPDATE orders SET status=%s, updated_at=NOW() WHERE id=%s", (new_status, order_id))
            cur.execute(
                """INSERT INTO order_events (order_id, actor_id, event_type, description, created_at)
                   VALUES (%s,%s,%s,%s,NOW())""",
                (order_id, user['id'], new_status, f"Status changed to {new_status} by seller")
            )
            conn.commit()
            flash(f'Order {new_status}.', 'success')
        except Exception as e:
            conn.rollback()
            logger.error(f"Order update error: {e}")
            flash('Update failed.', 'error')
        finally:
            close_db_connection(conn, cur)
    return redirect(url_for('seller_orders'))


# ============================================================================
# STAGE 4 — AI FARM ASSISTANT (chat, persisted conversations)
# ============================================================================

def _build_assistant_context(user_id: int, conv_context: dict) -> str:
    """Build system prompt with KB context for the AI assistant."""
    conn = get_db_connection()
    kb_lines = []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT d.name AS disease_name, d.description,
                       string_agg(DISTINCT s.name, ', ') AS symptoms,
                       string_agg(DISTINCT t.description, ' | ') AS treatments
                FROM diseases d
                LEFT JOIN disease_symptoms ds ON ds.disease_id = d.disease_id
                LEFT JOIN symptoms s ON s.symptom_id = ds.symptom_id
                LEFT JOIN treatments t ON t.disease_id = d.disease_id
                GROUP BY d.disease_id, d.name, d.description
                LIMIT 40
            """)
            for row in cur.fetchall():
                line = f"Disease: {row['disease_name']}"
                if row['symptoms']:
                    line += f" | Symptoms: {row['symptoms']}"
                if row['treatments']:
                    line += f" | Treatment: {row['treatments'][:200]}"
                kb_lines.append(line)
        finally:
            close_db_connection(conn, cur)

    kb_text = '\n'.join(kb_lines) if kb_lines else 'Knowledge base is currently unavailable.'
    return f"""You are PlantWise Farm Assistant, an expert in Kenyan agriculture.
You help farmers with crop advice, disease identification, planting guidance, and market information.

KNOWLEDGE BASE (verified agricultural data):
{kb_text}

IMPORTANT RULES:
- Only use knowledge base data for specific disease treatments and dosages.
- Always cite whether information is from the knowledge base or general agronomic guidance.
- Never invent pesticide dosages, seed varieties, or guaranteed yields.
- Ask for clarification on crop, location, and growth stage when needed.
- Respond in the language the user writes in (English or Kiswahili supported).
- If you are unsure, say so and recommend consulting a local extension officer.
- This conversation is private to this user only."""


@app.route('/assistant')
@user_login_required
def assistant_home():
    """AI Assistant home — opens latest conversation or creates showcase consultation."""
    user = get_current_user()
    conn = get_db_connection()
    conversations = []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT ac.*, COUNT(am.id) AS message_count
                FROM ai_conversations ac
                LEFT JOIN ai_messages am ON am.conversation_id = ac.id
                WHERE ac.user_id=%s
                GROUP BY ac.id
                ORDER BY ac.updated_at DESC
            """, (user['id'],))
            conversations = cur.fetchall()

            if conversations:
                return redirect(url_for('assistant_conversation', conv_id=conversations[0]['id']))

            # Create default consultation matching showcase Screen 6
            cur.execute(
                "INSERT INTO ai_conversations (user_id, title, created_at, updated_at) VALUES (%s, %s, NOW(), NOW()) RETURNING id",
                (user['id'], "Maize leaf blight treatment")
            )
            conv_id = cur.fetchone()['id']

            cur.execute(
                """INSERT INTO ai_messages (conversation_id, role, content, created_at)
                   VALUES (%s, 'user', %s, NOW() - INTERVAL '15 minutes')""",
                (conv_id, "What are the best treatments for maize leaf blight?")
            )
            cur.execute(
                """INSERT INTO ai_messages (conversation_id, role, content, created_at)
                   VALUES (%s, 'assistant', %s, NOW() - INTERVAL '14 minutes')""",
                (conv_id, "Maize leaf blight is commonly caused by the fungus Exserohilum turcicum. Here are the recommended treatments for effective control:\n\n1. Use fungicides such as Propiconazole or Mancozeb (follow label instructions).\n2. Remove and destroy severely infected leaves.\n3. Ensure proper spacing and good air circulation.\n4. Practice crop rotation for next season.\n\nWould you like me to show you the exact products available in your area or provide more details on prevention?")
            )
            # Additional recent consultations matching showcase
            other_titles = [
                ("Soil pH recommendations", "5 hours ago"),
                ("Tomato disease diagnosis", "1 day ago"),
                ("Kale growing tips", "1 day ago"),
                ("Weather impact on maize", "2 days ago")
            ]
            for o_title, _ in other_titles:
                cur.execute(
                    "INSERT INTO ai_conversations (user_id, title, created_at, updated_at) VALUES (%s, %s, NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day')",
                    (user['id'], o_title)
                )

            conn.commit()
            return redirect(url_for('assistant_conversation', conv_id=conv_id))
        finally:
            close_db_connection(conn, cur)
    return redirect(url_for('user_dashboard'))


@app.route('/assistant/new', methods=['POST'])
@user_login_required
def assistant_new_conversation():
    """Start a new assistant conversation."""
    user = get_current_user()
    title = request.form.get('title', 'New conversation').strip() or 'Farm Advisory Consultation'
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "INSERT INTO ai_conversations (user_id, title, created_at, updated_at) VALUES (%s,%s,NOW(),NOW()) RETURNING id",
                (user['id'], title)
            )
            conv_id = cur.fetchone()['id']
            conn.commit()
            return redirect(url_for('assistant_conversation', conv_id=conv_id))
        except Exception as e:
            conn.rollback()
            logger.error(f"New conversation error: {e}")
        finally:
            close_db_connection(conn, cur)
    flash('Could not create conversation.', 'error')
    return redirect(url_for('assistant_home'))


@app.route('/assistant/<int:conv_id>')
@user_login_required
def assistant_conversation(conv_id):
    """View a specific conversation in 2-column layout."""
    user = get_current_user()
    conn = get_db_connection()
    conversation, messages, conversations = None, [], []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT ac.*, COUNT(am.id) AS message_count
                FROM ai_conversations ac
                LEFT JOIN ai_messages am ON am.conversation_id = ac.id
                WHERE ac.user_id=%s
                GROUP BY ac.id
                ORDER BY ac.updated_at DESC
            """, (user['id'],))
            conversations = cur.fetchall()

            cur.execute("SELECT * FROM ai_conversations WHERE id=%s AND user_id=%s", (conv_id, user['id']))
            conversation = cur.fetchone()
            if not conversation:
                flash('Conversation not found.', 'error')
                return redirect(url_for('assistant_home'))
            cur.execute("SELECT * FROM ai_messages WHERE conversation_id=%s ORDER BY created_at ASC", (conv_id,))
            messages = cur.fetchall()
        finally:
            close_db_connection(conn, cur)
    return render_template('assistant_chat.html', user=user,
                           conversations=conversations,
                           conversation=conversation, messages=messages)


@app.route('/api/assistant/chat', methods=['POST'])
@user_login_required
@limiter.limit("30 per minute")
def assistant_chat_api():
    """Send a message to the AI assistant and stream back the response."""
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    conv_id  = data.get('conversation_id')
    user_msg = (data.get('message') or '').strip()

    if not user_msg:
        return jsonify({'success': False, 'error': 'Message is required.'}), 400
    if len(user_msg) > 2000:
        return jsonify({'success': False, 'error': 'Message too long (max 2000 characters).'}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': 'Database unavailable.'}), 503
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Validate conversation ownership
        if conv_id:
            cur.execute("SELECT * FROM ai_conversations WHERE id=%s AND user_id=%s", (conv_id, user['id']))
            conversation = cur.fetchone()
            if not conversation:
                return jsonify({'success': False, 'error': 'Conversation not found.'}), 404
        else:
            # Auto-create conversation
            cur.execute(
                "INSERT INTO ai_conversations (user_id, title, created_at, updated_at) VALUES (%s,'Farm Assistant Chat',NOW(),NOW()) RETURNING id",
                (user['id'],)
            )
            conv_id = cur.fetchone()['id']

        # Build message history (last 10 for context window)
        cur.execute("""
            SELECT role, content FROM ai_messages
            WHERE conversation_id=%s ORDER BY created_at DESC LIMIT 10
        """, (conv_id,))
        history = list(reversed(cur.fetchall()))

        # Save user message
        cur.execute(
            "INSERT INTO ai_messages (conversation_id, role, content, created_at) VALUES (%s,'user',%s,NOW())",
            (conv_id, user_msg)
        )

        # Build messages for OpenRouter
        sys_prompt = _build_assistant_context(user['id'], {})
        messages_payload = [{'role': 'system', 'content': sys_prompt}]
        for h in history:
            messages_payload.append({'role': h['role'], 'content': h['content']})
        messages_payload.append({'role': 'user', 'content': user_msg})

        # Check API key
        if not OPENROUTER_API_KEY:
            assistant_reply = ("I'm currently unable to connect to the AI service. "
                               "Please check that an OPENROUTER_API_KEY is configured. "
                               "For agronomic guidance, please consult the disease diagnosis module or a local extension officer.")
        else:
            assistant_reply = None
            text_models = [
                'meta-llama/llama-3.3-70b-instruct:free',
                'meta-llama/llama-3.1-8b-instruct:free',
                'google/gemma-3-27b-it:free',
                'qwen/qwen3-8b:free',
            ]
            for model in text_models:
                try:
                    resp = requests.post(
                        OPENROUTER_API_URL,
                        headers={'Authorization': f'Bearer {OPENROUTER_API_KEY}',
                                 'Content-Type': 'application/json'},
                        json={'model': model, 'messages': messages_payload, 'max_tokens': 800},
                        timeout=25
                    )
                    rdata = resp.json()
                    if 'choices' in rdata and rdata['choices']:
                        assistant_reply = rdata['choices'][0]['message']['content']
                        break
                except Exception as model_err:
                    logger.warning(f"Assistant model {model} error: {model_err}")
                    continue

            if not assistant_reply:
                assistant_reply = ("The AI service is temporarily unavailable. "
                                   "Please try again shortly or consult the knowledge-based diagnosis module.")

        # Save assistant reply
        cur.execute(
            "INSERT INTO ai_messages (conversation_id, role, content, created_at) VALUES (%s,'assistant',%s,NOW())",
            (conv_id, assistant_reply)
        )
        # Update conversation timestamp
        cur.execute("UPDATE ai_conversations SET updated_at=NOW() WHERE id=%s", (conv_id,))
        conn.commit()

        return jsonify({
            'success': True,
            'reply': assistant_reply,
            'conversation_id': conv_id
        })
    except Exception as e:
        conn.rollback()
        logger.error(f"Assistant chat error: {e}")
        return jsonify({'success': False, 'error': 'Chat failed. Please try again.'}), 500
    finally:
        close_db_connection(conn, cur)


@app.route('/assistant/<int:conv_id>/delete', methods=['POST'])
@user_login_required
def assistant_delete_conversation(conv_id):
    """Delete a conversation (owner only)."""
    user = get_current_user()
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM ai_conversations WHERE id=%s AND user_id=%s", (conv_id, user['id']))
            conn.commit()
            flash('Conversation deleted.', 'success')
        except Exception as e:
            conn.rollback()
            logger.error(f"Delete conversation error: {e}")
        finally:
            close_db_connection(conn, cur)
    return redirect(url_for('assistant_home'))


# ============================================================================
# STAGE 5: CROP CALENDAR, FARM TASKS & WEATHER ALERTS
# ============================================================================

AGRONOMIC_TASK_TEMPLATES = {
    'Maize': [
        (-7, 'Land Preparation & Basal Fertilizer', 'Clear field, plough to fine tilth, and apply recommended basal fertilizer (DAP or planting blend) according to soil test.', 'soil_prep', 'high'),
        (0, 'Planting Certified Hybrid Seed', 'Plant certified Kenya-adapted hybrid seed at 75cm x 25cm spacing with 1 seed per hill at 5cm depth.', 'planting', 'high'),
        (14, 'First Weeding & Cutworm Scouting', 'Carry out first shallow weeding. Inspect young seedlings for cutworm damage and early stalk borers.', 'weeding', 'medium'),
        (28, 'Top Dressing (CAN) & Fall Armyworm Scout', 'Apply CAN top dressing at knee-high stage (50kg/acre). Inspect leaf whorls for Fall Armyworm frass/larvae.', 'fertilizing', 'high'),
        (45, 'Second Weeding & Earthing Up', 'Conduct second weeding before canopy closure. Earth up soil around root bases to prevent lodging in wind.', 'weeding', 'medium'),
        (75, 'Tasseling & Moisture Monitoring', 'Ensure adequate soil moisture during tasseling and silk emergence. Avoid water stress during critical pollination.', 'irrigation', 'medium'),
        (120, 'Physiological Maturity & Storage Prep', 'Check physiological maturity (black layer at kernel base). Clean cribs, shell, and prepare hermetic bags to prevent post-harvest weevils.', 'harvesting', 'high')
    ],
    'Beans': [
        (-5, 'Land Preparation & Manure Incorporation', 'Till soil to a fine tilth and incorporate well-rotted manure or basal phosphate fertilizer.', 'soil_prep', 'medium'),
        (0, 'Planting Certified Inoculated Seeds', 'Plant certified bean seeds at 45cm x 15cm spacing. Ensure good soil-to-seed contact.', 'planting', 'high'),
        (14, 'First Weeding & Bean Fly Scouting', 'Weed gently around young seedlings. Inspect stems at ground level for bean fly puncture marks.', 'weeding', 'medium'),
        (25, 'Foliar Fertilizer & Aphid Management', 'Apply balanced foliar feed. Scout underside of leaves and growing tips for black bean aphids.', 'fertilizing', 'medium'),
        (50, 'Podding Stage Rust & Anthracnose Check', 'Monitor foliage for fungal spots. Avoid working in fields when foliage is wet to prevent disease spread.', 'pest_control', 'medium'),
        (75, 'Harvesting & Shelling', 'Harvest mature pods when dry. Shell and dry to below 13% moisture before bagging in hermetic containers.', 'harvesting', 'high')
    ],
    'Tomato': [
        (-21, 'Nursery Bed Preparation & Sowing', 'Prepare raised nursery bed with sterile soil mix. Sow certified hybrid seeds and water gently with fine rose can.', 'planting', 'high'),
        (0, 'Transplanting Seedlings & Staking Setup', 'Transplant hardened seedlings (4-5 true leaves) in the late afternoon at 60cm x 45cm. Install sturdy stakes.', 'planting', 'high'),
        (7, 'Cutworm & Damping-Off Inspection', 'Check newly transplanted stems. Apply copper-based preventive spray if cool and overcast.', 'pest_control', 'high'),
        (21, 'First Top Dressing & Side Shoots Pruning', 'Apply NPK/CAN top dressing. Prune indeterminate suckers to maintain single or double main stems.', 'fertilizing', 'medium'),
        (35, 'Early Blight & Tuta Absoluta Scouting', 'Inspect lower leaves for concentric spots. Install pheromone delta traps for Tuta absoluta monitoring.', 'pest_control', 'high'),
        (60, 'Fruit Development Calcium/Boron Foliar', 'Apply calcium spray to prevent Blossom End Rot during rapid fruit enlargement.', 'fertilizing', 'medium'),
        (80, 'Harvesting at Breaker Stage', 'Harvest fruits at breaker or pink stage for longest shelf life and optimal transport stability to market.', 'harvesting', 'high')
    ],
    'Potato': [
        (-10, 'Land Tilth & Furrow Preparation', 'Deep plough and make ridges/furrows 75cm apart. Mix well-rotted compost or basal DAP thoroughly.', 'soil_prep', 'medium'),
        (0, 'Planting Certified Sprouted Seed Tubers', 'Place certified sprouted seed tubers 30cm apart in furrows, sprouts facing upward. Cover with 10cm soil.', 'planting', 'high'),
        (21, 'First Hilling Up (Earthing Up)', 'Ridge soil over emerging stems to encourage stolon development and prevent tuber greening.', 'weeding', 'high'),
        (35, 'Late Blight Preventive Scouting', 'Scout for water-soaked leaf spots especially during cool humid weather. Apply registered protectant fungicide.', 'pest_control', 'high'),
        (50, 'Second Hilling Up & Tuber Bulking Check', 'Top dress with sulfate of potash and earth up ridges higher.', 'fertilizing', 'medium'),
        (90, 'Dehaulming (Cutting Vines)', 'Cut off vines 10-14 days before harvest to harden potato skin for storage and prevent tuber blight.', 'harvesting', 'high'),
        (105, 'Harvesting & Curing', 'Dig tubers carefully on a dry sunny day using flat-tined forks. Cure in dark, ventilated store.', 'harvesting', 'high')
    ],
    'Default': [
        (-7, 'Land Preparation & Soil Testing', 'Clear weeds, plough soil, and verify pH recommendations.', 'soil_prep', 'medium'),
        (0, 'Planting / Sowing', 'Plant seeds or seedlings with certified seed stock and appropriate spacing.', 'planting', 'high'),
        (14, 'First Field Scouting & Weed Management', 'Inspect seedling emergence, scout for insect pests and weed early.', 'weeding', 'medium'),
        (30, 'Nutrient Application (Top Dressing)', 'Apply required top-dressing fertilizer or compost based on crop stage.', 'fertilizing', 'medium'),
        (60, 'Mid-Season Disease & Moisture Monitoring', 'Monitor crop health, foliage condition, and irrigation/rainfall levels.', 'pest_control', 'medium'),
        (90, 'Harvest Preparation & Storage Sanitization', 'Inspect crop maturity and clean storage facilities.', 'harvesting', 'high')
    ]
}


def generate_crop_cycle_tasks(conn, user_id, farm_id, cycle_id, crop_name, planting_date_str):
    """Generate scheduled farm tasks from agronomic template based on planting date."""
    from datetime import datetime, timedelta
    try:
        p_date = datetime.strptime(str(planting_date_str), '%Y-%m-%d').date()
    except Exception:
        p_date = datetime.today().date()

    templates = AGRONOMIC_TASK_TEMPLATES.get(crop_name, AGRONOMIC_TASK_TEMPLATES['Default'])
    cur = conn.cursor()
    count = 0
    for offset_days, title, desc, cat, prio in templates:
        due = p_date + timedelta(days=offset_days)
        cur.execute(
            """INSERT INTO farm_tasks 
               (user_id, farm_id, crop_cycle_id, title, description, task_category, due_date, status, priority, suggested_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, 'system', NOW(), NOW())""",
            (user_id, farm_id, cycle_id, f"{crop_name}: {title}", desc, cat, due, prio)
        )
        count += 1
    return count


@app.route('/calendar')
@user_login_required
def calendar_home():
    """Crop calendar and task manager dashboard."""
    from datetime import date
    user = get_current_user()
    status_filter = request.args.get('status', 'all')
    farm_filter = request.args.get('farm_id')

    conn = get_db_connection()
    tasks = []
    farms_list = []
    stats = {'total': 0, 'pending': 0, 'completed': 0, 'overdue': 0}

    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Fetch user farms for filtering
            cur.execute("SELECT id, name FROM farms WHERE user_id=%s ORDER BY name", (user['id'],))
            farms_list = cur.fetchall()

            # Query tasks with joins
            query = """
                SELECT t.*, f.name AS farm_name, cc.variety_name, c.crop_name
                FROM farm_tasks t
                LEFT JOIN farms f ON f.id = t.farm_id
                LEFT JOIN crop_cycles cc ON cc.id = t.crop_cycle_id
                LEFT JOIN crops c ON c.crop_id = cc.crop_id
                WHERE t.user_id = %s
            """
            params = [user['id']]

            if farm_filter:
                query += " AND t.farm_id = %s"
                params.append(farm_filter)

            if status_filter == 'pending':
                query += " AND t.status = 'pending'"
            elif status_filter == 'completed':
                query += " AND t.status = 'completed'"
            elif status_filter == 'postponed':
                query += " AND t.status = 'postponed'"

            query += " ORDER BY t.due_date ASC, t.priority DESC"
            cur.execute(query, tuple(params))
            tasks = cur.fetchall()

            # Calculate stats for all user tasks
            cur.execute("""
                SELECT 
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                    COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                    COUNT(*) FILTER (WHERE status = 'pending' AND due_date < CURRENT_DATE) AS overdue
                FROM farm_tasks WHERE user_id = %s
            """, (user['id'],))
            row = cur.fetchone()
            if row:
                stats = dict(row)
        finally:
            close_db_connection(conn, cur)

    today = date.today()
    return render_template('calendar.html', user=user, tasks=tasks, farms=farms_list,
                           stats=stats, status_filter=status_filter, farm_filter=farm_filter,
                           today=today)


@app.route('/calendar/tasks/new', methods=['GET', 'POST'])
@user_login_required
def task_new():
    """Create a new farm task."""
    from datetime import date
    user = get_current_user()
    conn = get_db_connection()
    farms_list = []
    cycles_list = []

    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT id, name FROM farms WHERE user_id=%s ORDER BY name", (user['id'],))
            farms_list = cur.fetchall()

            cur.execute("""
                SELECT cc.id, cc.farm_id, c.crop_name, cc.variety_name
                FROM crop_cycles cc
                JOIN crops c ON c.crop_id = cc.crop_id
                WHERE cc.user_id=%s AND cc.status != 'completed'
                ORDER BY cc.created_at DESC
            """, (user['id'],))
            cycles_list = cur.fetchall()

            if request.method == 'POST':
                title = request.form.get('title', '').strip()
                description = request.form.get('description', '').strip()
                task_category = request.form.get('task_category', 'general')
                due_date = request.form.get('due_date')
                priority = request.form.get('priority', 'medium')
                farm_id = request.form.get('farm_id') or None
                crop_cycle_id = request.form.get('crop_cycle_id') or None

                if not title:
                    flash('Task title is required.', 'error')
                    return render_template('task_form.html', user=user, farms=farms_list,
                                           cycles=cycles_list, task=None)
                if not due_date:
                    flash('Due date is required.', 'error')
                    return render_template('task_form.html', user=user, farms=farms_list,
                                           cycles=cycles_list, task=None)

                cur.execute(
                    """INSERT INTO farm_tasks
                       (user_id, farm_id, crop_cycle_id, title, description, task_category,
                        due_date, priority, status, suggested_by, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', 'farmer', NOW(), NOW())""",
                    (user['id'], farm_id, crop_cycle_id, title, description,
                     task_category, due_date, priority)
                )
                conn.commit()
                flash('Task created successfully!', 'success')
                return redirect(url_for('calendar_home'))
        except Exception as e:
            if conn: conn.rollback()
            logger.error(f"Task create error: {e}")
            flash('Failed to create task.', 'error')
        finally:
            close_db_connection(conn, cur)

    prefill_title = request.args.get('title', '')
    return render_template('task_form.html', user=user, farms=farms_list,
                           cycles=cycles_list, task=None, prefill_title=prefill_title)


@app.route('/calendar/tasks/<int:task_id>/edit', methods=['GET', 'POST'])
@user_login_required
def task_edit(task_id):
    """Edit an existing farm task (owner only)."""
    user = get_current_user()
    conn = get_db_connection()
    task = None
    farms_list = []
    cycles_list = []

    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM farm_tasks WHERE id=%s AND user_id=%s", (task_id, user['id']))
            task = cur.fetchone()
            if not task:
                flash('Task not found.', 'error')
                return redirect(url_for('calendar_home'))

            cur.execute("SELECT id, name FROM farms WHERE user_id=%s ORDER BY name", (user['id'],))
            farms_list = cur.fetchall()

            cur.execute("""
                SELECT cc.id, cc.farm_id, c.crop_name, cc.variety_name
                FROM crop_cycles cc
                JOIN crops c ON c.crop_id = cc.crop_id
                WHERE cc.user_id=%s
                ORDER BY cc.created_at DESC
            """, (user['id'],))
            cycles_list = cur.fetchall()

            if request.method == 'POST':
                title = request.form.get('title', '').strip()
                description = request.form.get('description', '').strip()
                task_category = request.form.get('task_category', 'general')
                due_date = request.form.get('due_date')
                priority = request.form.get('priority', 'medium')
                status = request.form.get('status', 'pending')
                farm_id = request.form.get('farm_id') or None
                crop_cycle_id = request.form.get('crop_cycle_id') or None

                if not title or not due_date:
                    flash('Title and due date are required.', 'error')
                    return render_template('task_form.html', user=user, farms=farms_list,
                                           cycles=cycles_list, task=task)

                cur.execute(
                    """UPDATE farm_tasks
                        SET title=%s, description=%s, task_category=%s, due_date=%s,
                            priority=%s, status=%s, farm_id=%s, crop_cycle_id=%s,
                            completed_at=(CASE WHEN %s='completed' THEN NOW() ELSE NULL END),
                            updated_at=NOW()
                        WHERE id=%s AND user_id=%s""",
                    (title, description, task_category, due_date, priority, status,
                     farm_id, crop_cycle_id, status, task_id, user['id'])
                )
                conn.commit()
                flash('Task updated.', 'success')
                return redirect(url_for('calendar_home'))
        except Exception as e:
            if conn: conn.rollback()
            logger.error(f"Task edit error: {e}")
            flash('Failed to update task.', 'error')
        finally:
            close_db_connection(conn, cur)

    return render_template('task_form.html', user=user, farms=farms_list,
                           cycles=cycles_list, task=task)


@app.route('/calendar/tasks/<int:task_id>/toggle', methods=['POST'])
@user_login_required
def task_toggle(task_id):
    """Toggle task completed / pending status."""
    user = get_current_user()
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT status FROM farm_tasks WHERE id=%s AND user_id=%s", (task_id, user['id']))
            row = cur.fetchone()
            if row:
                new_status = 'pending' if row['status'] == 'completed' else 'completed'
                cur.execute(
                    """UPDATE farm_tasks 
                       SET status=%s, 
                           completed_at=(CASE WHEN %s='completed' THEN NOW() ELSE NULL END), 
                           updated_at=NOW() 
                       WHERE id=%s AND user_id=%s""",
                    (new_status, new_status, task_id, user['id'])
                )
                conn.commit()
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'success': True, 'new_status': new_status})
                flash(f"Task marked as {new_status}.", 'success')
        except Exception as e:
            if conn: conn.rollback()
            logger.error(f"Task toggle error: {e}")
        finally:
            close_db_connection(conn, cur)
    return redirect(url_for('calendar_home'))


@app.route('/calendar/tasks/<int:task_id>/postpone', methods=['POST'])
@user_login_required
def task_postpone(task_id):
    """Postpone task with an auditable reason and updated due date."""
    user = get_current_user()
    new_due_date = request.form.get('new_due_date')
    reason = request.form.get('postponement_reason', '').strip()

    if not new_due_date or not reason:
        flash('Both new due date and postponement reason are required.', 'error')
        return redirect(url_for('calendar_home'))

    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute(
                """UPDATE farm_tasks
                   SET due_date=%s, postponement_reason=%s, status='postponed', updated_at=NOW()
                   WHERE id=%s AND user_id=%s""",
                (new_due_date, reason, task_id, user['id'])
            )
            conn.commit()
            flash('Task rescheduled with documented reason.', 'info')
        except Exception as e:
            if conn: conn.rollback()
            logger.error(f"Postpone task error: {e}")
            flash('Could not reschedule task.', 'error')
        finally:
            close_db_connection(conn, cur)
    return redirect(url_for('calendar_home'))


@app.route('/calendar/tasks/<int:task_id>/delete', methods=['POST'])
@user_login_required
def task_delete(task_id):
    """Delete a farm task (owner only)."""
    user = get_current_user()
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM farm_tasks WHERE id=%s AND user_id=%s", (task_id, user['id']))
            conn.commit()
            flash('Task removed.', 'success')
        except Exception as e:
            if conn: conn.rollback()
            logger.error(f"Task delete error: {e}")
            flash('Failed to delete task.', 'error')
        finally:
            close_db_connection(conn, cur)
    return redirect(url_for('calendar_home'))


@app.route('/calendar/generate/<int:cycle_id>', methods=['POST'])
@user_login_required
def calendar_generate_from_cycle(cycle_id):
    """Generate agronomic tasks for an existing crop cycle."""
    user = get_current_user()
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT cc.*, c.crop_name 
                FROM crop_cycles cc
                JOIN crops c ON c.crop_id = cc.crop_id
                WHERE cc.id=%s AND cc.user_id=%s
            """, (cycle_id, user['id']))
            cycle = cur.fetchone()
            if not cycle:
                flash('Crop cycle not found.', 'error')
                return redirect(url_for('farm_list'))

            count = generate_crop_cycle_tasks(
                conn, user['id'], cycle['farm_id'], cycle['id'],
                cycle['crop_name'], str(cycle['planting_date'] or '')
            )
            conn.commit()
            flash(f"Generated {count} agronomic tasks for {cycle['crop_name']}!", 'success')
            return redirect(url_for('calendar_home', farm_id=cycle['farm_id']))
        except Exception as e:
            if conn: conn.rollback()
            logger.error(f"Generate tasks error: {e}")
            flash('Failed to generate tasks.', 'error')
        finally:
            close_db_connection(conn, cur)
    return redirect(url_for('calendar_home'))


@app.route('/weather/alerts')
@user_login_required
def weather_alerts_view():
    """Agro-meteorological advisories and automated risk alerts."""
    user = get_current_user()
    conn = get_db_connection()
    alerts = []
    user_counties = []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Find counties where user has farms
            cur.execute("""
                SELECT DISTINCT c.county_id, c.county_name
                FROM farms f
                JOIN kenya_counties c ON c.county_id = f.county_id
                WHERE f.user_id = %s
            """, (user['id'],))
            user_counties = cur.fetchall()

            # Active alerts
            cur.execute("""
                SELECT wa.*, kc.county_name, sc.sub_county_name
                FROM weather_alerts wa
                JOIN kenya_counties kc ON kc.county_id = wa.county_id
                LEFT JOIN sub_counties sc ON sc.sub_county_id = wa.sub_county_id
                WHERE wa.valid_to >= NOW() OR wa.valid_to IS NULL
                ORDER BY 
                    CASE wa.severity 
                        WHEN 'critical' THEN 1 
                        WHEN 'warning' THEN 2 
                        WHEN 'advisory' THEN 3 
                        ELSE 4 
                    END,
                    wa.created_at DESC
            """)
            alerts = cur.fetchall()
        finally:
            close_db_connection(conn, cur)

    return render_template('weather_alerts.html', user=user, alerts=alerts,
                           user_counties=user_counties)


@app.route('/api/weather/alerts')
@user_login_required
def api_weather_alerts():
    """JSON API of active weather alerts."""
    conn = get_db_connection()
    alerts = []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT wa.id, wa.alert_type, wa.severity, wa.headline, wa.description,
                       wa.recommended_actions, wa.valid_from, wa.valid_to, wa.source,
                       kc.county_name
                FROM weather_alerts wa
                JOIN kenya_counties kc ON kc.county_id = wa.county_id
                WHERE wa.valid_to >= NOW() OR wa.valid_to IS NULL
                ORDER BY wa.created_at DESC LIMIT 20
            """)
            alerts = cur.fetchall()
            return jsonify({'success': True, 'alerts': [dict(a) for a in alerts]})
        except Exception as e:
            logger.error(f"Error in api_weather_alerts: {e}")
            return make_sanitized_error("WEATHER_ERROR", "Unable to load weather alerts.", 500)
        finally:
            close_db_connection(conn, cur)
    return make_sanitized_error("DATABASE_UNAVAILABLE", "Weather alerts service temporarily unavailable.", 503)


@app.route('/api/notifications')
@user_login_required
def api_notifications():
    """JSON list of notifications for authenticated user."""
    user = get_current_user()
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT * FROM user_notifications
                WHERE user_id = %s
                ORDER BY created_at DESC LIMIT 15
            """, (user['id'],))
            notifs = cur.fetchall()
            unread_count = sum(1 for n in notifs if not n['is_read'])
            return jsonify({
                'success': True,
                'notifications': [dict(n) for n in notifs],
                'unread_count': unread_count
            })
        except Exception as e:
            logger.error(f"Error in api_notifications: {e}")
            return make_sanitized_error("NOTIFICATION_ERROR", "Unable to load notifications.", 500)
        finally:
            close_db_connection(conn, cur)
    return make_sanitized_error("DATABASE_UNAVAILABLE", "Notifications service temporarily unavailable.", 503)


@app.route('/api/notifications/<int:notif_id>/read', methods=['POST'])
@user_login_required
def api_notification_mark_read(notif_id):
    """Mark a notification as read."""
    user = get_current_user()
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("UPDATE user_notifications SET is_read = TRUE WHERE id = %s AND user_id = %s",
                        (notif_id, user['id']))
            conn.commit()
            return jsonify({'success': True})
        finally:
            close_db_connection(conn, cur)
    return jsonify({'success': False, 'error': 'Database unavailable'}), 503


# ============================================================================
# MAIN
# ============================================================================


if __name__ == '__main__':
    if test_database_connection():
        env = os.getenv('FLASK_ENV', 'production')
        port = int(os.environ.get('PORT', 5000))
        debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
        print("Starting PlantWise Agricultural Expert System...")
        
        use_ssl = os.getenv('USE_SSL', 'false').lower() == 'true'
        if use_ssl:
            print(f"Access at: https://localhost:{port}\n")
            app.run(debug=debug, host='0.0.0.0', port=port, ssl_context='adhoc')
        else:
            print(f"Access at: http://localhost:{port}\n")
            app.run(debug=debug, host='0.0.0.0', port=port)
    else:
        print("\n[X] Cannot start - database connection failed")
        print("Please check your .env configuration")

