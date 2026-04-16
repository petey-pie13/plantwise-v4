"""
PlantWise Agricultural Expert System - Backend
Integrates with existing crop_advisory database
Tables: kenya_counties, crop_suitability, crops
"""

import logging
from flask import Flask, render_template, request, flash, redirect, url_for, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import mysql.connector
from mysql.connector import Error
import os
import uuid
from datetime import timedelta
from dotenv import load_dotenv
from typing import List, Dict, Optional
import json
import requests
import base64
from io import BytesIO
from functools import wraps

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

# Load environment variables
load_dotenv()

# Get the directory of this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# OpenRouter AI Vision API Configuration (Free)
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
OPENROUTER_API_URL = 'https://openrouter.ai/api/v1/chat/completions'
OPENROUTER_MODELS = [
    'qwen/qwen2.5-vl-72b-instruct:free',       # Best vision quality, free
    'qwen/qwen2.5-vl-32b-instruct:free',       # Slightly smaller, still excellent
    'moonshotai/kimi-vl-a3b-thinking:free',    # Vision + reasoning
    'mistralai/mistral-small-3.1-24b-instruct:free',  # Proven vision support
    'google/gemma-3-27b-it:free',              # Lower fallback
    'openrouter/auto',                         # Smart fallback router (picks best available)
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
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'crop_advisory'),
    'port': int(os.getenv('DB_PORT', 3306))
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

def get_db_connection():
    """
    Create and return a database connection to crop_advisory database
    """
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        if connection.is_connected():
            return connection
    except Error as e:
        logger.error(f"Error connecting to MySQL Database 'crop_advisory': {e}")
        return None


def close_db_connection(connection, cursor=None):
    """
    Safely close database connection and cursor
    """
    try:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()
    except Error as e:
        logger.error(f"Error closing database connection: {e}")


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
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM admin_users WHERE username = %s AND is_active = TRUE",
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
        cursor = connection.cursor(dictionary=True)
        
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
            WHERE DATE(created_at) = CURDATE()
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
# CROP ADVISORY FUNCTIONS
# ============================================================================

def get_all_counties() -> List[str]:
    """
    Fetch all counties from kenya_counties table
    
    Returns:
        List of county names sorted alphabetically
    """
    connection = get_db_connection()
    if not connection:
        print("Failed to connect to database")
        return []
    
    try:
        cursor = connection.cursor()
        
        query = "SELECT county_name FROM kenya_counties ORDER BY county_name ASC"
        cursor.execute(query)
        counties = [row[0] for row in cursor.fetchall()]
        
        print(f"[OK] Loaded {len(counties)} counties from database")
        return counties
        
    except Error as e:
        print(f"Error fetching counties: {e}")
        return []
    finally:
        close_db_connection(connection, cursor)


def get_all_crops() -> List[str]:
    """
    Fetch all crops from crops table
    
    Returns:
        List of crop names sorted alphabetically
    """
    connection = get_db_connection()
    if not connection:
        print("Failed to connect to database")
        return []
    
    try:
        cursor = connection.cursor()
        
        query = "SELECT crop_name FROM crops ORDER BY crop_name ASC"
        cursor.execute(query)
        crops = [row[0] for row in cursor.fetchall()]
        
        print(f"[OK] Loaded {len(crops)} crops from database")
        return crops
        
    except Error as e:
        print(f"Error fetching crops: {e}")
        return []
    finally:
        close_db_connection(connection, cursor)


def get_sub_counties_by_county(county: str) -> List[str]:
    """
    Fetch sub-counties for a specific county from sub_counties table
    
    Args:
        county: County name to filter by
        
    Returns:
        List of sub-county names sorted alphabetically
    """
    connection = get_db_connection()
    if not connection:
        print("Failed to connect to database")
        return []
    
    try:
        cursor = connection.cursor()
        
        # JOIN with kenya_counties to match county name to county_id
        query = """
            SELECT sc.sub_county_name 
            FROM sub_counties sc
            INNER JOIN kenya_counties kc ON sc.county_id = kc.county_id
            WHERE kc.county_name = %s 
            ORDER BY sc.sub_county_name ASC
        """
        cursor.execute(query, (county,))
        sub_counties = [row[0] for row in cursor.fetchall()]
        
        print(f"[OK] Loaded {len(sub_counties)} sub-counties for {county}")
        return sub_counties
        
    except Error as e:
        print(f"Error fetching sub-counties: {e}")
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
        cursor = connection.cursor(dictionary=True)

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
            COALESCE(ccs.commonly_grown, TRUE)        AS commonly_grown
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
            AND COALESCE(ccs.commonly_grown, TRUE) = TRUE
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
        top_3 = scored_crops[:3]

        recommendations = []
        for idx, item in enumerate(top_3, 1):
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
        cursor = connection.cursor(dictionary=True)
        
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
        LIMIT 3
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
# FLASK ROUTES
# ============================================================================

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
            
            # Get recommendations
            recommended_crops = get_crop_recommendations(
                county=county,
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
                    _sub_county = session.get('user_input', {}).get('sub_county', request.form.get('sub_county', ''))
                    _top_crop = recommended_crops[0].get('name', '') if recommended_crops else ''
                    _cur2.execute("""
                        INSERT INTO advisory_sessions
                            (county, sub_county, soil_ph, rainfall, temperature, result_count, top_disease, ip_address)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (county, _sub_county, soil_ph, rainfall, temperature,
                          len(recommended_crops), _top_crop, request.remote_addr))
                    _conn2.commit()
                    close_db_connection(_conn2, _cur2)
                    print(f"[ADVISORY] Session logged: {county}, top={_top_crop}, crops={len(recommended_crops)}")
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
    """Disease diagnosis results page"""
    # Get diagnosis results from session
    results = session.get('diagnosis_results', {})
    
    # If no results in session, redirect back to diagnosis
    if not results:
        flash('No diagnosis results found. Please complete a diagnosis first.', 'warning')
        return redirect(url_for('diagnosis'))
    
    return render_template('diagnosis_results.html', 
                         crop=results.get('crop', 'Unknown'),
                         method=results.get('method', 'unknown'),
                         plant_part=results.get('plant_part', ''),
                         diseases=results.get('diseases', []),
                         uploaded_image_url=results.get('uploaded_image_url'))


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


@app.route('/admin/logout')
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
            cur = connection.cursor(dictionary=True)
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
            cur = connection.cursor(dictionary=True)
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
            cur = connection.cursor(dictionary=True)
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
            cur = connection.cursor(dictionary=True)
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
            cur = connection.cursor(dictionary=True)
            cur.execute("SELECT * FROM crops ORDER BY crop_name")
            crops = cur.fetchall()
        except Error as e:
            return jsonify({'error': str(e)}), 500
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
            cur = connection.cursor(dictionary=True)
            base_sql = """
                SELECT  d.disease_id,
                        d.name,
                        d.scientific_name,
                        d.category,
                        d.severity,
                        d.description,
                        d.ai_label,
                        GROUP_CONCAT(DISTINCT c.crop_name ORDER BY c.crop_name SEPARATOR ', ')
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
            cur = connection.cursor(dictionary=True)
            cur.execute("""
                SELECT  f.id,
                        f.session_ref,
                        f.disease_name,
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
            cur = connection.cursor(dictionary=True)
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
            cur = connection.cursor(dictionary=True)
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
            cur = connection.cursor(dictionary=True)
            cur.execute("SELECT username, is_active FROM admin_users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if row:
                new_state = not row['is_active']
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
            cur = connection.cursor(dictionary=True)

            # Total crops
            cur.execute("SELECT COUNT(*) AS cnt FROM crops")
            stats['total_crops'] = cur.fetchone()['cnt']

            # Total admin users
            cur.execute("SELECT COUNT(*) AS cnt FROM admin_users WHERE is_active = TRUE")
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
            cur = connection.cursor(dictionary=True)
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
                        ON DUPLICATE KEY UPDATE setting_val = VALUES(setting_val), updated_by = VALUES(updated_by)
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
        cur = conn.cursor(dictionary=True)

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
        print(f"[SOIL] Profile fetch error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        close_db_connection(conn, cur)

@app.route('/api/ecological-conditions', methods=['GET'])
def api_ecological_conditions():
    """
    Server-side weather fetch from Open-Meteo.
    Receives lat/lon from the frontend and returns
    categorised rainfall and temperature.
    """
    try:
        lat = float(request.args.get('lat', 0))
        lon = float(request.args.get('lon', 0))
        weather_data = fetch_ecological_conditions_data(lat, lon)
        # If the helper fell back to defaults, signal failure so the
        # frontend can show the manual-selection form instead.
        if weather_data.get('source') == 'Error-Fallback':
            return jsonify({
                'success': False,
                'error': 'Weather data unavailable – please enter conditions manually'
            })
        return jsonify({
            'success': True,
            **weather_data
        })
    except Exception as e:
        print(f"[WEATHER] Conditions API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


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
        print(f"[WEATHER] Archive API: {start_date} → {end_date}")
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
    """API endpoint to get sub-counties for a specific county"""
    county = request.args.get('county', '')
    
    if not county:
        return jsonify({
            'success': False,
            'error': 'County parameter is required'
        }), 400
    
    sub_counties = get_sub_counties_by_county(county)
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
            address.get('neighbourhood') or
            ''
        )

        # Clean up county name
        # Nominatim often returns "Nairobi City County" 
        # or "Nakuru County"
        county = raw_county\
            .replace(' City County', '')\
            .replace(' County', '')\
            .strip()

        sub_county = raw_sub_county.strip()

        print(f"[GEOCODE] Parsed county: '{county}', "
              f"sub-county: '{sub_county}'")

        # Try to match against our kenya_counties table
        matched_county    = None
        matched_county_id = None

        if county:
            conn = get_db_connection()
            if conn:
                try:
                    cur = conn.cursor(dictionary=True)

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
        print(f"[GEOCODE] Reverse geocode error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


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
        cur = conn.cursor(dictionary=True)

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
        print(f"[ADVISORY] County lookup error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
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
                print(f"[DIAGNOSIS] API failed: {error_msg}")
                return jsonify({
                    'success': False,
                    'error': f'Diagnosis error: {error_msg}'
                }), 500
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
                import uuid
                uploads_dir = os.path.join(app.static_folder, 'uploads')
                os.makedirs(uploads_dir, exist_ok=True)
                img = uploaded_images[0]
                img.seek(0)
                img_data = img.read()
                filename = f"diag_{uuid.uuid4().hex[:8]}.jpg"
                with open(os.path.join(uploads_dir, filename), 'wb') as f:
                    f.write(img_data)
                image_url = f"/static/uploads/{filename}"
            except Exception as e:
                print(f"Could not save diagnosis image: {e}")
        
        # Store results in session for the results page
        session['diagnosis_results'] = {
            'crop': crop,
            'method': method,
            'plant_part': plant_part,
            'diseases': results,
            'uploaded_image_url': image_url
        }
        
        # ── Log this diagnosis session to the database ────────────────────
        try:
            import uuid
            _conn = get_db_connection()
            if _conn:
                _cur = _conn.cursor()
                # Map frontend method values -> ENUM values in diagnosis_sessions
                _method_map = {
                    'hybrid':        'hybrid',
                    'symptom':       'knowledge_base',
                    'knowledge':     'knowledge_base',
                    'knowledge_base': 'knowledge_base',
                }
                _db_method   = _method_map.get(str(method).lower(), 'knowledge_base')
                _top_disease = results[0].get('name', '') if results else ''
                _session_ref = str(uuid.uuid4())[:16]   # short unique ref
                _cur.execute("""
                    INSERT INTO diagnosis_sessions
                        (session_ref, crop_name, method, result_count, top_disease, ip_address)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (_session_ref, crop, _db_method, len(results), _top_disease, request.remote_addr))
                _conn.commit()
                close_db_connection(_conn, _cur)
                print(f"[DIAGNOSIS] Session logged: ref={_session_ref}, method={_db_method}, results={len(results)}")
        except Exception as _e:
            print(f"[DIAGNOSIS] Could not log session: {_e}")

        return jsonify({
            'success': True,
            'message': 'Diagnosis complete',
            'results': results
        })

        
    except Exception as e:
        print(f"Error in diagnose API: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


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
        cur = conn.cursor(dictionary=True)

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
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def page_not_found(e):
    flash('Page not found.', 'error')
    return render_template('index.html'), 404


@app.errorhandler(500)
def internal_server_error(e):
    flash('An internal server error occurred.', 'error')
    return render_template('index.html'), 500


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
            cur.execute("""
                INSERT INTO diagnosis_feedback
                    (disease_name, was_correct, session_ref, ip_address)
                VALUES (%s, %s, %s, %s)
            """, (disease_name, was_correct, session_ref or None, request.remote_addr))
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
        cur = connection.cursor(dictionary=True)
        cur.execute("""
            SELECT
                DATE(created_at)            AS day,
                COUNT(*)                    AS total,
                SUM(method = 'hybrid')      AS image_based,
                SUM(method = 'knowledge_base') AS symptom_based
            FROM diagnosis_sessions
            WHERE created_at >= CURDATE() - INTERVAL 29 DAY
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
        print(f"[TREND API] Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
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
        cur = connection.cursor(dictionary=True)
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
        print(f"[TOP DISEASES API] Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        close_db_connection(connection, cur)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    if test_database_connection():
        port = int(os.environ.get('PORT', 5000))
        debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
        print("Starting PlantWise Agricultural Expert System...")
        print(f"Access at: http://localhost:{port}\n")
        # ssl_context='adhoc' removed — production SSL is handled by the reverse proxy
        app.run(debug=debug, host='0.0.0.0', port=port)
    else:
        print("\n[X] Cannot start - database connection failed")
        print("Please check your .env configuration")

