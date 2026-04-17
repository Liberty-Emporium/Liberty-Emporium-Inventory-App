# Liberty Inventory — v2.1.0 — 2026-04-14 Echo Build
import os
import csv
import json
import uuid
import shutil
import base64
import hashlib
import bcrypt
import datetime
import io
import tempfile
import threading
import time
import functools
import sqlite3
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, send_file, send_from_directory, make_response, g)
from werkzeug.utils import secure_filename

# Load .env file
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# ── Stripe (payments) ──────────────────────────────────────────────────────
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_PUBLIC_KEY = os.environ.get('STRIPE_PUBLIC_KEY', '')
stripe_enabled = bool(STRIPE_SECRET_KEY and (STRIPE_SECRET_KEY.startswith('sk_live_') or STRIPE_SECRET_KEY.startswith('sk_test_')))

app = Flask(__name__, template_folder='templates')
def _get_secret_key():
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key
    # Persist key to /data so sessions survive Railway redeploys
    data_dir = os.environ.get('RAILWAY_DATA_DIR') or os.environ.get('DATA_DIR') or '/data'
    key_file = os.path.join(data_dir, 'secret_key')
    try:
        os.makedirs(data_dir, exist_ok=True)
        if os.path.exists(key_file):
            with open(key_file) as f:
                key = f.read().strip()
            if key:
                return key
        import secrets as _sec
        key = _sec.token_hex(32)
        with open(key_file, 'w') as f:
            f.write(key)
        return key
    except Exception:
        import secrets as _sec
        return _sec.token_hex(32)

app.secret_key = _get_secret_key()
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(hours=8)

import secrets as _secrets_module

def _get_csrf_token():
    """Generate or retrieve CSRF token from session."""
    if 'csrf_token' not in session:
        session['csrf_token'] = _secrets_module.token_hex(32)
    return session['csrf_token']

def _validate_csrf():
    """Validate CSRF token on POST requests. Returns True if valid."""
    if request.method != 'POST':
        return True
    # Skip API routes
    if request.path.startswith('/api/'):
        return True
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    return token and token == session.get('csrf_token')

app.jinja_env.globals['csrf_token'] = _get_csrf_token


# ── Security Headers (API Security Best Practices) ──────────────────────────
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    # Content Security Policy
    response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' 'unsafe-eval' https: data: blob:;"
    # Prevent clickjacking
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    # Prevent MIME type sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # XSS Protection
    response.headers['X-XSS-Protection'] = '1; mode=block'
    # Referrer Policy
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # HSTS (only for HTTPS)
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

# ── Rate Limiting (Simple In-Memory) ──────────────────────────────────────────
from collections import defaultdict
import time as time_module

# ============================================================
# RATE LIMITER — No external dependencies required
# ============================================================
import time as _rl_time

def _is_rate_limited(db, key, max_calls=5, window_seconds=60):
    """Returns True if this key has exceeded the rate limit."""
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS rate_limits (
            key TEXT NOT NULL, window_start INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (key, window_start))""")
        db.execute("DELETE FROM rate_limits WHERE window_start < ?",
                   (int(_rl_time.time()) - window_seconds * 2,))
        now = int(_rl_time.time())
        ws = now - (now % window_seconds)
        row = db.execute(
            "SELECT count FROM rate_limits WHERE key=? AND window_start=?",
            (key, ws)).fetchone()
        if row is None:
            db.execute("INSERT OR IGNORE INTO rate_limits VALUES (?,?,1)", (key, ws))
            db.commit()
            return False
        if row[0] >= max_calls:
            return True
        db.execute("UPDATE rate_limits SET count=count+1 WHERE key=? AND window_start=?",
                   (key, ws))
        db.commit()
        return False
    except Exception:
        return False


rate_limits = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # 1 minute
RATE_LIMIT_MAX = 100  # max requests per minute
# Higher limit for AI-specific routes to avoid blocking legitimate use
AI_RATE_LIMIT_MAX = 30  # AI requests per minute per IP

def check_rate_limit():
    """Check if request exceeds rate limit"""
    ip = request.remote_addr
    now = time_module.time()
    # Clean old entries
    rate_limits[ip] = [t for t in rate_limits[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(rate_limits[ip]) >= RATE_LIMIT_MAX:
        return False
    rate_limits[ip].append(now)
    return True

def check_ai_rate_limit():
    """Check if AI request exceeds rate limit - separate from general rate limit"""
    ip = request.remote_addr
    now = time_module.time()
    # Use a separate key for AI endpoints
    ai_key = f"ai_{ip}"
    rate_limits[ai_key] = [t for t in rate_limits[ai_key] if now - t < RATE_LIMIT_WINDOW]
    if len(rate_limits[ai_key]) >= AI_RATE_LIMIT_MAX:
        return False
    rate_limits[ai_key].append(now)
    return True

# Decorator for rate-limited routes
def rate_limit(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not check_rate_limit():
            return jsonify({'error': 'Rate limit exceeded. Try again later.'}), 429
        return f(*args, **kwargs)
    return decorated

# Special decorator for AI routes with higher limit
def ai_rate_limit(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not check_ai_rate_limit():
            return jsonify({'error': '⚠️ API rate limit reached. Please wait a moment before trying again.'}), 429
        return f(*args, **kwargs)
    return decorated

# ── Helper: Fix EXIF orientation ──────────────────────────────────────────────
def fix_image_orientation(img):
    """
    Rotate image based on EXIF orientation metadata.
    Handles sideways/upside-down photos from smartphones.
    """
    try:
        from PIL import ImageOps
        return ImageOps.exif_transpose(img)
    except (AttributeError, ImportError, KeyError):
        return img

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _find_writable_data_dir():
    """Return the first directory that exists and is writable."""
    candidates = [
        os.environ.get('RAILWAY_DATA_DIR', ''),
        '/data',
        BASE_DIR,
        '/tmp',
    ]
    for d in candidates:
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, '.write_test')
            with open(test, 'w') as f:
                f.write('ok')
            os.unlink(test)
            return d
        except Exception:
            continue
    return BASE_DIR

DATA_DIR = _find_writable_data_dir()
print(f"[STARTUP] DATA_DIR={DATA_DIR}", flush=True)

# ── User API Keys Database ─────────────────────────────────────────────────
USER_KEYS_DB = os.path.join(DATA_DIR, 'user_api_keys.db')

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(USER_KEYS_DB)
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA synchronous=NORMAL")
        g.db.execute("PRAGMA foreign_keys=ON")
        g.db.execute("PRAGMA busy_timeout=5000")
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_user_keys_db():
    db = sqlite3.connect(USER_KEYS_DB)
    db.execute('''CREATE TABLE IF NOT EXISTS user_api_keys (
        user_id TEXT PRIMARY KEY,
        groq_key TEXT DEFAULT '',
        openrouter_key TEXT DEFAULT '',
        anthropic_key TEXT DEFAULT '',
        xai_key TEXT DEFAULT '',
        active_provider TEXT DEFAULT 'openrouter',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    db.commit()
    db.close()

init_user_keys_db()

INVENTORY_FILE = os.path.join(DATA_DIR, 'inventory.csv')
UPLOAD_FOLDER  = os.path.join(DATA_DIR, 'uploads')
BACKUP_FOLDER  = os.path.join(DATA_DIR, 'backups')
ADS_FOLDER     = os.path.join(DATA_DIR, 'ads')
MUSIC_FOLDER   = os.path.join(DATA_DIR, 'music')
USERS_FILE     = os.path.join(DATA_DIR, 'users.json')
CUSTOMERS_DIR  = os.path.join(DATA_DIR, 'customers')
PENDING_FILE   = os.path.join(DATA_DIR, 'pending_users.json')
SALE_FILE      = os.path.join(DATA_DIR, 'sale_state.json')

for d in [UPLOAD_FOLDER, BACKUP_FOLDER, ADS_FOLDER, MUSIC_FOLDER, CUSTOMERS_DIR]:
    os.makedirs(d, exist_ok=True)

# Seed data files into DATA_DIR from BASE_DIR if not already present
if DATA_DIR != BASE_DIR:
    for _fname in ['inventory.csv', 'users.json', 'pending_users.json', 'sale_state.json']:
        _src = os.path.join(BASE_DIR, _fname)
        _dst = os.path.join(DATA_DIR, _fname)
        if os.path.exists(_src) and not os.path.exists(_dst):
            shutil.copy2(_src, _dst)
            print(f"[STARTUP] Seeded {_fname} -> {_dst}", flush=True)

# ── Centralized AI API Key Management ─────────────────────────────────────────

def get_ai_api_key(user_id=None):
    """Get Claude API key. Checks: 1) user DB key → 2) app config JSON → 3) store config.
    NO environment variable fallback.
    """
    # 1. Per-user key from DB
    if user_id:
        try:
            db = get_db()
            row = db.execute('SELECT anthropic_key FROM user_api_keys WHERE user_id = ?', (user_id,)).fetchone()
            if row and row[0]:
                return row[0].strip()
        except Exception:
            pass

    # 2. Check app config file (admin-entered)
    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file, 'r') as f:
                app_cfg = json.load(f)
            key = app_cfg.get('anthropic_api_key', '').strip()
            if key:
                return key
        except Exception:
            pass

    # 3. Check store_config.json
    cfg = load_store_config()
    key = cfg.get('anthropic_api_key', '').strip()
    return key if key else ''

def get_groq_api_key(user_id=None):
    """Get Groq API key. Checks: 1) user DB key → 2) app config JSON → 3) store config.
    NO environment variable fallback.
    """
    if user_id:
        try:
            db = get_db()
            row = db.execute('SELECT groq_key FROM user_api_keys WHERE user_id = ?', (user_id,)).fetchone()
            if row and row[0]:
                return row[0].strip()
        except Exception:
            pass

    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file, 'r') as f:
                app_cfg = json.load(f)
            key = app_cfg.get('groq_api_key', '').strip()
            if key:
                return key
        except Exception:
            pass

    cfg = load_store_config()
    key = cfg.get('groq_api_key', '').strip()
    return key if key else ''

def get_xai_api_key(user_id=None):
    """Get xAI API key. Checks: 1) user DB key → 2) app config JSON → 3) store config.
    NO environment variable fallback.
    """
    if user_id:
        try:
            db = get_db()
            row = db.execute('SELECT xai_key FROM user_api_keys WHERE user_id = ?', (user_id,)).fetchone()
            if row and row[0]:
                return row[0].strip()
        except Exception:
            pass

    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file, 'r') as f:
                app_cfg = json.load(f)
            key = app_cfg.get('xai_api_key', '').strip()
            if key:
                return key
        except Exception:
            pass

    cfg = load_store_config()
    key = cfg.get('xai_api_key', '').strip()
    return key if key else ''

def get_openrouter_api_key(user_id=None):
    """Get OpenRouter API key. Checks: 1) user DB key → 2) app config JSON → 3) store config.
    NO environment variable fallback.
    """
    if user_id:
        try:
            db = get_db()
            row = db.execute('SELECT openrouter_key FROM user_api_keys WHERE user_id = ?', (user_id,)).fetchone()
            if row and row[0]:
                return row[0].strip()
        except Exception:
            pass

    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file, 'r') as f:
                app_cfg = json.load(f)
            key = app_cfg.get('openrouter_api_key', '').strip()
            if key:
                return key
        except Exception:
            pass

    cfg = load_store_config()
    key = cfg.get('openrouter_api_key', '').strip()
    return key if key else ''

def get_openrouter_model():
    """Get selected OpenRouter model from app config. Defaults to gemini-flash-1.5."""
    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file, 'r') as f:
                app_cfg = json.load(f)
            model = app_cfg.get('openrouter_model', '').strip()
            if model:
                return model
        except Exception:
            pass
    cfg = load_store_config()
    return cfg.get('openrouter_model', 'google/gemini-flash-1.5')

def save_ai_api_key(key):
    """Save the Claude API key. Stored in-app (not in env vars)."""
    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    config = {}
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file, 'r') as f:
                config = json.load(f)
        except Exception:
            pass
    config['anthropic_api_key'] = key.strip()
    with open(app_config_file, 'w') as f:
        json.dump(config, f, indent=2)

def save_groq_api_key(key):
    """Save the Groq API key."""
    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    config = {}
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file, 'r') as f:
                config = json.load(f)
        except Exception:
            pass
    config['groq_api_key'] = key.strip()
    with open(app_config_file, 'w') as f:
        json.dump(config, f, indent=2)

def save_xai_api_key(key):
    """Save the xAI (Grok) API key."""
    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    config = {}
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file, 'r') as f:
                config = json.load(f)
        except Exception:
            pass
    config['xai_api_key'] = key.strip()
    with open(app_config_file, 'w') as f:
        json.dump(config, f, indent=2)

def get_stripe_keys():
    """Load Stripe keys from app_config.json, falling back to env vars."""
    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    secret = ''
    public = ''
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file) as f:
                cfg = json.load(f)
            secret = cfg.get('stripe_secret_key', '').strip()
            public = cfg.get('stripe_public_key', '').strip()
        except Exception:
            pass
    return (
        secret or os.environ.get('STRIPE_SECRET_KEY', ''),
        public or os.environ.get('STRIPE_PUBLIC_KEY', ''),
    )

def save_stripe_keys(secret, public):
    """Persist Stripe keys to app_config.json."""
    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    config = {}
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file) as f:
                config = json.load(f)
        except Exception:
            pass
    if secret is not None:
        config['stripe_secret_key'] = secret.strip()
    if public is not None:
        config['stripe_public_key'] = public.strip()
    with open(app_config_file, 'w') as f:
        json.dump(config, f, indent=2)

def get_smtp_config():
    """Load SMTP credentials from app_config.json."""
    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    defaults = {'smtp_host': '', 'smtp_port': 587, 'smtp_user': '', 'smtp_password': ''}
    if not os.path.exists(app_config_file):
        return defaults
    try:
        with open(app_config_file) as f:
            cfg = json.load(f)
        return {
            'smtp_host':     cfg.get('smtp_host', ''),
            'smtp_port':     int(cfg.get('smtp_port', 587)),
            'smtp_user':     cfg.get('smtp_user', ''),
            'smtp_password': cfg.get('smtp_password', ''),
        }
    except Exception:
        return defaults

def save_smtp_config(host, port, user, password):
    """Persist SMTP credentials to app_config.json."""
    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    config = {}
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file) as f:
                config = json.load(f)
        except Exception:
            pass
    config['smtp_host']     = host.strip()
    config['smtp_port']     = int(port)
    config['smtp_user']     = user.strip()
    config['smtp_password'] = password.strip()
    with open(app_config_file, 'w') as f:
        json.dump(config, f, indent=2)

def send_smtp_email(to, subject, body):
    """Send an email via SMTP using stored credentials.
    Returns (True, '') on success or (False, error_message) on failure."""
    cfg = get_smtp_config()
    if not cfg['smtp_host'] or not cfg['smtp_user'] or not cfg['smtp_password']:
        return False, 'SMTP not configured. Set credentials in Admin Settings.'
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From']    = cfg['smtp_user']
        msg['To']      = to
        if cfg['smtp_port'] == 465:
            with smtplib.SMTP_SSL(cfg['smtp_host'], 465, timeout=15) as server:
                server.login(cfg['smtp_user'], cfg['smtp_password'])
                server.sendmail(cfg['smtp_user'], [to], msg.as_string())
        else:
            with smtplib.SMTP(cfg['smtp_host'], cfg['smtp_port'], timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(cfg['smtp_user'], cfg['smtp_password'])
                server.sendmail(cfg['smtp_user'], [to], msg.as_string())
        return True, ''
    except Exception as e:
        return False, str(e)

# ── Multi-tenant helpers ──────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════
# MULTI-TENANT INFRASTRUCTURE — Applied 2026-04-16
# ══════════════════════════════════════════════════════════════════════════════
import re as _re, threading as _threading, queue as _queue, zipfile as _zipfile
import io as _io
from functools import wraps as _wraps

# ── 1. Slug Validation ────────────────────────────────────────────────────────
def _validate_slug(slug):
    """Sanitize and validate a tenant slug. Raises ValueError if invalid."""
    if not slug:
        raise ValueError("Empty slug")
    clean = _re.sub(r"[^a-z0-9\-]", "", str(slug).lower().strip())
    clean = _re.sub(r"-+", "-", clean).strip("-")[:60]
    reserved = {"admin","api","static","health","login","logout","overseer","guest","demo"}
    if not clean or clean in reserved:
        raise ValueError(f"Invalid or reserved slug: {slug}")
    return clean

# ── 2. Audit Log ──────────────────────────────────────────────────────────────
_AUDIT_FILE = os.path.join(
    os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"), "audit.log"
)

def _audit(action, slug=None, user=None, details=None):
    """Fire-and-forget audit entry. Never raises."""
    try:
        from datetime import datetime as _dt
        import json as _j
        slug  = slug  or (session.get("impersonating_slug") or session.get("store_slug") or "system")
        user  = user  or session.get("username", "unknown")
        ip    = request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr) if request else ""
        line  = _j.dumps({
            "ts": _dt.utcnow().isoformat(),
            "slug": slug, "user": user,
            "action": action, "ip": ip,
            "details": details or {}
        })
        os.makedirs(os.path.dirname(_AUDIT_FILE), exist_ok=True)
        with open(_AUDIT_FILE, "a") as _f:
            _f.write(line + "\n")
    except Exception:
        pass

# ── 3. Background Job Queue ───────────────────────────────────────────────────
class _JobQueue:
    def __init__(self):
        self._q = _queue.Queue()
        t = _threading.Thread(target=self._worker, daemon=True)
        t.start()
    def enqueue(self, fn, *args, **kwargs):
        self._q.put((fn, args, kwargs))
    def _worker(self):
        while True:
            try:
                fn, args, kwargs = self._q.get(timeout=1)
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    try:
                        app.logger.error(f"[JobQueue] {e}")
                    except Exception:
                        pass
                self._q.task_done()
            except _queue.Empty:
                pass

_job_queue = _JobQueue()

# ── 4. Per-Tenant Rate Limiter ────────────────────────────────────────────────
import time as _time
from collections import defaultdict as _defaultdict
_tenant_calls = _defaultdict(list)

def _tenant_rate_ok(slug, max_calls=120, window=60):
    now = _time.time()
    _tenant_calls[slug] = [t for t in _tenant_calls[slug] if now - t < window]
    if len(_tenant_calls[slug]) >= max_calls:
        return False
    _tenant_calls[slug].append(now)
    return True

def _tenant_rate_limit(max_calls=120):
    def decorator(f):
        @_wraps(f)
        def decorated(*args, **kwargs):
            slug = session.get("impersonating_slug") or session.get("store_slug")
            if slug and not _tenant_rate_ok(slug, max_calls):
                return jsonify({"error": "Too many requests. Please slow down."}), 429
            return f(*args, **kwargs)
        return decorated
    return decorator

# ── 5. Trial Status ───────────────────────────────────────────────────────────
def _get_trial_status(slug):
    """Returns 'paid', 'active', or 'expired'."""
    try:
        from datetime import datetime as _dt
        cfg_path = os.path.join(
            os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"),
            "customers", slug, "config.json"
        )
        if not os.path.exists(cfg_path):
            return "active"
        with open(cfg_path) as f:
            import json as _j; cfg = _j.load(f)
        if cfg.get("plan") == "paid":
            return "paid"
        trial_end = cfg.get("trial_ends")
        if not trial_end:
            return "active"
        return "active" if _dt.utcnow() < _dt.fromisoformat(trial_end) else "expired"
    except Exception:
        return "active"

def _trial_gate(f):
    """Redirect expired trials to upgrade page."""
    @_wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_guest"):
            slug = session.get("impersonating_slug") or session.get("store_slug")
            if slug and _get_trial_status(slug) == "expired":
                if not session.get("role") == "overseer":
                    flash("Your trial has expired. Upgrade to continue.", "warning")
                    return redirect("/upgrade")
        return f(*args, **kwargs)
    return decorated

# ── 6. Tenant Health Summary (for Overseer) ────────────────────────────────────
def _get_tenant_health():
    from datetime import datetime as _dt
    import json as _j, csv as _csv
    customers_dir = os.path.join(
        os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"), "customers"
    )
    stores = []
    if not os.path.exists(customers_dir):
        return stores
    for slug in os.listdir(customers_dir):
        cfg_path = os.path.join(customers_dir, slug, "config.json")
        if not os.path.isdir(os.path.join(customers_dir, slug)):
            continue
        if not os.path.exists(cfg_path):
            continue
        try:
            with open(cfg_path) as f:
                cfg = _j.load(f)
            status = _get_trial_status(slug)
            trial_end = cfg.get("trial_ends","")
            days_left = 0
            if status == "active" and trial_end:
                days_left = max(0, (_dt.fromisoformat(trial_end) - _dt.utcnow()).days)

            # Count items
            items = 0
            inv = os.path.join(customers_dir, slug, "inventory.csv")
            if os.path.exists(inv):
                with open(inv) as f:
                    items = max(0, sum(1 for _ in f) - 1)

            # Last active
            tdir = os.path.join(customers_dir, slug)
            mtimes = [os.path.getmtime(os.path.join(tdir, fn))
                      for fn in os.listdir(tdir)
                      if os.path.isfile(os.path.join(tdir, fn))]
            last_active = _dt.fromtimestamp(max(mtimes)).strftime("%Y-%m-%d %H:%M") if mtimes else ""

            stores.append({
                "slug":        slug,
                "store_name":  cfg.get("store_name", slug),
                "email":       cfg.get("contact_email",""),
                "plan":        cfg.get("plan","trial"),
                "status":      status,
                "days_left":   days_left,
                "items":       items,
                "created":     cfg.get("created_at","")[:10],
                "last_active": last_active,
                "mrr":         20.0 if cfg.get("plan") == "paid" else 0,
            })
        except Exception:
            continue
    return sorted(stores, key=lambda x: (x["plan"] != "paid", x["last_active"]), reverse=True)

# ── 7. Data Export ─────────────────────────────────────────────────────────────
@app.route("/settings/export-data")
def _export_tenant_data():
    if not session.get("logged_in"):
        return redirect("/login")
    if session.get("is_guest"):
        flash("Sign up to export your data.", "error")
        return redirect("/")
    slug = session.get("impersonating_slug") or session.get("store_slug")
    if not slug:
        abort(403)
    _audit("data_export", slug)
    customers_dir = os.path.join(
        os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"), "customers"
    )
    tenant_dir = os.path.join(customers_dir, slug)
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(tenant_dir):
            for fname in files:
                full = os.path.join(root, fname)
                arcname = os.path.relpath(full, tenant_dir)
                zf.write(full, arcname)
    buf.seek(0)
    safe_slug = _re.sub(r"[^a-z0-9\-]", "", slug)
    from flask import send_file as _sf
    return _sf(buf, mimetype="application/zip", as_attachment=True,
               download_name=f"{safe_slug}-data-export.zip")

# ── 8. Overseer Tenant Health API ────────────────────────────────────────────
@app.route("/overseer/tenant-health")
def _overseer_tenant_health():
    if session.get("role") != "overseer" and session.get("username") != "admin":
        abort(403)
    return jsonify(_get_tenant_health())

# ══════════════════════════════════════════════════════════════════════════════
# END MULTI-TENANT INFRASTRUCTURE
# ══════════════════════════════════════════════════════════════════════════════
def get_store_paths(slug=None):
    """Return data paths for a given client store slug, or Liberty Emporium if None."""
    if slug:
        base = os.path.join(CUSTOMERS_DIR, slug)
        return {
            'inventory': os.path.join(base, 'inventory.csv'),
            'uploads':   os.path.join(base, 'uploads'),
            'users':     os.path.join(base, 'users.json'),
            'config':    os.path.join(base, 'config.json'),
            'backups':   os.path.join(base, 'backups'),
        }
    return {
        'inventory': INVENTORY_FILE,
        'uploads':   UPLOAD_FOLDER,
        'users':     USERS_FILE,
        'config':    STORE_CONFIG_FILE,
        'backups':   BACKUP_FOLDER,
    }

def active_store_slug():
    """Returns the slug of the currently active client store, or None for Liberty Emporium."""
    return session.get('impersonating_slug') or session.get('store_slug') or None

def load_client_config(slug):
    """Load a client store's config.json. Returns None if not found."""
    cfg_path = os.path.join(CUSTOMERS_DIR, slug, 'config.json')
    if not os.path.exists(cfg_path):
        return None
    with open(cfg_path) as f:
        return json.load(f)

def save_client_config(slug, config):
    """Save a client store's config.json."""
    cfg_path = os.path.join(CUSTOMERS_DIR, slug, 'config.json')
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    with open(cfg_path, 'w') as f:
        json.dump(config, f, indent=2)

def list_client_stores():
    """Return list of all provisioned client store configs."""
    stores = []
    if not os.path.exists(CUSTOMERS_DIR):
        return stores
    for entry in os.listdir(CUSTOMERS_DIR):
        cfg_path = os.path.join(CUSTOMERS_DIR, entry, 'config.json')
        if os.path.isdir(os.path.join(CUSTOMERS_DIR, entry)) and os.path.exists(cfg_path):
            try:
                with open(cfg_path) as f:
                    stores.append(json.load(f))
            except Exception:
                pass
    stores.sort(key=lambda s: s.get('created_at', ''), reverse=True)
    return stores

# ── Store Configuration (white-label) ─────────────────────────────────────────
STORE_CONFIG_FILE = os.path.join(DATA_DIR, 'store_config.json')

DEFAULT_STORE_CONFIG = {
    'store_name': 'Alexander AI Inventory',
    'tagline': 'Inventory Management',
    'contact_email': 'alexanderjay70@gmail.com',
    'jay_email': 'alexanderjay70@gmail.com',
    'primary_color': '#2c3e50',
    'secondary_color': '#27ae60',
    'accent_color': '#4f46e5',
    'logo_url': '',  # empty = use emoji fallback
    'logo_emoji': '🏪',
    'store_description': 'AI-powered inventory management for modern stores.',
    # Pricing tiers (customizable per demo instance)
    'pricing': {
        'starter': {'name': 'Starter', 'price': 299, 'features': [
            'Full inventory management', 'AI photo analysis',
            'Ad & listing generators', 'Single device']},
        'pro': {'name': 'Pro', 'price': 499, 'features': [
            'Everything in Starter', 'Multi-device sync',
            'Square integration', 'Email support', 'Custom branding']},
        'enterprise': {'name': 'Enterprise', 'price': 799, 'features': [
            'Everything in Pro', 'Multiple locations',
            'API access', 'Priority support', 'Custom features']},
    },
    # Whether first-run onboarding has been completed
    'onboarding_done': False,
}

def load_store_config():
    """Load store configuration, returning defaults if file doesn't exist."""
    if os.path.exists(STORE_CONFIG_FILE):
        try:
            with open(STORE_CONFIG_FILE, 'r') as f:
                config = json.load(f)
            # Merge with defaults to ensure all keys exist
            for key, val in DEFAULT_STORE_CONFIG.items():
                if key not in config:
                    config[key] = val
            return config
        except Exception:
            pass
    # Save defaults if config doesn't exist
    save_store_config(DEFAULT_STORE_CONFIG.copy())
    return DEFAULT_STORE_CONFIG.copy()

def save_store_config(config):
    """Persist store configuration."""
    with open(STORE_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

# ── Config ────────────────────────────────────────────────────────────────────
_store_cfg = load_store_config()
STORE_NAME    = _store_cfg['store_name']
DEMO_MODE     = os.environ.get('DEMO_MODE', 'false').lower() == 'true'
CONTACT_EMAIL = os.environ.get('CONTACT_EMAIL', _store_cfg['contact_email'])
ALLOWED_EXT   = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
MAX_BACKUPS   = 20

CATEGORIES = ['Furniture','Electronics','Clothing','Jewelry','Home Decor',
              'Books','Kitchen','Toys','Tools','Collectibles','Art','Miscellaneous']
CONDITIONS = ['New','Like New','Good','Fair','Poor']
STATUSES   = ['Available','Sold','Reserved','Pending']

ADMIN_USER  = 'admin'
ADMIN_PASS  = os.environ.get('ADMIN_PASSWORD', 'admin123')
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', _store_cfg.get('jay_email', 'alexanderjay70@gmail.com'))

# ── Helpers ───────────────────────────────────────────────────────────────────
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def hash_password(pw):
    """Hash password with bcrypt. Returns bcrypt hash string."""
    return bcrypt.hashpw(pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def _is_sha256(h):
    """Detect legacy SHA-256 hash (64 hex chars)."""
    return len(h) == 64 and all(c in '0123456789abcdef' for c in h)

def check_password(pw, stored_hash):
    """Verify password against stored hash. Supports bcrypt and legacy SHA-256.
    Returns (is_valid, needs_upgrade).
    """
    if _is_sha256(stored_hash):
        return hashlib.sha256(pw.encode()).hexdigest() == stored_hash, True
    try:
        return bcrypt.checkpw(pw.encode('utf-8'), stored_hash.encode('utf-8')), False
    except Exception:
        return False, False

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE) as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

def load_pending():
    if not os.path.exists(PENDING_FILE):
        return []
    with open(PENDING_FILE) as f:
        return json.load(f)

def save_pending(pending):
    with open(PENDING_FILE, 'w') as f:
        json.dump(pending, f, indent=2)

def load_inventory():
    paths = get_store_paths(active_store_slug())
    inv_file = paths['inventory']
    uploads  = paths['uploads']
    if not os.path.exists(inv_file):
        return []
    with open(inv_file, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        products = list(reader)
    for p in products:
        imgs = [i.strip() for i in p.get('Images','').split(',') if i.strip()]
        p['image_list']   = imgs
        p['valid_images'] = [i for i in imgs if os.path.exists(os.path.join(uploads, i))]
    return products

def save_inventory(products):
    paths = get_store_paths(active_store_slug())
    inv_file = paths['inventory']
    os.makedirs(os.path.dirname(inv_file), exist_ok=True)
    fieldnames = ['SKU','Title','Description','Category','Condition','Price',
                  'Cost Paid','Status','Date Added','Images','Section','Shelf']
    _backup_inventory()
    with open(inv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(products)

def _backup_inventory():
    paths    = get_store_paths(active_store_slug())
    inv_file = paths['inventory']
    bak_dir  = paths['backups']
    if not os.path.exists(inv_file):
        return
    os.makedirs(bak_dir, exist_ok=True)
    ts  = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = os.path.join(bak_dir, f'inventory_{ts}.csv')
    shutil.copy2(inv_file, dst)
    backups = sorted(
        [f for f in os.listdir(bak_dir) if f.endswith('.csv')],
        reverse=True
    )
    for old in backups[MAX_BACKUPS:]:
        os.remove(os.path.join(bak_dir, old))

def get_stats():
    products    = load_inventory()
    pending     = load_pending()
    total_value = sum(float(p.get('Price') or 0) for p in products)
    return {
        'total':         len(products),
        'available':     sum(1 for p in products if p.get('Status') == 'Available'),
        'sold':          sum(1 for p in products if p.get('Status') == 'Sold'),
        'reserved':      sum(1 for p in products if p.get('Status') == 'Reserved'),
        'total_value':   total_value,
        'pending_users': len(load_pending()),
    }

def load_sale():
    if not os.path.exists(SALE_FILE):
        return {'active': False}
    with open(SALE_FILE) as f:
        return json.load(f)

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            slug = session.get('store_slug')
            if slug:
                return redirect(f'/store/{slug}/login')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('username') != ADMIN_USER:
            flash('Admin access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

def overseer_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        if session.get('role') != 'overseer':
            flash('Overseer access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

def client_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in') or not session.get('store_slug'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def ctx():
    return dict(
        store_name=STORE_NAME,
        demo_mode=DEMO_MODE,
        demo_contact_email=CONTACT_EMAIL,
        stats=get_stats(),
        demo_username=ADMIN_USER,
        demo_password=ADMIN_PASS,
        store_config=load_store_config(),
    )

# ── Context processor ─────────────────────────────────────────────────────────
@app.context_processor
def inject_globals():
    is_admin = session.get('username') == ADMIN_USER
    try:
        stats = get_stats()
    except Exception:
        stats = {'total': 0, 'available': 0, 'sold': 0, 'reserved': 0, 'total_value': 0, 'pending_users': 0}
    try:
        sale_state = load_sale()
    except Exception:
        sale_state = {'active': False}
    impersonating_slug = session.get('impersonating_slug')
    impersonating_store_name = None
    if impersonating_slug:
        cfg = load_client_config(impersonating_slug)
        if cfg:
            impersonating_store_name = cfg.get('store_name', impersonating_slug)
    user_role = session.get('role', 'overseer' if is_admin else 'guest')
    server_has_ai_key = bool(get_ai_api_key() or get_groq_api_key() or get_xai_api_key() or get_openrouter_api_key())
    return dict(
        store_name=STORE_NAME,
        demo_mode=DEMO_MODE,
        demo_contact_email=CONTACT_EMAIL,
        stats=stats,
        sale_state=sale_state,
        user_role=user_role,
        store_config=load_store_config(),
        impersonating_slug=impersonating_slug,
        impersonating_store_name=impersonating_store_name,
        server_has_ai_key=server_has_ai_key,
    )

# ── Health check (no login required, for Railway) ─────────────────────────────

@app.route('/health', methods=["GET", "HEAD"])
@app.route('/healthz', methods=["GET", "HEAD"])
@app.route('/health-check', methods=["GET", "HEAD"])
def health_check():
    try:
        db = get_db()
        db.execute("SELECT 1").fetchone()
        db_status = "ok"
    except Exception:
        db_status = "error"
    status = "ok" if db_status == "ok" else "degraded"
    response_body = json.dumps({"status": status, "db": db_status})
    code = 200 if status == "ok" else 503
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    }
    return response_body, code, headers

@app.route('/health2')
def health2():
    return json.dumps({"status": "ok", "version": "2026-04-14"}), 200, {"Content-Type": "application/json"}

@app.route('/ping')
def ping():
    """Deeper check — exercises data loading without login."""
    try:
        n = len(load_inventory())
        return f'ok inventory={n}', 200
    except Exception as e:
        return f'error: {e}', 500

# ── Auth Routes ───────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET','POST'])
@rate_limit
def login():
    # Already logged in? Redirect to appropriate dashboard
    if session.get('logged_in') and not session.get('is_guest'):
        if session.get('store_slug'):
            return redirect(url_for('my_store'))
        return redirect(url_for('dashboard'))
    # Rate limiting — 10 login attempts per minute per IP
    _ip = request.remote_addr or 'unknown'
    if _is_rate_limited(get_db(), f'login:{_ip}', max_calls=10, window_seconds=60):
        return jsonify({'error': 'Too many login attempts. Please wait 1 minute.'}), 429

    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        if username == ADMIN_USER and password == ADMIN_PASS:
            session['logged_in'] = True
            session['username']  = ADMIN_USER
            session['is_guest']  = False
            session['role']      = 'overseer'
            session.permanent    = True
            app.permanent_session_lifetime = datetime.timedelta(hours=8)
            flash('Welcome back, Admin!', 'success')
            return redirect(url_for('dashboard'))
        users = load_users()
        if username in users:
            valid, needs_upgrade = check_password(password, users[username].get('password', ''))
            if valid:
                if needs_upgrade:
                    users[username]['password'] = hash_password(password)
                    save_users(users)
                user_record = users[username]
                if user_record.get('status') == 'suspended':
                    flash('Your account has been suspended. Contact the administrator.', 'error')
                    return render_template('login.html', client_stores=list_client_stores(), **ctx())
                session['logged_in'] = True
                session['username']  = username
                session['is_guest']  = False
                session['role']      = user_record.get('role', '')
                session.permanent    = True
                app.permanent_session_lifetime = datetime.timedelta(hours=8)
                flash(f'Welcome, {username}!', 'success')
                return redirect(url_for('dashboard'))
        # Check client store users
        if os.path.exists(CUSTOMERS_DIR):
            for entry in os.listdir(CUSTOMERS_DIR):
                store_users_path = os.path.join(CUSTOMERS_DIR, entry, 'users.json')
                if not os.path.exists(store_users_path):
                    continue
                try:
                    with open(store_users_path) as f:
                        store_users = json.load(f)
                except Exception:
                    continue
                if username in store_users:
                    su = store_users[username]
                    valid, needs_upgrade = check_password(password, su.get('password', ''))
                    if valid:
                        if needs_upgrade:
                            store_users[username]['password'] = hash_password(password)
                            with open(store_users_path, 'w') as f:
                                json.dump(store_users, f, indent=2)
                        store_cfg_path = os.path.join(CUSTOMERS_DIR, entry, 'config.json')
                        store_name = entry
                        if os.path.exists(store_cfg_path):
                            try:
                                with open(store_cfg_path) as f:
                                    store_name = json.load(f).get('store_name', entry)
                            except Exception:
                                pass
                        if su.get('status') == 'suspended':
                            flash('Your store has been suspended. Contact support.', 'error')
                            return render_template('login.html', **ctx())
                        session['logged_in']  = True
                        session['username']   = username
                        session['is_guest']   = False
                        session['role']       = 'client'
                        session['store_slug'] = entry
                        session.permanent     = True
                        app.permanent_session_lifetime = datetime.timedelta(hours=8)
                        flash(f'Welcome to {store_name}!', 'success')
                        return redirect(url_for('my_store'))
        flash('Invalid username or password.', 'error')
    return render_template('login.html', client_stores=list_client_stores(), **ctx())

@app.route('/store/<slug>/login', methods=['GET', 'POST'])
@rate_limit
def store_login(slug):
    """Branded login page for a specific client store."""
    cfg = load_client_config(slug)
    if not cfg:
        flash('Store not found.', 'error')
        return redirect(url_for('login'))

    if session.get('logged_in') and session.get('store_slug') == slug:
        return redirect(url_for('my_store'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        users_path = os.path.join(CUSTOMERS_DIR, slug, 'users.json')
        if os.path.exists(users_path):
            try:
                with open(users_path) as f:
                    store_users = json.load(f)
            except Exception:
                store_users = {}
        else:
            store_users = {}

        valid, needs_upgrade = check_password(password, store_users.get(username, {}).get('password', ''))
        if username in store_users and valid:
            if needs_upgrade:
                store_users[username]['password'] = hash_password(password)
                with open(users_path, 'w') as f:
                    json.dump(store_users, f, indent=2)
            su = store_users[username]
            if su.get('status') == 'suspended':
                error = 'Your account has been suspended. Contact support.'
            elif cfg.get('status') == 'suspended':
                error = 'This store has been suspended. Contact support.'
            else:
                session['logged_in']  = True
                session['username']   = username
                session['email']      = username
                session['is_guest']   = False
                session['role']       = 'client'
                session['store_slug'] = slug
                session.permanent     = True
                app.permanent_session_lifetime = datetime.timedelta(hours=8)
                return redirect(url_for('my_store'))
        else:
            error = 'Invalid email or password.'

    return render_template('store_login.html', cfg=cfg, error=error, slug=slug)

@app.route('/store/<slug>/change-password', methods=['GET', 'POST'])
@rate_limit
def store_change_password(slug):
    """Allow a customer store user to change their password."""
    cfg = load_client_config(slug)
    if not cfg:
        return redirect(url_for('login'))

    error = None
    success = None

    if request.method == 'POST':
        username    = request.form.get('username', '').strip()
        current_pw  = request.form.get('current_password', '')
        new_pw      = request.form.get('new_password', '')
        confirm_pw  = request.form.get('confirm_password', '')

        users_path = os.path.join(CUSTOMERS_DIR, slug, 'users.json')
        try:
            with open(users_path) as f:
                store_users = json.load(f)
        except Exception:
            store_users = {}

        if username not in store_users:
            error = 'Email address not found.'
        elif not check_password(current_pw, store_users[username].get('password', ''))[0]:
            error = 'Current password is incorrect.'
        elif len(new_pw) < 6:
            error = 'New password must be at least 6 characters.'
        elif new_pw != confirm_pw:
            error = 'New passwords do not match.'
        else:
            store_users[username]['password'] = hash_password(new_pw)
            try:
                with open(users_path, 'w') as f:
                    json.dump(store_users, f, indent=2)
                success = 'Password updated successfully!'
            except Exception:
                error = 'Could not save. Please try again.'

    return render_template('store_change_password.html', cfg=cfg, slug=slug, error=error, success=success)

# In-memory reset tokens: { token: { 'slug': slug, 'username': username, 'expires': datetime } }
_reset_tokens = {}


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password_general():
    """General forgot password - finds store by email address."""
    import os, json, secrets as _sec, datetime as _dt
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        found_slug = None
        # Search all stores for this email
        if os.path.exists(CUSTOMERS_DIR):
            for slug in os.listdir(CUSTOMERS_DIR):
                cfg_path = os.path.join(CUSTOMERS_DIR, slug, 'config.json')
                upath    = os.path.join(CUSTOMERS_DIR, slug, 'users.json')
                # Check store contact email
                if os.path.exists(cfg_path):
                    try:
                        with open(cfg_path) as f2:
                            cfg = json.load(f2)
                        if cfg.get('contact_email','').lower() == email:
                            found_slug = slug; break
                    except: pass
                # Check users.json
                if os.path.exists(upath):
                    try:
                        with open(upath) as f2:
                            users = json.load(f2)
                        if email in users:
                            found_slug = slug; break
                    except: pass
        if found_slug:
            return redirect(url_for('store_forgot_password', slug=found_slug))
        flash('If that email is registered, a reset link has been sent.', 'info')
    return render_template('forgot_password_general.html')

@app.route('/store/<slug>/forgot-password', methods=['GET', 'POST'])
@rate_limit
def store_forgot_password(slug):
    cfg = load_client_config(slug)
    if not cfg:
        return redirect(url_for('login'))

    sent = False
    error = None

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        users_path = os.path.join(CUSTOMERS_DIR, slug, 'users.json')
        try:
            with open(users_path) as f:
                store_users = json.load(f)
        except Exception:
            store_users = {}

        # Always show "sent" message — don't reveal if email exists
        if email in store_users:
            token = str(uuid.uuid4())
            _reset_tokens[token] = {
                'slug': slug,
                'username': email,
                'expires': datetime.datetime.utcnow() + datetime.timedelta(hours=1)
            }
            reset_url = request.host_url.rstrip('/') + f'/store/{slug}/reset-password/{token}'
            body = (
                f"Hi,\n\n"
                f"You requested a password reset for {cfg.get('store_name', slug)}.\n\n"
                f"Click the link below to set a new password (valid for 1 hour):\n\n"
                f"{reset_url}\n\n"
                f"If you didn't request this, ignore this email.\n\n"
                f"— Alexander AI Integrated Solutions"
            )
            ok, err = send_smtp_email(email, f"Password Reset — {cfg.get('store_name', slug)}", body)
            if not ok:
                error = f"Could not send email: {err}"
            else:
                sent = True
        else:
            sent = True  # Don't reveal that email wasn't found

    return render_template('store_forgot_password.html', cfg=cfg, slug=slug, sent=sent, error=error)


@app.route('/store/<slug>/reset-password/<token>', methods=['GET', 'POST'])
@rate_limit
def store_reset_password(slug, token):
    cfg = load_client_config(slug)
    if not cfg:
        return redirect(url_for('login'))

    entry = _reset_tokens.get(token)
    expired = not entry or entry['expires'] < datetime.datetime.utcnow() or entry['slug'] != slug

    error = None
    success = None

    if not expired and request.method == 'POST':
        new_pw     = request.form.get('new_password', '')
        confirm_pw = request.form.get('confirm_password', '')
        if len(new_pw) < 6:
            error = 'Password must be at least 6 characters.'
        elif new_pw != confirm_pw:
            error = 'Passwords do not match.'
        else:
            users_path = os.path.join(CUSTOMERS_DIR, slug, 'users.json')
            try:
                with open(users_path) as f:
                    store_users = json.load(f)
                store_users[entry['username']]['password'] = hash_password(new_pw)
                with open(users_path, 'w') as f:
                    json.dump(store_users, f, indent=2)
                del _reset_tokens[token]
                success = 'Password updated! You can now sign in.'
            except Exception:
                error = 'Could not save. Please try again.'

    return render_template('store_reset_password.html', cfg=cfg, slug=slug,
                           expired=expired, error=error, success=success)

@app.route('/logout')
def logout():
    slug = session.get('store_slug')
    session.clear()
    if slug:
        return redirect(f'/store/{slug}/login')
    return redirect(url_for('login'))

@app.route('/guest')
def guest():
    session['logged_in'] = True
    session['username']  = 'guest'
    session['is_guest']  = True
    return redirect(url_for('dashboard'))

@app.route('/signup', methods=['GET','POST'])
@rate_limit
def signup():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        email    = request.form.get('email','').strip()
        password = request.form.get('password','')
        if not username or not password:
            flash('Username and password are required.', 'error')
        elif username == ADMIN_USER:
            flash('That username is reserved.', 'error')
        else:
            users   = load_users()
            pending = load_pending()
            if username in users or any(p['username'] == username for p in pending):
                flash('Username already exists or is pending.', 'error')
            else:
                pending.append({
                    'username':  username,
                    'email':     email,
                    'password':  hash_password(password),
                    'requested': datetime.date.today().isoformat()
                })
                save_pending(pending)
                flash('Account request submitted! Wait for admin approval.', 'success')
                return redirect(url_for('login'))
    return render_template('signup.html', **ctx())

# ── Sales Landing Page ────────────────────────────────────────────────────────
@app.route('/')
def sales_page():
    """Public sales/landing page - shown when not logged in"""
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))
    return render_template('sales_page.html', **ctx())

# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route('/dashboard')
@login_required
@_trial_gate
def dashboard():
    products = load_inventory()
    return render_template('dashboard.html', products=products, **ctx())

# ── Products ──────────────────────────────────────────────────────────────────
@app.route('/product/<sku>')
@login_required
def view_product(sku):
    products = load_inventory()
    product  = next((p for p in products if p['SKU'] == sku), None)
    if not product:
        flash('Product not found.', 'error')
        return redirect(url_for('dashboard'))
    return render_template('product.html', product=product, **ctx())

@app.route('/my-store/add', methods=['GET','POST'])
@rate_limit
@client_required
def client_new_product():
    """Add new product - client version"""
    if request.method == 'POST':
        sku = request.form.get('sku','').strip().upper()
        if not sku:
            flash('SKU is required.', 'error')
            return render_template('edit_with_ai.html', product={},
                                   categories=CATEGORIES, conditions=CONDITIONS,
                                   statuses=STATUSES, **ctx())
        products = load_inventory()
        if any(p['SKU'] == sku for p in products):
            flash('SKU already exists.', 'error')
            return render_template('edit_with_ai.html', product={},
                                   categories=CATEGORIES, conditions=CONDITIONS,
                                   statuses=STATUSES, **ctx())
        images = []
        for file in request.files.getlist('images'):
            if file and allowed_file(file.filename):
                ext      = file.filename.rsplit('.', 1)[1].lower()
                filename = f"{sku}_{uuid.uuid4().hex[:8]}.{ext}"
                uploads_dir = get_store_paths(active_store_slug())['uploads']
                os.makedirs(uploads_dir, exist_ok=True)
                file.save(os.path.join(uploads_dir, filename))
                images.append(filename)
        product = {
            'SKU':         sku,
            'Title':       request.form.get('title','').strip(),
            'Description': request.form.get('description','').strip(),
            'Category':    request.form.get('category','').strip(),
            'Condition':   request.form.get('condition','Good'),
            'Price':       request.form.get('price','0'),
            'Cost Paid':   '',
            'Status':      request.form.get('status','Available'),
            'Date Added':  datetime.date.today().isoformat(),
            'Images':      ','.join(images),
            'Section':     request.form.get('section','').strip(),
            'Shelf':       request.form.get('shelf','').strip(),
        }
        products.append(product)
        save_inventory(products)
        flash(f'Product {sku} created!', 'success')
        return redirect(url_for('my_store'))
    return render_template('edit_with_ai.html', product={},
                           categories=CATEGORIES, conditions=CONDITIONS,
                           statuses=STATUSES, **ctx())

@app.route('/new', methods=['GET','POST'])
@rate_limit
@login_required
def new_product():
    if session.get('is_guest'):
        flash('Guests cannot add products.', 'error')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        sku = request.form.get('sku','').strip().upper()
        if not sku:
            flash('SKU is required.', 'error')
            return render_template('edit_with_ai.html', product={},
                                   categories=CATEGORIES, conditions=CONDITIONS,
                                   statuses=STATUSES, **ctx())
        products = load_inventory()
        if any(p['SKU'] == sku for p in products):
            flash('SKU already exists.', 'error')
            return render_template('edit_with_ai.html', product={},
                                   categories=CATEGORIES, conditions=CONDITIONS,
                                   statuses=STATUSES, **ctx())
        images = []
        for file in request.files.getlist('images'):
            if file and allowed_file(file.filename):
                ext      = file.filename.rsplit('.', 1)[1].lower()
                filename = f"{sku}_{uuid.uuid4().hex[:8]}.{ext}"
                file.save(os.path.join(get_store_paths(active_store_slug())['uploads'], filename))
                images.append(filename)
        product = {
            'SKU':         sku,
            'Title':       request.form.get('title','').strip(),
            'Description': request.form.get('description','').strip(),
            'Category':    request.form.get('category','').strip(),
            'Condition':   request.form.get('condition','Good'),
            'Price':       request.form.get('price','0'),
            'Cost Paid':   request.form.get('cost_paid','') if session.get('username') == ADMIN_USER else '',
            'Status':      request.form.get('status','Available'),
            'Date Added':  datetime.date.today().isoformat(),
            'Images':      ','.join(images),
            'Section':     request.form.get('section','').strip(),
            'Shelf':       request.form.get('shelf','').strip(),
        }
        products.append(product)
        save_inventory(products)
        flash(f'Product {sku} created!', 'success')
        return redirect(url_for('view_product', sku=sku))
    return render_template('edit_with_ai.html', product={},
                           categories=CATEGORIES, conditions=CONDITIONS,
                           statuses=STATUSES, **ctx())

@app.route('/edit/<sku>', methods=['GET','POST'])
@rate_limit
@login_required
def edit_product(sku):
    if session.get('is_guest'):
        flash('Guests cannot edit products.', 'error')
        return redirect(url_for('dashboard'))
    products = load_inventory()
    idx      = next((i for i, p in enumerate(products) if p['SKU'] == sku), None)
    if idx is None:
        flash('Product not found.', 'error')
        return redirect(url_for('dashboard'))
    product = products[idx]
    if request.method == 'POST':
        for file in request.files.getlist('images'):
            if file and allowed_file(file.filename):
                ext      = file.filename.rsplit('.', 1)[1].lower()
                filename = f"{sku}_{uuid.uuid4().hex[:8]}.{ext}"
                uploads_dir = get_store_paths(active_store_slug())['uploads']
                os.makedirs(uploads_dir, exist_ok=True)
                file.save(os.path.join(uploads_dir, filename))
                existing = [i.strip() for i in product.get('Images','').split(',') if i.strip()]
                existing.append(filename)
                product['Images'] = ','.join(existing)
        product['Title']       = request.form.get('title', product['Title']).strip()
        product['Description'] = request.form.get('description', product.get('Description','')).strip()
        product['Category']    = request.form.get('category', product.get('Category','')).strip()
        product['Condition']   = request.form.get('condition', product.get('Condition','Good'))
        product['Price']       = request.form.get('price', product.get('Price','0'))
        product['Status']      = request.form.get('status', product.get('Status','Available'))
        product['Section']     = request.form.get('section', product.get('Section','')).strip()
        product['Shelf']       = request.form.get('shelf', product.get('Shelf','')).strip()
        if session.get('username') == ADMIN_USER:
            product['Cost Paid'] = request.form.get('cost_paid', product.get('Cost Paid',''))
        products[idx] = product
        save_inventory(products)
        flash('Product updated!', 'success')
        return redirect(url_for('view_product', sku=sku))
    return render_template('edit_with_ai.html', product=product,
                           categories=CATEGORIES, conditions=CONDITIONS,
                           statuses=STATUSES, **ctx())

@app.route('/confirm-delete/<sku>')
@login_required
def confirm_delete_product(sku):
    if session.get('is_guest'):
        flash('Guests cannot delete products.', 'error')
        return redirect(url_for('dashboard'))
    products = load_inventory()
    product = next((p for p in products if p['SKU'] == sku), None)
    if not product:
        flash('Product not found.', 'error')
        return redirect(url_for('dashboard'))
    return render_template('confirm_delete.html', product=product,
        delete_type='product', back_url=url_for('view_product', sku=sku), **ctx())

@app.route('/delete/<sku>', methods=['POST'])
@rate_limit
@login_required
def delete_product(sku):
    if session.get('is_guest'):
        flash('Guests cannot delete products.', 'error')
        return redirect(url_for('dashboard'))
    products = load_inventory()
    products = [p for p in products if p['SKU'] != sku]
    save_inventory(products)
    _audit('delete_product', details={'sku': sku})
    flash('Product deleted.', 'success')
    return redirect(url_for('dashboard'))

# ── Images ────────────────────────────────────────────────────────────────────
@app.route('/uploads/<filename>')
def serve_upload(filename):
    uploads_dir = get_store_paths(active_store_slug())['uploads']
    return send_from_directory(uploads_dir, filename)

@app.route('/confirm-delete-image/<sku>/<filename>')
@login_required
def confirm_delete_image(sku, filename):
    if session.get('is_guest'):
        flash('Guests cannot delete images.', 'error')
        return redirect(url_for('dashboard'))
    return render_template('confirm_delete.html', sku=sku, filename=filename,
        delete_type='image', back_url=url_for('edit_product', sku=sku), **ctx())

@app.route('/delete-image/<sku>', methods=['POST'])
@rate_limit
@login_required
def delete_image(sku):
    filename = request.form.get('filename')
    products = load_inventory()
    idx      = next((i for i, p in enumerate(products) if p['SKU'] == sku), None)
    if idx is not None and filename:
        imgs = [i.strip() for i in products[idx].get('Images','').split(',') if i.strip()]
        if filename in imgs:
            imgs.remove(filename)
            products[idx]['Images'] = ','.join(imgs)
            save_inventory(products)
            filepath = os.path.join(get_store_paths(active_store_slug())['uploads'], filename)
            if os.path.exists(filepath):
                os.remove(filepath)
    return redirect(url_for('edit_product', sku=sku))

@app.route('/edit-image/<sku>')
@login_required
def edit_image(sku):
    products = load_inventory()
    product  = next((p for p in products if p['SKU'] == sku), None)
    if not product:
        flash('Product not found.', 'error')
        return redirect(url_for('dashboard'))
    return render_template('image_editor.html', product=product, **ctx())

@app.route('/save-image/<sku>', methods=['POST'])
@rate_limit
@login_required
def save_image(sku):
    data       = request.json
    image_data = data.get('image_data','')
    filename   = data.get('filename','')
    if image_data and filename:
        header, encoded = image_data.split(',', 1)
        img_bytes = base64.b64decode(encoded)
        filepath  = os.path.join(get_store_paths(active_store_slug())['uploads'], filename)
        with open(filepath, 'wb') as f:
            f.write(img_bytes)
        return jsonify({'success': True})
    return jsonify({'success': False})

# ── AI Analysis ───────────────────────────────────────────────────────────────
@app.route('/ai-analyze', methods=['POST'])
@ai_rate_limit
@login_required
def ai_analyze():
    # Check which AI provider is available
    # Priority: Anthropic > Groq > xAI
    api_key = request.form.get('api_key', '').strip()
    ai_provider = request.form.get('ai_provider', '').strip()
    
    if not api_key:
        # Try to find an available API key
        api_key = get_ai_api_key()  # Anthropic
        ai_provider = 'anthropic'
        if not api_key:
            api_key = get_groq_api_key()  # Groq
            ai_provider = 'groq'
        if not api_key:
            api_key = get_xai_api_key()  # xAI/Grok
            ai_provider = 'xai'
        if not api_key:
            api_key = get_openrouter_api_key()  # OpenRouter
            ai_provider = 'openrouter'
    
    if not api_key:
        return jsonify({'error': 'No API key configured. Ask the admin to configure an AI API key in App Settings or Railway variables.'})
    
    # Override provider if user specified one
    if request.form.get('ai_provider'):
        ai_provider = request.form.get('ai_provider')
    file = request.files.get('image')
    if not file:
        return jsonify({'error': 'No image provided.'})
    img_bytes = file.read()

    # Re-encode via Pillow to ensure valid JPEG
    try:
        from PIL import Image as _Img
        import io as _io
        _pil = _Img.open(_io.BytesIO(img_bytes))
        try:
            from PIL import ExifTags as _ET
            exif = _pil._getexif()
            if exif:
                orient_key = next((k for k, v in _ET.TAGS.items() if v == 'Orientation'), None)
                if orient_key and orient_key in exif:
                    rot = {3:180, 6:270, 8:90}.get(exif[orient_key])
                    if rot:
                        _pil = _pil.rotate(rot, expand=True)
        except Exception:
            pass
        _pil = _pil.convert('RGB')
        if max(_pil.size) > 1600:
            _pil.thumbnail((1600, 1600), _Img.LANCZOS)
        buf = _io.BytesIO()
        _pil.save(buf, format='JPEG', quality=85)
        img_bytes = buf.getvalue()
    except Exception:
        pass

    img_b64      = base64.b64encode(img_bytes).decode('utf-8')
    content_type = 'image/jpeg'
    
    # Auto-detect provider based on API key prefix
    if not ai_provider:
        if api_key.startswith('sk-ant-'):
            ai_provider = 'anthropic'
        elif api_key.startswith('gsk_'):
            ai_provider = 'groq'
        elif api_key.startswith('xai-'):
            ai_provider = 'xai'
        elif api_key.startswith('sk-'):
            ai_provider = 'openrouter'  # OpenRouter keys start with sk-or-
        else:
            ai_provider = 'anthropic'  # default
    
    try:
        import urllib.request as ur
        import json as _json
        
        if ai_provider == 'anthropic':
            # Use Anthropic API
            payload = {
                'model': 'claude-haiku-4-5-20251001',
                'max_tokens': 1024,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'image', 'source': {'type': 'base64', 'media_type': content_type, 'data': img_b64}},
                        {'type': 'text', 'text': (
                            'Analyze this thrift store item photo. '
                            'Return JSON only with keys: title, category, condition, description, suggested_price, labels, objects. '
                            'condition must be one of: New, Like New, Good, Fair, Poor. '
                            'suggested_price is a number string like "24.99". '
                            'labels is a list of up to 5 descriptive tags. '
                            'objects is a list of up to 3 detected objects.'
                        )}
                    ]
                }]
            }
            req = ur.Request(
                'https://api.anthropic.com/v1/messages',
                data=_json.dumps(payload).encode(),
                headers={
                    'x-api-key': api_key,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json'
                }
            )
            with ur.urlopen(req, timeout=30) as resp:
                result = _json.loads(resp.read())
            text = result['content'][0]['text'].strip()
            if text.startswith('```'):
                text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
            parsed = _json.loads(text)
            usage = result.get('usage', {})
            parsed['_usage'] = {
                'input_tokens':  usage.get('input_tokens', 0),
                'output_tokens': usage.get('output_tokens', 0),
            }
            parsed['_provider'] = 'anthropic'
            return jsonify(parsed)
            
        elif ai_provider == 'groq':
            # Use Groq API (Vision)
            payload = {
                'model': 'llama-3.2-90b-vision-preview',
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'image_url', 'image_url': {'url': f'data:{content_type};base64,{img_b64}'}},
                        {'type': 'text', 'text': (
                            'Analyze this thrift store item photo. '
                            'Return JSON only with keys: title, category, condition, description, suggested_price, labels, objects. '
                            'condition must be one of: New, Like New, Good, Fair, Poor. '
                            'suggested_price is a number string like "24.99". '
                            'labels is a list of up to 5 descriptive tags. '
                            'objects is a list of up to 3 detected objects.'
                        )}
                    ]
                }],
                'temperature': 0.5,
                'max_tokens': 1024,
                'response_format': {'type': 'json_object'}
            }
            req = ur.Request(
                'https://api.groq.com/openai/v1/chat/completions',
                data=_json.dumps(payload).encode(),
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json'
                }
            )
            with ur.urlopen(req, timeout=30) as resp:
                result = _json.loads(resp.read())
            text = result['choices'][0]['message']['content'].strip()
            try:
                parsed = _json.loads(text)
            except:
                parsed = {'title': 'Item', 'category': 'Miscellaneous', 'condition': 'Good', 'description': text, 'suggested_price': '25.00', 'labels': [], 'objects': []}
            usage = result.get('usage', {})
            parsed['_usage'] = {
                'input_tokens':  usage.get('prompt_tokens', 0),
                'output_tokens':  usage.get('completion_tokens', 0),
            }
            parsed['_provider'] = 'groq'
            return jsonify(parsed)
            
        elif ai_provider == 'xai':
            # Use xAI Grok API
            payload = {
                'model': 'grok-2-vision-1212',
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'image_url', 'image_url': {'url': f'data:{content_type};base64,{img_b64}'}},
                        {'type': 'text', 'text': (
                            'Analyze this thrift store item photo. '
                            'Return JSON only with keys: title, category, condition, description, suggested_price, labels, objects. '
                            'condition must be one of: New, Like New, Good, Fair, Poor. '
                            'suggested_price is a number string like "24.99". '
                            'labels is a list of up to 5 descriptive tags. '
                            'objects is a list of up to 3 detected objects.'
                        )}
                    ]
                }],
                'temperature': 0.5,
                'max_tokens': 1024
            }
            req = ur.Request(
                'https://api.xai.com/v1/chat/completions',
                data=_json.dumps(payload).encode(),
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json'
                }
            )
            with ur.urlopen(req, timeout=30) as resp:
                result = _json.loads(resp.read())
            text = result['choices'][0]['message']['content'].strip()
            try:
                parsed = _json.loads(text)
            except:
                parsed = {'title': 'Item', 'category': 'Miscellaneous', 'condition': 'Good', 'description': text, 'suggested_price': '25.00', 'labels': [], 'objects': []}
            usage = result.get('usage', {})
            parsed['_usage'] = {
                'input_tokens':  usage.get('prompt_tokens', 0),
                'output_tokens':  usage.get('completion_tokens', 0),
            }
            parsed['_provider'] = 'xai'
            return jsonify(parsed)
            
        elif ai_provider == 'openrouter':
            # Use OpenRouter API (supports many vision models)
            payload = {
                'model': get_openrouter_model(),
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'image_url', 'image_url': {'url': f'data:{content_type};base64,{img_b64}'}},
                        {'type': 'text', 'text': (
                            'Analyze this thrift store item photo. '
                            'Return JSON only with keys: title, category, condition, description, suggested_price, labels, objects. '
                            'condition must be one of: New, Like New, Good, Fair, Poor. '
                            'suggested_price is a number string like "24.99". '
                            'labels is a list of up to 5 descriptive tags. '
                            'objects is a list of up to 3 detected objects.'
                        )}
                    ]
                }]
            }
            req = ur.Request(
                'https://openrouter.ai/api/v1/chat/completions',
                data=_json.dumps(payload).encode(),
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                    'HTTP-Referer': 'https://liberty-emporium.app',
                    'X-Title': 'Alexander AI Inventory'
                }
            )
            with ur.urlopen(req, timeout=30) as resp:
                result = _json.loads(resp.read())
            text = result['choices'][0]['message']['content'].strip()
            try:
                if text.startswith('```'):
                    text = text.split('```')[1]
                    if text.startswith('json'):
                        text = text[4:]
                parsed = _json.loads(text.strip())
            except:
                parsed = {'title': 'Item', 'category': 'Miscellaneous', 'condition': 'Good', 'description': text, 'suggested_price': '25.00', 'labels': [], 'objects': []}
            usage = result.get('usage', {})
            input_tok  = usage.get('prompt_tokens', 0)
            output_tok = usage.get('completion_tokens', 0)
            # If model returned 0 tokens it likely doesn't support vision or was unavailable
            if input_tok == 0 and output_tok == 0:
                model_used = get_openrouter_model()
                return jsonify({'error': f'Model "{model_used}" returned no response. It may not support image analysis. Please switch to Gemini Flash 1.5 or GPT-4o Mini in the AI Settings (⚙️ gear icon).'})
            parsed['_usage'] = {
                'input_tokens': input_tok,
                'output_tokens': output_tok,
            }
            parsed['_provider'] = 'openrouter'
            return jsonify(parsed)
            
    except Exception as e:
        return jsonify({'error': str(e)})

def generate_ad_copy(title, price, category, condition, description, api_key):
    """Call Claude to generate ad copy for one product. Returns dict with
    headline, selling_line, price_callout, tagline. Falls back to raw fields
    on any error."""
    fallback = {
        'headline':      title,
        'selling_line':  description[:80] if description else '',
        'price_callout': f'${price}',
        'tagline':       '125 W Swannanoa Ave · Liberty, NC',
    }
    if not api_key:
        return fallback

    prompt = (
        "You write ad copy for a retail store.\n"
        "Given this product, return ONLY a JSON object with these exact keys:\n"
        "- headline: punchy ad headline, max 6 words\n"
        "- selling_line: one benefit or descriptor, max 10 words\n"
        "- price_callout: price with excitement, max 5 words (e.g. \"Just $24!\")\n"
        "- tagline: short footer line, max 8 words\n\n"
        f"Product:\nTitle: {title}\nCategory: {category}\n"
        f"Condition: {condition}\nPrice: ${price}\nDescription: {description}"
    )

    payload = {
        'model': 'claude-haiku-4-5-20251001',
        'max_tokens': 200,
        'messages': [{'role': 'user', 'content': prompt}],
    }

    try:
        import urllib.request as _ur
        req = _ur.Request(
            'https://api.anthropic.com/v1/messages',
            data=json.dumps(payload).encode(),
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
            }
        )
        with _ur.urlopen(req, timeout=15) as resp:  # shorter timeout — called from threads
            result = json.loads(resp.read())
        text = result['content'][0]['text'].strip()
        # Strip markdown code fences if Claude wraps response
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        copy = json.loads(text)
        # Validate all keys present
        for key in ('headline', 'selling_line', 'price_callout', 'tagline'):
            if key not in copy:
                return fallback
        return copy
    except Exception:
        return fallback


# ── Ad Generator ──────────────────────────────────────────────────────────────
@app.route('/ads')
@login_required
def ad_generator():
    products = load_inventory()
    resp = make_response(render_template('ad_generator.html', products=products, **ctx()))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp

@app.route('/generate-ads', methods=['POST'])
@ai_rate_limit
@login_required
def generate_ads():
    """Generate AI-written JPEG picture ads, streaming results via SSE."""
    from PIL import Image as _Img, ImageDraw as _Draw, ImageFont as _Font
    import textwrap as _tw

    data     = request.get_json() or {}
    products = data.get('products', [])
    style    = data.get('style', 'elegant')
    fmt      = data.get('format', 'square')

    if not products:
        return jsonify({'error': 'No products provided.'}), 400

    api_key = get_ai_api_key()

    sizes = {
        'square':   (1080, 1080),
        'portrait': (1080, 1350),
        'story':    (1080, 1920),
    }
    W, H = sizes.get(fmt, (1080, 1080))

    palettes = {
        'elegant': {'grad': (10, 10, 28),   'accent': (240, 192, 64),  'text': (255, 255, 255), 'sub': (200, 200, 225)},
        'vivid':   {'grad': (170, 25, 25),  'accent': (255, 220, 60),  'text': (255, 255, 255), 'sub': (255, 195, 195)},
        'forest':  {'grad': (18, 55, 35),   'accent': (120, 210, 120), 'text': (235, 255, 235), 'sub': (165, 215, 165)},
        'ocean':   {'grad': (10, 38, 90),   'accent': (90, 195, 255),  'text': (255, 255, 255), 'sub': (155, 205, 255)},
        'warm':    {'grad': (72, 35, 8),    'accent': (255, 182, 65),  'text': (255, 248, 228), 'sub': (220, 188, 135)},
    }
    pal = palettes.get(style, palettes['elegant'])

    font_bold = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    font_reg  = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

    def _font(path, size):
        try:    return _Font.truetype(path, size)
        except Exception: return _Font.load_default()

    def _shadow(draw, text, x, y, font, color, off=3):
        draw.text((x + off, y + off), text, font=font, fill=(0, 0, 0, 130))
        draw.text((x, y), text, font=font, fill=color)

    # Resolve uploads path inside request context before spawning threads
    uploads_path = get_store_paths(active_store_slug())['uploads']

    results = {}   # sku -> result dict or Exception
    lock    = threading.Lock()

    def render_one(idx, p):
        sku   = p.get('sku', 'UNKNOWN')
        title = p.get('title', 'Untitled')
        try:
            desc      = (p.get('description', '') or '').strip()
            image_url = p.get('image', '')
            category  = p.get('category', '')
            condition = p.get('condition', '')
            try:
                pf = float(p.get('price', '0'))
                raw_price = f"{int(pf)}" if pf == int(pf) else f"{pf:.2f}"
            except Exception:
                raw_price = str(p.get('price', '0'))

            # Get AI copy (falls back to raw fields on error)
            copy = generate_ad_copy(title, raw_price, category, condition, desc, api_key)
            headline      = copy['headline']
            selling_line  = copy['selling_line']
            price_callout = copy['price_callout']
            tagline       = copy['tagline']

            r_g, g_g, b_g = pal['grad']
            canvas = _Img.new('RGB', (W, H), (r_g, g_g, b_g))

            # Product photo — fills top 55% of canvas, text area below stays clear
            photo_h = int(H * 0.55)
            if image_url:
                img_fname = image_url.split('/')[-1]
                img_fpath = os.path.join(uploads_path, img_fname)
                if os.path.exists(img_fpath):
                    try:
                        prod  = _Img.open(img_fpath)
                        prod  = fix_image_orientation(prod).convert('RGB')
                        scale = min(W / prod.width, photo_h / prod.height)
                        nw    = int(prod.width  * scale)
                        nh    = int(prod.height * scale)
                        prod  = prod.resize((nw, nh), _Img.LANCZOS)
                        px    = (W - nw) // 2
                        py    = (photo_h - nh) // 2
                        canvas.paste(prod, (px, py))
                    except Exception:
                        pass

            # Vignette — starts at 58% down so top half of photo stays clear
            vign = _Img.new('RGBA', (W, H), (0, 0, 0, 0))
            vd   = _Draw.Draw(vign)
            gs   = int(H * 0.58)
            for row in range(gs, H):
                t     = (row - gs) / (H - gs)
                alpha = int(252 * min(1.0, t ** 0.65))
                vd.line([(0, row), (W - 1, row)], fill=(r_g, g_g, b_g, alpha))
            canvas = _Img.alpha_composite(canvas.convert('RGBA'), vign).convert('RGB')

            draw    = _Draw.Draw(canvas)
            sz_headline = max(42, int(W * 0.052))
            sz_price    = max(62, int(W * 0.080))
            sz_desc     = max(22, int(W * 0.022))
            sz_cta      = max(20, int(W * 0.022))

            f_headline = _font(font_bold, sz_headline)
            f_price    = _font(font_bold, sz_price)
            f_desc     = _font(font_reg,  sz_desc)
            f_cta      = _font(font_reg,  sz_cta)

            accent = pal['accent']
            white  = pal['text']
            sub    = pal['sub']
            pad_x  = int(W * 0.06)

            y = H - int(H * 0.044)

            # Tagline + divider
            div_y = y - sz_cta - 14
            draw.line([(pad_x, div_y), (pad_x + int(W * 0.88), div_y)],
                      fill=tuple(max(0, int(c * 0.55)) for c in accent), width=2)
            draw.text((pad_x, y - sz_cta - 6), tagline, font=f_cta, fill=sub)
            y -= sz_cta + 22

            # Selling line
            if selling_line:
                chars_d = max(15, int((W * 0.88) / (sz_desc * 0.56)))
                d_lines = _tw.wrap(selling_line[:120], width=chars_d)[:1]
                for line in d_lines:
                    _shadow(draw, line, pad_x, y - sz_desc, f_desc, sub, off=2)
                    y -= sz_desc + 14

            # Price callout
            _shadow(draw, price_callout, pad_x, y - sz_price, f_price, white, off=4)
            y -= sz_price + 12

            # Headline
            chars_t = max(8, int((W * 0.88) / (sz_headline * 0.56)))
            t_lines = _tw.wrap(headline, width=chars_t)[:2]
            for line in reversed(t_lines):
                _shadow(draw, line, pad_x, y - sz_headline, f_headline, accent, off=3)
                y -= sz_headline + 6

            ts     = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            jfname = f'ad_{sku}_{style}_{fmt}_{ts}.jpg'
            canvas.convert('RGB').save(os.path.join(ADS_FOLDER, jfname), 'JPEG', quality=95)

            with lock:
                results[idx] = {'filename': jfname, 'product_title': title}
        except Exception as exc:
            with lock:
                results[idx] = {'filename': None, 'product_title': title, 'error': str(exc)}

    def stream():
        threads = []
        for idx, p in enumerate(products):
            t = threading.Thread(target=render_one, args=(idx, p), daemon=True)
            t.start()
            threads.append((idx, p.get('sku', 'UNKNOWN'), t))

        sent = set()
        while len(sent) < len(threads):
            made_progress = False
            for idx, sku, t in threads:
                if idx in sent:
                    continue
                with lock:
                    if idx in results:
                        r = results[idx]
                        sent.add(idx)
                        yield f"data: {json.dumps(r)}\n\n"
                        made_progress = True
            if not made_progress:
                time.sleep(0.05)

        yield "data: {\"done\": true}\n\n"

    return app.response_class(stream(), mimetype='text/event-stream',
                              headers={'X-Accel-Buffering': 'no',
                                       'Cache-Control': 'no-cache'})

@app.route('/ads/<filename>')
def view_ad(filename):
    return send_from_directory(ADS_FOLDER, filename)

@app.route('/download-ad/<filename>')
@login_required
def download_ad(filename):
    return send_from_directory(ADS_FOLDER, filename, as_attachment=True)

# ── Ad Vault ──────────────────────────────────────────────────────────────────
@app.route('/ad-vault')
@login_required
def ad_vault():
    """Browse and manage all saved picture ads."""
    ads = []
    if os.path.exists(ADS_FOLDER):
        for filename in os.listdir(ADS_FOLDER):
            if filename.lower().endswith('.jpg') or filename.lower().endswith('.jpeg'):
                filepath = os.path.join(ADS_FOLDER, filename)
                try:
                    mod_time = os.path.getmtime(filepath)
                    mod_date = datetime.datetime.fromtimestamp(mod_time).strftime('%b %d, %Y %H:%M')
                    ads.append({
                        'filename':     filename,
                        'display_name': filename.rsplit('.', 1)[0].replace('_', ' '),
                        'mod_date':     mod_date,
                        'mod_ts':       mod_time,
                    })
                except Exception:
                    pass
    ads.sort(key=lambda a: a['mod_ts'], reverse=True)
    return render_template('ad_vault.html', ads=ads, **ctx())

@app.route('/ad-vault/delete/<filename>', methods=['POST'])
@rate_limit
@login_required
def delete_ad(filename):
    if not (filename.lower().endswith('.jpg') or filename.lower().endswith('.jpeg')):
        return jsonify({'error': 'Invalid file type'}), 400
    filepath = os.path.join(ADS_FOLDER, filename)
    if os.path.exists(filepath) and os.path.isfile(filepath):
        try:
            os.remove(filepath)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'File not found'}), 404

# ── Listing Generator ─────────────────────────────────────────────────────────
@app.route('/listing-generator')
@login_required
def listing_generator():
    products = load_inventory()
    return render_template('listing_generator.html', products=products, **ctx())

@app.route('/rewrite-voice-script', methods=['POST'])
@rate_limit
@login_required
def rewrite_voice_script():
    """Use Claude to polish a rough voiceover script into natural narration."""
    draft = request.form.get('draft', '').strip()
    if not draft:
        return jsonify({'error': 'No draft text provided.'})

    api_key = get_ai_api_key()
    if not api_key:
        return jsonify({'error': 'No Anthropic API key configured.'})

    prompt = (
        f"Rewrite this draft text into a natural-sounding voiceover script for a thrift store video ad. "
        f"Use short sentences, conversational tone, contractions (e.g., 'don't' not 'do not'), "
        f"and friendly enthusiasm. Include the store name, product name, price, and condition. "
        f"Keep it under 80 words so it reads in about 20-25 seconds at normal speaking pace. "
        f"Return ONLY the rewritten script — no quotes, no preamble.\n\n"
        f"Draft:\n{draft}"
    )

    payload = {
        'model': 'claude-haiku-4-5-20251001',
        'max_tokens': 300,
        'messages': [{'role': 'user', 'content': prompt}],
    }

    try:
        import urllib.request
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=data,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
            }
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        rewritten = result['content'][0]['text'].strip()
        return jsonify({'script': rewritten})
    except Exception as e:
        return jsonify({'error': f'Rewrite failed: {str(e)}'})

@app.route('/generate-listing', methods=['POST'])
@rate_limit
@login_required
def generate_listing():
    data = request.get_json() or {}
    if not data:
        return jsonify({'error': 'No product data provided.'}), 400
    product   = data.get('product', {})
    platform  = data.get('platform', 'facebook')
    api_key   = get_ai_api_key()

    title     = product.get('title', '')
    price     = product.get('price', '0')
    category  = product.get('category', '')
    condition = product.get('condition', '')
    desc      = product.get('description', '')
    sku       = product.get('sku', '')

    store_info = 'Alexander AI Integrated Solutions'

    platform_prompts = {
        'facebook': (
            f'Write a Facebook Marketplace listing for this thrift store item. '
            f'Item: {title} '
            f'Price: ${price} '
            f'Condition: {condition} '
            f'Category: {category} '
            f'Description: {desc} '
            f'Store: {store_info} '
            f'Return JSON only with keys: title, price, condition, description, location. '
            f'Make the description engaging and friendly, 3-5 sentences.'
        ),
        'craigslist': (
            f'Write a Craigslist listing for this thrift store item. '
            f'Item: {title} '
            f'Price: ${price} '
            f'Condition: {condition} '
            f'Category: {category} '
            f'Description: {desc} '
            f'Store: {store_info} '
            f'Return JSON only with keys: title, price, condition, description, location. '
            f'Keep it straightforward and factual.'
        ),
        'instagram': (
            f'Write an Instagram caption for this thrift store item. '
            f'Item: {title} '
            f'Price: ${price} '
            f'Condition: {condition} '
            f'Description: {desc} '
            f'Store: {store_info} '
            f'Return JSON only with keys: title, price, condition, description, location. '
            f'Make description fun with emojis and relevant hashtags.'
        ),
    }

    prompt = platform_prompts.get(platform, platform_prompts['facebook'])

    if not api_key:
        fallback_desc = desc + '\n\n' + store_info
        return jsonify({'title': title, 'price': '$' + price, 'condition': condition,
                        'description': fallback_desc, 'location': 'Liberty, NC 27298'})
    try:
        import urllib.request as _ur
        import json as _json
        payload = {
            'model': 'claude-haiku-4-5-20251001',
            'max_tokens': 500,
            'messages': [{'role': 'user', 'content': prompt}]
        }
        req = _ur.Request(
            'https://api.anthropic.com/v1/messages',
            data=_json.dumps(payload).encode(),
            headers={
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            }
        )
        with _ur.urlopen(req, timeout=30) as resp:
            result = _json.loads(resp.read())
        text = result['content'][0]['text'].strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        return jsonify(_json.loads(text))
    except Exception as e:
        return jsonify({'error': str(e)})

# ── Export (LibertyE CSV) ─────────────────────────────────────────────────────
@app.route('/export')
@login_required
def export_inventory():
    return send_file(INVENTORY_FILE, as_attachment=True, download_name='inventory.csv')

# ── Export (Square CSV) ───────────────────────────────────────────────────────
@app.route('/export-square')
@login_required
@admin_required
def export_square():
    products = load_inventory()
    output   = io.StringIO()
    writer   = csv.writer(output)

    writer.writerow([
        'Token', 'Item Name', 'Customer-facing Name', 'Variation Name',
        'SKU', 'Description', 'Categories', 'Reporting Category',
        'GTIN', 'Item Type', 'Weight (lb)',
        'Social Media Link Title', 'Social Media Link Description',
        'Price', 'Online Sale Price', 'Archived',
        'Sellable', 'Contains Alcohol', 'Stockable',
        'Skip Detail Screen in POS',
        'Option Name 1', 'Option Value 1',
        'Current Quantity Liberty E Programs',
        'New Quantity Liberty E Programs',
        'Stock Alert Enabled Liberty E Programs',
        'Stock Alert Count Liberty E Programs'
    ])

    for p in products:
        title    = p.get('Title', '')
        sku      = p.get('SKU', '')
        desc     = p.get('Description', '')
        category = p.get('Category', '')
        price    = p.get('Price', '0.00')
        status   = p.get('Status', 'Available')
        archived = 'Y' if status in ('Sold', 'Reserved') else 'N'

        writer.writerow([
            '',           # Token
            title,        # Item Name
            title,        # Customer-facing Name
            'Regular',    # Variation Name
            sku,          # SKU
            desc,         # Description
            category,     # Categories
            '',           # Reporting Category
            '',           # GTIN
            'Physical',   # Item Type
            '',           # Weight
            '',           # Social Media Link Title
            '',           # Social Media Link Description
            price,        # Price
            '',           # Online Sale Price
            archived,     # Archived
            'Y',          # Sellable
            'N',          # Contains Alcohol
            'Y',          # Stockable
            'N',          # Skip Detail Screen in POS
            'Condition',  # Option Name 1  ← fixes Square "missing option set" error
            p.get('Condition', 'Good'),  # Option Value 1
            '',           # Current Quantity
            '',           # New Quantity
            'N',          # Stock Alert Enabled
            '',           # Stock Alert Count
        ])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='libertye_square_export.csv'
    )

# ── Import (Square CSV) ───────────────────────────────────────────────────────
@app.route('/import-square', methods=['GET', 'POST'])
@rate_limit
@login_required
@admin_required
def import_square():
    if request.method == 'POST':
        file = request.files.get('csv_file')
        if not file or not file.filename.endswith('.csv'):
            flash('Please upload a valid .csv file.', 'error')
            return redirect(url_for('import_square'))
        try:
            stream = file.read().decode('utf-8-sig')
            reader = csv.reader(stream.splitlines())
            rows   = list(reader)
        except Exception as e:
            flash(f'Could not read file: {e}', 'error')
            return redirect(url_for('import_square'))

        header_idx = None
        for i, row in enumerate(rows):
            if 'SKU' in row and 'Item Name' in row:
                header_idx = i
                break

        if header_idx is None:
            flash('Could not find a valid Square header row with "SKU" and "Item Name".', 'error')
            return redirect(url_for('import_square'))

        headers = rows[header_idx]

        def col(row, name):
            try:
                idx = headers.index(name)
                return row[idx].strip() if idx < len(row) else ''
            except ValueError:
                return ''

        existing_products = load_inventory()
        existing_skus     = {p['SKU'] for p in existing_products}
        imported  = 0
        skipped   = 0
        duplicate = 0
        errors    = []
        mode      = request.form.get('import_mode', 'skip')

        for row_num, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
            if not any(row):
                continue
            if row[0].strip().lower() in ('token', 'created by square', ''):
                continue

            sku   = col(row, 'SKU')
            title = col(row, 'Item Name') or col(row, 'Customer-facing Name')

            if not sku:
                errors.append(f'Row {row_num}: Missing SKU — skipped.')
                skipped += 1
                continue
            if not title:
                errors.append(f'Row {row_num}: Missing Item Name for SKU {sku} — skipped.')
                skipped += 1
                continue

            description = col(row, 'Description')
            category    = col(row, 'Categories').split(',')[0].strip() or 'Miscellaneous'
            price_raw   = col(row, 'Price')
            try:
                price = f"{float(price_raw):.2f}" if price_raw else '0.00'
            except ValueError:
                price = '0.00'

            archived = col(row, 'Archived').strip().upper()
            status   = 'Sold' if archived == 'Y' else 'Available'

            product = {
                'SKU':         sku,
                'Title':       title,
                'Description': description,
                'Category':    category,
                'Condition':   'Good',
                'Price':       price,
                'Cost Paid':   '',
                'Status':      status,
                'Date Added':  datetime.date.today().isoformat(),
                'Images':      '',
                'Section':     '',
                'Shelf':       '',
            }

            if sku in existing_skus:
                if mode == 'overwrite':
                    idx = next(i for i, p in enumerate(existing_products) if p['SKU'] == sku)
                    product['Images']    = existing_products[idx].get('Images', '')
                    product['Section']   = existing_products[idx].get('Section', '')
                    product['Shelf']     = existing_products[idx].get('Shelf', '')
                    product['Cost Paid'] = existing_products[idx].get('Cost Paid', '')
                    existing_products[idx] = product
                    duplicate += 1
                else:
                    errors.append(f'Row {row_num}: SKU {sku} already exists — skipped.')
                    duplicate += 1
                    continue
            else:
                existing_products.append(product)
                existing_skus.add(sku)
                imported += 1

        save_inventory(existing_products)
        flash(f'Import complete: {imported} new, {duplicate} duplicates, {skipped} skipped.', 'success')
        for e in errors[:10]:
            flash(e, 'error')
        return redirect(url_for('import_square'))

    return render_template('import_square.html', **ctx())

# ── Seasonal Sale ─────────────────────────────────────────────────────────────
@app.route('/seasonal-sale', methods=['GET','POST'])
@rate_limit
@login_required
@admin_required
def seasonal_sale():
    sale_state = load_sale()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'activate':
            try:
                discount_pct = int(request.form.get('discount_percent', '10'))
            except (ValueError, TypeError):
                discount_pct = 10
            sale_state = {
                'active':           True,
                'category':         request.form.get('category',''),
                'discount_percent': discount_pct
            }
        else:
            sale_state = {'active': False}
        with open(SALE_FILE, 'w') as f:
            json.dump(sale_state, f)
        flash('Sale settings updated!', 'success')
    return render_template('seasonal_sale.html', sale_state=sale_state,
                           categories=CATEGORIES, **ctx())

# ── Admin – Users ─────────────────────────────────────────────────────────────
@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users   = load_users()
    pending = load_pending()
    return render_template('admin_users.html', users=users, pending=pending, **ctx())

@app.route('/admin/approve/<username>', methods=['POST'])
@rate_limit
@login_required
@admin_required
def approve_user(username):
    pending = load_pending()
    user    = next((p for p in pending if p['username'] == username), None)
    if user:
        users = load_users()
        users[username] = {'password': user['password'], 'email': user.get('email','')}
        save_users(users)
        pending = [p for p in pending if p['username'] != username]
        save_pending(pending)
        flash(f'User {username} approved!', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/reject/<username>', methods=['POST'])
@rate_limit
@login_required
@admin_required
def reject_user(username):
    pending = [p for p in load_pending() if p['username'] != username]
    save_pending(pending)
    flash(f'User {username} rejected.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/remove/<username>', methods=['POST'])
@rate_limit
@login_required
@admin_required
def remove_user(username):
    users = load_users()
    users.pop(username, None)
    save_users(users)
    flash(f'User {username} removed.', 'success')
    return redirect(url_for('admin_users'))

# ── Admin – Backups ───────────────────────────────────────────────────────────

@app.route('/admin/add-user', methods=['POST'])
@rate_limit
@login_required
@admin_required
def add_user():
    username = request.form.get('username','').strip().lower()
    email    = request.form.get('email','').strip()
    password = request.form.get('password','')
    role     = request.form.get('role','user')
    if not username or not password:
        flash('Username and password are required.', 'error')
        return redirect(url_for('admin_users'))
    users = load_users()
    if username == ADMIN_USER or username in users:
        flash(f'Username "{username}" already exists.', 'error')
        return redirect(url_for('admin_users'))
    if len(password) < 6:
        flash('Password must be at least 6 characters.', 'error')
        return redirect(url_for('admin_users'))
    users[username] = {
        'password': hash_password(password),
        'email': email,
        'role': role,
        'joined': datetime.date.today().isoformat(),
        'status': 'active'
    }
    save_users(users)
    flash(f'User "{username}" created successfully!', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/edit-user', methods=['POST'])
@rate_limit
@login_required
@admin_required
def edit_user():
    username = request.form.get('username','').strip()
    email    = request.form.get('email','').strip()
    role     = request.form.get('role','user')
    users = load_users()
    if username in users:
        users[username]['email'] = email
        users[username]['role']  = role
        save_users(users)
        flash(f'User "{username}" updated.', 'success')
    else:
        flash('User not found.', 'error')
    return redirect(url_for('admin_users'))

@app.route('/admin/reset-password', methods=['POST'])
@rate_limit
@login_required
@admin_required
def admin_reset_password():
    username     = request.form.get('username','').strip()
    new_password = request.form.get('new_password','')
    confirm      = request.form.get('confirm_password','')
    if new_password != confirm:
        flash('Passwords do not match.', 'error')
        return redirect(url_for('admin_users'))
    if len(new_password) < 6:
        flash('Password must be at least 6 characters.', 'error')
        return redirect(url_for('admin_users'))
    users = load_users()
    if username in users:
        users[username]['password'] = hash_password(new_password)
        save_users(users)
        flash(f'Password reset for "{username}".', 'success')
    else:
        flash('User not found.', 'error')
    return redirect(url_for('admin_users'))

@app.route('/admin/suspend/<username>', methods=['POST'])
@rate_limit
@login_required
@admin_required
def suspend_user(username):
    users = load_users()
    if username in users:
        users[username]['status'] = 'suspended'
        save_users(users)
        flash(f'User "{username}" suspended.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/unsuspend/<username>', methods=['POST'])
@rate_limit
@login_required
@admin_required
def unsuspend_user(username):
    users = load_users()
    if username in users:
        users[username]['status'] = 'active'
        save_users(users)
        flash(f'User "{username}" restored.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/backups')
@login_required
@admin_required
def admin_backups():
    files   = sorted(os.listdir(BACKUP_FOLDER), reverse=True)
    backups = []
    for f in files:
        if f.endswith('.csv'):
            path = os.path.join(BACKUP_FOLDER, f)
            stat = os.stat(path)
            backups.append({
                'filename': f,
                'size':     stat.st_size,
                'modified': datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
            })
    return render_template('admin_backups.html', backups=backups, **ctx())

@app.route('/admin/backups/download/<filename>')
@login_required
@admin_required
def download_backup(filename):
    return send_from_directory(BACKUP_FOLDER, filename, as_attachment=True)

@app.route('/admin/backups/restore/<filename>', methods=['POST'])
@rate_limit
@login_required
@admin_required
def restore_backup(filename):
    src = os.path.join(BACKUP_FOLDER, filename)
    if os.path.exists(src):
        shutil.copy2(src, INVENTORY_FILE)
        flash(f'Inventory restored from {filename}!', 'success')
    return redirect(url_for('admin_backups'))

@app.route('/admin/backups/manual', methods=['POST'])
@rate_limit
@login_required
@admin_required
def manual_backup():
    _backup_inventory()
    flash('Manual backup created!', 'success')
    return redirect(url_for('admin_backups'))

# ── Debug ─────────────────────────────────────────────────────────────────────
@app.route('/debug')
@login_required
@admin_required
def debug():
    info = {
        'store_name':        STORE_NAME,
        'base_dir':          BASE_DIR,
        'inventory_file':    INVENTORY_FILE,
        'inventory_exists':  os.path.exists(INVENTORY_FILE),
        'upload_folder':     UPLOAD_FOLDER,
        'anthropic_key_set': bool(get_ai_api_key()),
        'demo_mode':         DEMO_MODE,
        'python_version':    __import__('sys').version,
    }
    return jsonify(info)

# ── Contact ───────────────────────────────────────────────────────────────────
@app.route('/contact')
def contact():
    return render_template('jay_resume.html')

# ── Price Tag ─────────────────────────────────────────────────────────────────
@app.route('/price-tag/<sku>')
@login_required
def price_tag(sku):
    products = load_inventory()
    product  = next((p for p in products if p['SKU'] == sku), None)
    if not product:
        flash('Product not found.', 'error')
        return redirect(url_for('dashboard'))
    return render_template('price_tag.html', product=product, **ctx())

# ── Store Configuration (white-label admin) ───────────────────────────────────
# ── App Settings (single API key for all AI features) ─────────────────────────
@app.route('/admin/settings', methods=['GET','POST'])
@rate_limit
@login_required
@admin_required
def admin_settings():
    key = get_ai_api_key()
    masked = ''
    if key:
        # Show only first 8 + last 4 with fixed 10 dots in middle
        masked = f"{key[:8]}{'•' * 10}{key[-4:]}"
    if request.method == 'POST':
        new_key = request.form.get('anthropic_api_key', '').strip()
        if new_key:
            save_ai_api_key(new_key)
            flash('AI API key updated! All AI features now use this key.', 'success')
        elif request.form.get('clear_key') == '1':
            save_ai_api_key('')
            flash('AI API key cleared.', 'warning')
        return redirect(url_for('admin_settings'))
    return render_template('admin_settings.html',
        masked_key=masked, has_key=bool(key),
        stripe_secret_key=get_stripe_keys()[0],
        stripe_public_key=get_stripe_keys()[1],
        smtp_config=get_smtp_config(),
        **ctx())

@app.route('/my-settings', methods=['GET', 'POST'])
@login_required
def my_settings():
    """Per-user AI API key settings"""
    user_id = session.get('user_id')
    db = get_db()
    if request.method == 'POST':
        groq_key = request.form.get('groq_key', '').strip()
        openrouter_key = request.form.get('openrouter_key', '').strip()
        anthropic_key = request.form.get('anthropic_key', '').strip()
        xai_key = request.form.get('xai_key', '').strip()
        active_provider = request.form.get('active_provider', 'openrouter')
        db.execute('''INSERT OR REPLACE INTO user_api_keys
            (user_id, groq_key, openrouter_key, anthropic_key, xai_key, active_provider, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)''',
            (user_id, groq_key, openrouter_key, anthropic_key, xai_key, active_provider))
        db.commit()
        # Save Telegram config to client config
        slug = session.get('impersonating_slug') or session.get('store_slug')
        if slug:
            telegram_bot_token = request.form.get('telegram_bot_token', '').strip()
            telegram_chat_id   = request.form.get('telegram_chat_id', '').strip()
            cfg = load_client_config(slug) or {}
            if telegram_bot_token: cfg['telegram_bot_token'] = telegram_bot_token
            if telegram_chat_id:   cfg['telegram_chat_id']   = telegram_chat_id
            save_client_config(slug, cfg)
        flash('Your AI settings saved!', 'success')
        return redirect(url_for('my_settings'))
    row = db.execute('SELECT * FROM user_api_keys WHERE user_id = ?', (user_id,)).fetchone()
    keys = dict(row) if row else {'groq_key': '', 'openrouter_key': '', 'anthropic_key': '', 'xai_key': '', 'active_provider': 'openrouter'}
    for k in ['groq_key', 'openrouter_key', 'anthropic_key', 'xai_key']:
        if keys.get(k):
            keys[k] = keys[k][:8] + '...'
    slug = session.get('impersonating_slug') or session.get('store_slug')
    client_config = load_client_config(slug) if slug else {}
    return render_template('my_settings.html', keys=keys, client_config=client_config, **ctx())

@app.route('/admin/settings/stripe', methods=['POST'])
@rate_limit
@login_required
@admin_required
def admin_settings_stripe():
    clear = request.form.get('clear_stripe') == '1'
    if clear:
        save_stripe_keys('', '')
        flash('Stripe keys cleared.', 'warning')
    else:
        pub = request.form.get('stripe_public_key', '').strip()
        sec = request.form.get('stripe_secret_key', '').strip()
        save_stripe_keys(sec or None, pub or None)
        flash('Stripe keys saved! Payments are now enabled.', 'success')
    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/smtp', methods=['POST'])
@rate_limit
@login_required
@admin_required
def admin_settings_smtp():
    host     = request.form.get('smtp_host', '').strip()
    port     = request.form.get('smtp_port', '587').strip()
    user     = request.form.get('smtp_user', '').strip()
    password = request.form.get('smtp_password', '').strip()
    save_smtp_config(host, int(port) if port.isdigit() else 587, user, password)
    flash('Email (SMTP) settings saved.', 'success')
    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/smtp/test', methods=['POST'])
@rate_limit
@login_required
@admin_required
def admin_settings_smtp_test():
    cfg = get_smtp_config()
    ok, err = send_smtp_email(
        to=cfg['smtp_user'],
        subject='Alexander AI Inventory — SMTP Test',
        body='Your email settings are working correctly.'
    )
    if ok:
        flash('Test email sent successfully!', 'success')
    else:
        flash(f'Email failed: {err}', 'error')
    return redirect(url_for('admin_settings'))

@app.route('/admin/branding', methods=['GET','POST'])
@rate_limit
@login_required
@admin_required
def admin_branding():
    cfg = load_store_config()
    if request.method == 'POST':
        cfg['store_name']   = request.form.get('store_name', cfg['store_name']).strip()
        cfg['tagline']      = request.form.get('tagline', cfg['tagline']).strip()
        cfg['contact_email'] = request.form.get('contact_email', cfg['contact_email']).strip()
        cfg['store_description'] = request.form.get('store_description', cfg['store_description']).strip()
        cfg['primary_color']   = request.form.get('primary_color', cfg['primary_color']).strip()
        cfg['secondary_color'] = request.form.get('secondary_color', cfg['secondary_color']).strip()
        cfg['accent_color']    = request.form.get('accent_color', cfg['accent_color']).strip()
        cfg['logo_emoji']      = request.form.get('logo_emoji', cfg['logo_emoji']).strip()
        save_store_config(cfg)
        flash('Store branding updated! Refresh to see changes.', 'success')
        return redirect(url_for('admin_branding'))
    return render_template('admin_branding.html', config=cfg, **ctx())

# ── Onboarding Wizard ────────────────────────────────────────────────────────
@app.route('/onboarding', methods=['GET','POST'])
@rate_limit
def onboarding():
    cfg = load_store_config()
    # If onboarding already done and user is logged in, redirect to dashboard
    if cfg.get('onboarding_done') and session.get('logged_in'):
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        # Save onboarding responses
        cfg['store_name']   = request.form.get('store_name', cfg['store_name']).strip()
        cfg['tagline']      = request.form.get('tagline', cfg['tagline']).strip()
        cfg['contact_email'] = request.form.get('contact_email', cfg['contact_email']).strip()
        cfg['primary_color']   = request.form.get('primary_color', cfg['primary_color']).strip()
        cfg['accent_color']    = request.form.get('accent_color', cfg['accent_color']).strip()
        cfg['logo_emoji']      = request.form.get('logo_emoji', cfg['logo_emoji']).strip()
        cfg['onboarding_done'] = True
        save_store_config(cfg)
        flash(f"Welcome to {cfg['store_name']}! Your store is ready to go. 🎉", 'success')
        return redirect(url_for('dashboard'))
    return render_template('onboarding.html', config=cfg, **ctx())

# ── Sample Products by Industry ──────────────────────────────────────────────
SAMPLE_PRODUCTS_FILE = os.path.join(BASE_DIR, 'sample_products.json')

def load_sample_products(industry='general'):
    """Load industry-specific sample products for customer demos."""
    if not os.path.exists(SAMPLE_PRODUCTS_FILE):
        return []
    
    try:
        with open(SAMPLE_PRODUCTS_FILE) as f:
            all_products = json.load(f)
    except:
        return []
    
    # Return the selected industry's products, or general as fallback
    if industry in all_products and all_products[industry]:
        return all_products[industry]
    return all_products.get('general', [])

# ── Landing Page ─────────────────────────────────────────────────────────────
@app.route('/landing')
def landing_page():
    """Public landing page for advertising — no login needed"""
    return render_template('landing.html')

# ── Wizard Routes ────────────────────────────────────────────────────────────
os.makedirs(CUSTOMERS_DIR, exist_ok=True)

def load_leads():
    """Load all customer leads from JSON file."""
    leads_file = os.path.join(CUSTOMERS_DIR, 'leads.json')
    if os.path.exists(leads_file):
        with open(leads_file) as f:
            return json.load(f)
    return []

def save_leads(leads):
    """Save all customer leads."""
    leads_file = os.path.join(CUSTOMERS_DIR, 'leads.json')
    with open(leads_file, 'w') as f:
        json.dump(leads, f, indent=2)

def slugify(text):
    """Convert text to URL-safe slug."""
    import re
    slug = text.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'\s+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')

@app.route('/wizard')
def wizard():
    """Multi-step wizard to get the app."""
    return render_template('wizard.html')

@app.route('/wizard-submit', methods=['POST'])
@rate_limit
def wizard_submit():
    """Handle wizard submission — create customer store with auto-provisioned demo data."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400
    
    store_name = data.get('store_name', '').strip()
    slug = slugify(data.get('store_name', 'unnamed-store'))
    
    # Ensure slug is unique
    base_slug = slug
    counter = 1
    customer_file = os.path.join(CUSTOMERS_DIR, f'{slug}.json')
    while os.path.exists(customer_file):
        slug = f'{base_slug}-{counter}'
        customer_file = os.path.join(CUSTOMERS_DIR, f'{slug}.json')
        counter += 1
    
    # Load industry-specific sample products
    industry = data.get('industry', 'general')
    sample_products = load_sample_products(industry)
    
    # Save customer config
    config = {
        'store_name': store_name,
        'slug': slug,
        'primary_color': data.get('color', '#2e7d6e'),
        'industry': industry,
        'tagline': data.get('tagline', ''),
        'contact_name': data.get('contact_name', ''),
        'contact_email': data.get('contact_email', ''),
        'contact_phone': data.get('contact_phone', ''),
        'created_at': datetime.datetime.now().isoformat(),
        'sample_products': sample_products,
    }
    
    with open(customer_file, 'w') as f:
        json.dump(config, f, indent=2)
    
    # === ALSO SAVE TO config.json FOR LOGIN TO WORK ===
    customer_dir = os.path.join(CUSTOMERS_DIR, slug)
    os.makedirs(customer_dir, exist_ok=True)
    with open(os.path.join(customer_dir, 'config.json'), 'w') as f2:
        json.dump(config, f2, indent=2)
    # =================================================
    
    # === CREATE CLIENT USER ACCOUNT ===
    # This is the bug fix - create users.json so client can login
    contact_email = data.get('contact_email', '').strip()
    temp_password = data.get('temp_password', '').strip()
    
    if contact_email and temp_password:
        import hashlib
        customer_dir = os.path.join(CUSTOMERS_DIR, slug)
        os.makedirs(customer_dir, exist_ok=True)
        
        users_data = {
            contact_email: {
                'password': hashlib.sha256(temp_password.encode()).hexdigest(),
                'role': 'client',
                'name': data.get('contact_name', ''),
                'email': contact_email,
                'created': datetime.datetime.now().isoformat()
            }
        }
        
        users_file = os.path.join(customer_dir, 'users.json')
        with open(users_file, 'w') as f:
            json.dump(users_data, f, indent=2)
    # === END USER CREATION ===
    
    # Add to leads
    leads = load_leads()
    leads.append(config)
    save_leads(leads)
    
    # NOTE: If Jay sets up a sendgrid/Mailgun API key later, we can auto-send him
    # an email notification here. For now it's in the leads file.
    
    return jsonify({
        'url': f'/store/{slug}',
        'store_name': store_name,
        'product_count': len(sample_products),
    })

@app.route('/store/<slug>')
def customer_store(slug):
    """Render a customer's branded store demo with sample inventory."""
    config_file = os.path.join(CUSTOMERS_DIR, f'{slug}.json')
    
    if not os.path.exists(config_file):
        flash('Store not found. Start your own demo!', 'info')
        return redirect(url_for('wizard'))
    
    with open(config_file) as f:
        config = json.load(f)
    
    sample_products = config.get('sample_products') or load_sample_products(config.get('industry', 'general'))

    # Parse JSON string fields so templates get proper Python objects
    raw_gallery = config.get('gallery_images', '[]')
    if isinstance(raw_gallery, str):
        try: config['gallery_images'] = json.loads(raw_gallery)
        except: config['gallery_images'] = []

    raw_hours = config.get('business_hours', '{}')
    if isinstance(raw_hours, str):
        try: config['business_hours'] = json.loads(raw_hours)
        except: config['business_hours'] = {}

    return render_template('store_page.html',
        config=config,
        tagline=config.get('tagline', ''),
        sample_products=sample_products,
        **ctx())

# ── Payment Routes ───────────────────────────────────────────────────────────
@app.route('/pay/<plan>')
def payment_plan(plan):
    """Legacy route — redirect to trial signup."""
    return redirect(url_for('start_trial'))

@app.route('/start-trial', methods=['GET', 'POST'])
@rate_limit
def start_trial():
    """Provision a real client store for the trial signup and log them in."""
    if request.method == 'POST':
        store_name    = request.form.get('store_name', '').strip()
        contact_email = request.form.get('contact_email', '').strip()
        contact_name  = request.form.get('contact_name', '').strip()
        slug          = request.form.get('slug', '').strip() or slugify(store_name)
        primary_color = request.form.get('color', '#2e7d6e').strip()
        industry      = request.form.get('industry', 'general').strip()
        tagline       = request.form.get('tagline', '').strip()

        if not store_name or not contact_email:
            flash('Store name and email are required.', 'error')
            return redirect(url_for('wizard'))

        # Ensure slug is unique as a directory
        base_slug = slug
        counter = 1
        while os.path.exists(os.path.join(CUSTOMERS_DIR, slug)):
            slug = f'{base_slug}-{counter}'
            counter += 1

        # Provision real store directory (same as overseer_create_client)
        store_dir = os.path.join(CUSTOMERS_DIR, slug)
        os.makedirs(os.path.join(store_dir, 'uploads'), exist_ok=True)
        os.makedirs(os.path.join(store_dir, 'backups'), exist_ok=True)

        trial_start = datetime.datetime.now().isoformat()
        trial_end   = (datetime.datetime.now() + datetime.timedelta(days=14)).isoformat()

        config = {
            'store_name':    store_name,
            'slug':          slug,
            'primary_color': primary_color,
            'industry':      industry,
            'tagline':       tagline,
            'plan':          'trial',
            'status':        'active',
            'contact_name':  contact_name,
            'contact_email': contact_email,
            'trial_start':   trial_start,
            'trial_end':     trial_end,
            'created_at':    trial_start,
        }
        save_client_config(slug, config)

        # Empty inventory
        inv_path = os.path.join(store_dir, 'inventory.csv')
        fieldnames = ['SKU','Title','Description','Category','Condition','Price',
                      'Cost Paid','Status','Date Added','Images','Section','Shelf']
        with open(inv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()

        # Generate temp password and create login
        import secrets as _sec
        temp_password = _sec.token_urlsafe(8)
        users_path = os.path.join(store_dir, 'users.json')
        users = {
            contact_email: {
                'password':   hash_password(temp_password),
                'role':       'client',
                'store_slug': slug,
                'created_at': trial_start,
            }
        }
        with open(users_path, 'w') as f:
            json.dump(users, f, indent=2)

        # Save trial lead so Jay can follow up
        leads = load_leads()
        leads.append({
            'store_name':    store_name,
            'contact_email': contact_email,
            'contact_name':  contact_name,
            'slug':          slug,
            'trial_start':   trial_start,
            'trial_end':     trial_end,
            'type':          'trial',
            'status':        'active',
            'created_at':    trial_start,
        })
        save_leads(leads)

        # Queue 14-day onboarding email sequence
        try:
            app_url = request.host_url.rstrip('/')
            queue_onboarding_sequence(
                email=contact_email,
                name=contact_name or store_name,
                store_name=store_name,
                slug=slug,
                temp_password=temp_password,
                app_url=app_url
            )
            app.logger.info(f"ONBOARDING_SEQUENCE_QUEUED: {contact_email}")
            track('trial.signup', slug=slug)
        except Exception as e:
            app.logger.error(f"ONBOARDING_QUEUE_FAILED: {e}")

        # Log them in automatically
        session.clear()
        session['logged_in']  = True
        session['email']      = contact_email
        session['role']       = 'client'
        session['store_slug'] = slug

        flash(f'Welcome to your store! Your login is {contact_email} / {temp_password} — save this to log in next time.', 'success')
        return redirect(url_for('dashboard'))
    return redirect(url_for('wizard'))

@app.route('/pay-success')
def payment_success():
    """Thank you page after payment."""
    return render_template('payment_success.html')

@app.route('/admin/leads')
@login_required
@admin_required
def admin_leads():
    """Admin page showing all customer leads."""
    leads = load_leads()
    # Sort newest first
    leads.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return render_template('admin_leads.html', leads=leads, **ctx())

# ── Overseer Routes ───────────────────────────────────────────────────────────
@app.route('/overseer')
@login_required
@overseer_required
def overseer_dashboard():
    # Use enhanced tenant health data
    stores = _get_tenant_health() or list_client_stores()
    total_revenue   = sum(s.get('mrr', 0) for s in stores)
    active_count    = sum(1 for s in stores if s.get('status') == 'active')
    paid_count      = sum(1 for s in stores if s.get('plan') == 'paid')
    expired_count   = sum(1 for s in stores if s.get('status') == 'expired')
    suspended_count = sum(1 for s in stores if s.get('status') == 'suspended')
    return render_template('overseer_dashboard.html',
        stores=stores,
        total_revenue=total_revenue,
        active_count=active_count,
        paid_count=paid_count,
        expired_count=expired_count,
        suspended_count=suspended_count,
        **ctx()
    )

@app.route('/overseer/client/<slug>')
@login_required
@overseer_required
def overseer_client_detail(slug):
    cfg = load_client_config(slug)
    if not cfg:
        flash('Client store not found.', 'error')
        return redirect(url_for('overseer_dashboard'))
    inv_file = os.path.join(CUSTOMERS_DIR, slug, 'inventory.csv')
    product_count = 0
    if os.path.exists(inv_file):
        with open(inv_file, newline='', encoding='utf-8') as f:
            product_count = sum(1 for _ in csv.DictReader(f))
    return render_template('overseer_client.html',
        client=cfg,
        slug=slug,
        product_count=product_count,
        **ctx()
    )

@app.route('/overseer/client/create', methods=['POST'])
@rate_limit
@login_required
@overseer_required
def overseer_create_client():
    store_name    = request.form.get('store_name', '').strip()
    contact_email = request.form.get('contact_email', '').strip()
    temp_password = request.form.get('temp_password', '').strip()
    plan          = request.form.get('plan', 'starter')
    industry      = request.form.get('industry', 'general')
    primary_color = request.form.get('primary_color', '#2e7d6e')
    tagline       = request.form.get('tagline', '').strip()
    notes         = request.form.get('notes', '').strip()

    if not store_name or not contact_email or not temp_password:
        flash('Store name, contact email, and temp password are required.', 'error')
        return redirect(url_for('overseer_dashboard'))

    slug = slugify(store_name)
    base_slug = slug
    counter = 1
    while os.path.exists(os.path.join(CUSTOMERS_DIR, slug)):
        slug = f'{base_slug}-{counter}'
        counter += 1

    store_dir = os.path.join(CUSTOMERS_DIR, slug)
    os.makedirs(os.path.join(store_dir, 'uploads'), exist_ok=True)
    os.makedirs(os.path.join(store_dir, 'backups'), exist_ok=True)

    config = {
        'store_name':    store_name,
        'slug':          slug,
        'primary_color': primary_color,
        'industry':      industry,
        'tagline':       tagline,
        'plan':          plan,
        'status':        'active',
        'contact_name':  request.form.get('contact_name', '').strip(),
        'contact_email': contact_email,
        'contact_phone': request.form.get('contact_phone', '').strip(),
        'notes':         notes,
        'created_at':    datetime.datetime.now().isoformat(),
    }
    save_client_config(slug, config)

    inv_path = os.path.join(store_dir, 'inventory.csv')
    fieldnames = ['SKU','Title','Description','Category','Condition','Price',
                  'Cost Paid','Status','Date Added','Images','Section','Shelf']
    with open(inv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()

    users_path = os.path.join(store_dir, 'users.json')
    users = {
        contact_email: {
            'password':   hash_password(temp_password),
            'role':       'client',
            'store_slug': slug,
            'created_at': datetime.datetime.now().isoformat(),
        }
    }
    with open(users_path, 'w') as f:
        json.dump(users, f, indent=2)

    flash(f'Client "{store_name}" provisioned! Login: {contact_email} / {temp_password}', 'success')
    return redirect(url_for('overseer_client_detail', slug=slug))

@app.route('/overseer/client/<slug>/impersonate', methods=['POST'])
@rate_limit
@login_required
@overseer_required
def overseer_impersonate(slug):
    cfg = load_client_config(slug)
    if not cfg:
        flash('Client store not found.', 'error')
        return redirect(url_for('overseer_dashboard'))
    session['impersonating_slug'] = slug
    flash(f'Now managing {cfg["store_name"]}.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/overseer/exit-impersonate')
@login_required
def overseer_exit_impersonate():
    session.pop('impersonating_slug', None)
    flash('Returned to overseer view.', 'success')
    return redirect(url_for('overseer_dashboard'))

@app.route('/overseer/client/<slug>/suspend', methods=['POST'])
@rate_limit
@login_required
@overseer_required
def overseer_suspend(slug):
    cfg = load_client_config(slug)
    if not cfg:
        flash('Client not found.', 'error')
        return redirect(url_for('overseer_dashboard'))
    cfg['status'] = 'suspended' if cfg.get('status') == 'active' else 'active'
    save_client_config(slug, cfg)
    flash(f'{cfg["store_name"]} is now {cfg["status"]}.', 'success')
    return redirect(url_for('overseer_client_detail', slug=slug))

@app.route('/overseer/client/<slug>/delete', methods=['POST'])
@rate_limit
@login_required
@overseer_required
def overseer_delete(slug):
    confirm = request.form.get('confirm_name', '').strip()
    cfg = load_client_config(slug)
    if not cfg:
        flash('Client not found.', 'error')
        return redirect(url_for('overseer_dashboard'))
    if confirm != cfg['store_name']:
        flash('Store name did not match. Deletion cancelled.', 'error')
        return redirect(url_for('overseer_client_detail', slug=slug))
    store_dir = os.path.join(CUSTOMERS_DIR, slug)
    shutil.rmtree(store_dir, ignore_errors=True)
    flash(f'{cfg["store_name"]} deleted.', 'success')
    return redirect(url_for('overseer_dashboard'))

@app.route('/overseer/client/<slug>/reset-password', methods=['POST'])
@rate_limit
@login_required
@overseer_required
def overseer_reset_password(slug):
    new_pw = request.form.get('new_password', '').strip()
    confirm_pw = request.form.get('confirm_password', '').strip()
    if not new_pw or len(new_pw) < 6:
        flash('Password must be at least 6 characters.', 'error')
        return redirect(url_for('overseer_client_detail', slug=slug))
    if new_pw != confirm_pw:
        flash('Passwords do not match.', 'error')
        return redirect(url_for('overseer_client_detail', slug=slug))
    cfg = load_client_config(slug)
    if not cfg:
        flash('Client not found.', 'error')
        return redirect(url_for('overseer_dashboard'))
    users_file = os.path.join(CUSTOMERS_DIR, slug, 'users.json')
    if not os.path.exists(users_file):
        flash('No users file for this store.', 'error')
        return redirect(url_for('overseer_client_detail', slug=slug))
    with open(users_file) as f:
        users = json.load(f)
    email = cfg.get('contact_email', '')
    if email in users:
        users[email]['password'] = hash_password(new_pw)
        with open(users_file, 'w') as f:
            json.dump(users, f, indent=2)
        flash('Password updated successfully.', 'success')
    else:
        flash('User email not found in store users.', 'error')
    return redirect(url_for('overseer_client_detail', slug=slug))

@app.route('/overseer/client/<slug>/update', methods=['POST'])
@rate_limit
@login_required
@overseer_required
def overseer_update_client(slug):
    cfg = load_client_config(slug)
    if not cfg:
        flash('Client not found.', 'error')
        return redirect(url_for('overseer_dashboard'))
    cfg['store_name']    = request.form.get('store_name', cfg['store_name']).strip()
    cfg['tagline']       = request.form.get('tagline', cfg.get('tagline', '')).strip()
    cfg['primary_color'] = request.form.get('primary_color', cfg.get('primary_color', '#2e7d6e'))
    cfg['plan']          = request.form.get('plan', cfg.get('plan', 'starter'))
    cfg['notes']         = request.form.get('notes', cfg.get('notes', '')).strip()
    cfg['contact_name']  = request.form.get('contact_name', cfg.get('contact_name', '')).strip()
    cfg['contact_email'] = request.form.get('contact_email', cfg.get('contact_email', '')).strip()
    cfg['contact_phone'] = request.form.get('contact_phone', cfg.get('contact_phone', '')).strip()
    save_client_config(slug, cfg)
    flash('Client updated.', 'success')
    return redirect(url_for('overseer_client_detail', slug=slug))

# ── Client Dashboard ───────────────────────────────────────────────────────────
@app.route('/my-store/branding', methods=['GET', 'POST'])
@rate_limit
@client_required
def my_store_branding():
    slug = session.get('store_slug')
    cfg  = load_client_config(slug) or {}
    store_dir   = os.path.join(CUSTOMERS_DIR, slug)
    uploads_dir = os.path.join(store_dir, 'uploads')
    os.makedirs(uploads_dir, exist_ok=True)

    if request.method == 'POST':
        # Text fields
        cfg['store_name']        = request.form.get('store_name', cfg.get('store_name', '')).strip()
        cfg['tagline']           = request.form.get('tagline', '').strip()
        cfg['hero_slogan']       = request.form.get('hero_slogan', '').strip()
        cfg['store_description'] = request.form.get('store_description', '').strip()
        cfg['announcement']      = request.form.get('announcement', '').strip()
        cfg['primary_color']     = request.form.get('primary_color', cfg.get('primary_color', '#2e7d6e'))
        cfg['secondary_color']   = request.form.get('secondary_color', cfg.get('secondary_color', '#1a1a2e'))
        cfg['accent_color']      = request.form.get('accent_color', cfg.get('accent_color', '#10b981'))
        cfg['font_choice']       = request.form.get('font_choice', 'classic')
        cfg['contact_phone']     = request.form.get('contact_phone', '').strip()
        cfg['contact_address']   = request.form.get('contact_address', '').strip()
        cfg['social_facebook']   = request.form.get('social_facebook', '').strip()
        cfg['social_instagram']  = request.form.get('social_instagram', '').strip()
        cfg['social_tiktok']     = request.form.get('social_tiktok', '').strip()
        cfg['social_twitter']    = request.form.get('social_twitter', '').strip()

        # Business hours
        days = ['mon','tue','wed','thu','fri','sat','sun']
        hours_data = {}
        for d in days:
            closed = request.form.get(f'hours_{d}_closed') == 'on'
            hours_data[d] = 'Closed' if closed else request.form.get(f'hours_{d}', '').strip()
        cfg['business_hours'] = json.dumps(hours_data)

        # Logo upload
        logo_file = request.files.get('logo')
        if logo_file and logo_file.filename:
            ext = os.path.splitext(logo_file.filename)[1].lower() or '.jpg'
            logo_path = os.path.join(uploads_dir, f'logo{ext}')
            logo_file.save(logo_path)
            cfg['logo_url'] = f'/customer-upload/{slug}/logo{ext}'

        # Banner upload
        banner_file = request.files.get('banner')
        if banner_file and banner_file.filename:
            ext = os.path.splitext(banner_file.filename)[1].lower() or '.jpg'
            banner_path = os.path.join(uploads_dir, f'banner{ext}')
            banner_file.save(banner_path)
            cfg['banner_url'] = f'/customer-upload/{slug}/banner{ext}'

        # Gallery uploads (up to 8)
        gallery_images = cfg.get('gallery_images') or []
        if isinstance(gallery_images, str):
            try: gallery_images = json.loads(gallery_images)
            except: gallery_images = []
        for i in range(8):
            gfile = request.files.get(f'gallery_{i}')
            if gfile and gfile.filename:
                ext = os.path.splitext(gfile.filename)[1].lower() or '.jpg'
                gname = f'gallery_{i}{ext}'
                gfile.save(os.path.join(uploads_dir, gname))
                url = f'/customer-upload/{slug}/{gname}'
                if url not in gallery_images:
                    gallery_images.append(url)
        cfg['gallery_images'] = json.dumps(gallery_images)

        save_client_config(slug, cfg)
        flash('Branding saved!', 'success')
        return redirect(url_for('my_store_branding'))

    # Parse gallery_images and business_hours for template
    gallery_list = []
    raw_gallery = cfg.get('gallery_images', '[]')
    if isinstance(raw_gallery, str):
        try: gallery_list = json.loads(raw_gallery)
        except: gallery_list = []
    elif isinstance(raw_gallery, list):
        gallery_list = raw_gallery

    hours_obj = {}
    raw_hours = cfg.get('business_hours', '{}')
    if isinstance(raw_hours, str):
        try: hours_obj = json.loads(raw_hours)
        except: hours_obj = {}
    elif isinstance(raw_hours, dict):
        hours_obj = raw_hours

    return render_template('store_branding.html', cfg=cfg, slug=slug,
                           gallery_list=gallery_list, hours_obj=hours_obj, **ctx())


@app.route('/customer-upload/<slug>/<filename>')
def customer_upload(slug, filename):
    """Serve branding assets (logo, banner) for a client store."""
    import re
    if not re.fullmatch(r'[a-z0-9][a-z0-9\-]{0,62}', slug):
        return '', 404
    safe_filename = os.path.basename(filename)
    upload_dir = os.path.join(CUSTOMERS_DIR, slug, 'uploads')
    return send_from_directory(upload_dir, safe_filename)


@app.route('/my-store')
@client_required
def my_store():
    slug = session.get('store_slug')
    cfg  = load_client_config(slug) or {}
    products = load_inventory()
    total_value = sum(float(p.get('Price') or 0) for p in products)
    available   = sum(1 for p in products if p.get('Status','').lower() == 'available')
    sold        = sum(1 for p in products if p.get('Status','').lower() == 'sold')
    return render_template('client_dashboard.html',
        client_config=cfg,
        products=products,
        total_value=total_value,
        available=available,
        sold=sold,
        **ctx()
    )

@app.route('/my-store/change-password', methods=['GET', 'POST'])
@rate_limit
@client_required
def my_store_change_password():
    slug = session.get('store_slug')
    cfg  = load_client_config(slug) or {}
    if request.method == 'POST':
        current_pw  = request.form.get('current_password', '').strip()
        new_pw      = request.form.get('new_password', '').strip()
        confirm_pw  = request.form.get('confirm_password', '').strip()
        email = cfg.get('contact_email', '')
        users_file = os.path.join(CUSTOMERS_DIR, slug, 'users.json')
        if not os.path.exists(users_file) or not email:
            flash('Account not found.', 'error')
            return redirect(url_for('my_store_change_password'))
        with open(users_file) as f:
            users = json.load(f)
        user = users.get(email)
        if not user or not check_password(current_pw, user.get('password', ''))[0]:
            flash('Current password is incorrect.', 'error')
            return redirect(url_for('my_store_change_password'))
        if len(new_pw) < 6:
            flash('New password must be at least 6 characters.', 'error')
            return redirect(url_for('my_store_change_password'))
        if new_pw != confirm_pw:
            flash('New passwords do not match.', 'error')
            return redirect(url_for('my_store_change_password'))
        users[email]['password'] = hash_password(new_pw)
        with open(users_file, 'w') as f:
            json.dump(users, f, indent=2)
        flash('Password changed successfully!', 'success')
        return redirect(url_for('my_store'))
    return render_template('change_password.html', cfg=cfg, slug=slug, **ctx())

# ── Overseer Assistant ────────────────────────────────────────────────────────
def build_assistant_context():
    """Build a live business snapshot string for the AI system prompt."""
    stores = list_client_stores()
    leads  = load_leads()
    today  = datetime.datetime.now()

    # Enrich trial leads with days remaining
    trial_leads = [l for l in leads if l.get('type') == 'trial']
    for t in trial_leads:
        try:
            end = datetime.datetime.fromisoformat(t.get('trial_end') or t.get('created_at', today.isoformat()))
            t['_days_remaining'] = (end.date() - today.date()).days
        except Exception:
            t['_days_remaining'] = None

    active_stores    = [s for s in stores if s.get('status') == 'active']
    suspended_stores = [s for s in stores if s.get('status') == 'suspended']
    expiring_soon    = [t for t in trial_leads if t.get('_days_remaining') is not None and 0 <= t['_days_remaining'] <= 7]
    overdue_trials   = [t for t in trial_leads if t.get('_days_remaining') is not None and t['_days_remaining'] < 0]

    mrr        = len(active_stores) * 20
    setup_fees = len(stores) * 99.99

    lines = [
        f"Today's date: {today.strftime('%B %d, %Y')}",
        "",
        "PAYING CLIENTS (provisioned stores):",
    ]
    if stores:
        for s in stores:
            lines.append(
                f"  - {s.get('store_name','?')} (slug: {s.get('slug','?')}, plan: {s.get('plan','?')}, "
                f"status: {s.get('status','?')}, email: {s.get('contact_email','?')}, "
                f"created: {s.get('created_at','?')[:10]})"
            )
    else:
        lines.append("  (none yet)")

    lines += ["", "TRIAL SIGNUPS:"]
    if trial_leads:
        for t in trial_leads:
            dr = t.get('_days_remaining')
            dr_str = (f"{dr} days remaining" if dr is not None and dr >= 0
                      else (f"EXPIRED {abs(dr)} days ago" if dr is not None else "unknown"))
            lines.append(
                f"  - {t.get('store_name','?')} | {t.get('contact_email','?')} | "
                f"trial ends: {(t.get('trial_end','?') or '?')[:10]} ({dr_str})"
            )
    else:
        lines.append("  (none yet)")

    lines += [
        "",
        "BUSINESS SUMMARY:",
        f"  Total provisioned clients: {len(stores)}",
        f"  Active: {len(active_stores)}  Suspended: {len(suspended_stores)}",
        f"  Trial signups: {len(trial_leads)}",
        f"  Expiring within 7 days: {len(expiring_soon)}",
        f"  Overdue (trial expired, not converted): {len(overdue_trials)}",
        f"  Monthly recurring revenue: ${mrr}",
        f"  All-time setup fees collected: ${setup_fees}",
    ]

    return "\n".join(lines)


@app.route('/overseer/assistant/chat', methods=['POST'])
@rate_limit
@login_required
@overseer_required
def overseer_assistant_chat():
    data    = request.get_json(force=True)
    message = data.get('message', '').strip()
    if not message:
        return jsonify({'reply': 'Please type a message.', 'type': 'text'})

    api_key = get_ai_api_key()
    if not api_key:
        return jsonify({'reply': 'No AI API key configured. Add it in Admin Settings.', 'type': 'text'})

    context = build_assistant_context()
    system_prompt = f"""You are the Overseer Assistant for RetailTrack, a SaaS inventory management app run by Jay Alexander (Alexander AI Integrated Solutions).

{context}

You help Jay run his business. You can:
1. Answer questions about clients, trials, and revenue using the data above.
2. Draft follow-up emails — return them as JSON on its own line: {{"type":"email","to":"client@email.com","subject":"...","body":"..."}}
3. Trigger actions — return them as JSON on its own line: {{"action":"suspend","slug":"store-slug"}} or {{"action":"unsuspend","slug":"store-slug"}} or {{"action":"reset_password","slug":"store-slug"}}

Rules:
- Be concise and direct. Jay is busy.
- When drafting emails, be warm and professional — sign off as "— Jay, Alexander AI Integrated Solutions"
- For suspend/unsuspend/reset_password, output ONLY the JSON on its own line (no extra text on that line).
- Never suggest deleting a store — that requires manual confirmation.
- If you don't know something, say so honestly."""

    payload = {
        'model': 'claude-haiku-4-5-20251001',
        'max_tokens': 1024,
        'system': system_prompt,
        'messages': [{'role': 'user', 'content': message}],
    }

    try:
        import urllib.request as _ur
        req = _ur.Request(
            'https://api.anthropic.com/v1/messages',
            data=json.dumps(payload).encode(),
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
            }
        )
        with _ur.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        reply_text = result['content'][0]['text'].strip()
    except Exception as e:
        app.logger.error("Overseer assistant API error: %s", e)
        return jsonify({'reply': 'AI service error. Please try again.', 'type': 'text'})

    # Parse reply — look for action or email JSON on its own line
    for line in reply_text.splitlines():
        line = line.strip()
        if line.startswith('{') and line.endswith('}'):
            try:
                parsed = json.loads(line)
                if 'action' in parsed:
                    slug   = parsed.get('slug', '')
                    import re as _re_slug
                    if not _re_slug.fullmatch(r'[a-z0-9][a-z0-9\-]{0,62}', slug):
                        return jsonify({'reply': 'Invalid store slug.', 'type': 'text'})
                    action = parsed.get('action', '')
                    cfg    = load_client_config(slug)
                    if not cfg:
                        return jsonify({'reply': f'Store "{slug}" not found.', 'type': 'text'})
                    if action == 'suspend':
                        cfg['status'] = 'suspended'
                        save_client_config(slug, cfg)
                        return jsonify({'reply': f'✅ **{cfg["store_name"]}** has been suspended.', 'type': 'text'})
                    elif action == 'unsuspend':
                        cfg['status'] = 'active'
                        save_client_config(slug, cfg)
                        return jsonify({'reply': f'✅ **{cfg["store_name"]}** is now active.', 'type': 'text'})
                    elif action == 'reset_password':
                        import secrets as _sec
                        users_file = os.path.join(CUSTOMERS_DIR, slug, 'users.json')
                        if not os.path.exists(users_file):
                            return jsonify({'reply': f'No users file found for {slug}.', 'type': 'text'})
                        with open(users_file) as f:
                            users = json.load(f)
                        temp_pw = _sec.token_urlsafe(10)
                        email   = cfg.get('contact_email', '')
                        if email in users:
                            users[email]['password'] = hash_password(temp_pw)
                            with open(users_file, 'w') as f:
                                json.dump(users, f, indent=2)
                            return jsonify({'reply': f'✅ Password reset for **{cfg["store_name"]}**. New temp password: `{temp_pw}`', 'type': 'text'})
                        return jsonify({'reply': 'User email not found in store users.', 'type': 'text'})
                elif parsed.get('type') == 'email':
                    return jsonify({
                        'type':    'email',
                        'reply':   "Here's a draft email for you to review:",
                        'to':      parsed.get('to', ''),
                        'subject': parsed.get('subject', ''),
                        'body':    parsed.get('body', ''),
                    })
            except (json.JSONDecodeError, KeyError):
                pass

    return jsonify({'reply': reply_text, 'type': 'text'})


@app.route('/overseer/assistant/send-email', methods=['POST'])
@rate_limit
@login_required
@overseer_required
def overseer_assistant_send_email():
    data    = request.get_json(force=True)
    to      = data.get('to', '').strip()
    subject = data.get('subject', '').strip()
    body    = data.get('body', '').strip()
    if not to or not subject or not body:
        return jsonify({'success': False, 'error': 'to, subject, and body are required.'}), 400
    if '@' not in to or '.' not in to.split('@')[-1]:
        return jsonify({'success': False, 'error': 'Invalid email address.'}), 400
    ok, err = send_smtp_email(to, subject, body)
    if ok:
        _audit('wizard_complete', slug=slug, details={'store_name': store_name})
    return jsonify({'success': True, 'message': f'Sent to {to}'})
    return jsonify({'success': False, 'error': err}), 422


@app.route('/overseer/assistant/alerts', methods=['POST'])
@rate_limit
@login_required
@overseer_required
def overseer_assistant_alerts():
    """Return pre-generated alert cards for trials expiring within 3 days."""
    try:
        leads = load_leads()
    except Exception as e:
        app.logger.error("Failed to load leads in alerts: %s", e)
        return jsonify({'alerts': []}), 500
    today = datetime.datetime.now()
    alerts = []
    for lead in leads:
        if lead.get('type') != 'trial':
            continue
        try:
            trial_end_str = lead.get('trial_end', '')
            if not trial_end_str:
                continue
            end  = datetime.datetime.fromisoformat(trial_end_str)
            days = (end.date() - today.date()).days
        except Exception:
            continue
        if 0 <= days <= 3:
            store_name    = lead.get('store_name', 'your store')
            contact_email = lead.get('contact_email', '')
            contact_name  = lead.get('contact_name', '')
            greeting      = f"Hi {contact_name}" if contact_name else "Hi there"
            end_formatted = end.strftime('%B %d, %Y')
            days_label    = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
            alerts.append({
                'store_name':    store_name,
                'contact_email': contact_email,
                'days':          days,
                'days_label':    days_label,
                'draft_to':      contact_email,
                'draft_subject': f"Your RetailTrack trial ends {days_label} — next steps",
                'draft_body':    (
                    f"{greeting},\n\n"
                    f"Just a quick note — your 14-day free trial for {store_name} ends {days_label} ({end_formatted}).\n\n"
                    f"To keep your store running, the one-time setup fee is $99.99 and then just $20/month after that. "
                    f"I'll send you a payment link as soon as you're ready.\n\n"
                    f"Feel free to reply to this email with any questions.\n\n"
                    f"— Jay\nAlexander AI Integrated Solutions"
                ),
            })
    alerts.sort(key=lambda a: a['days'])
    return jsonify({'alerts': alerts})


# ── Run ───────────────────────────────────────────────────────────────────────

# ============================================================

# ============================================================
# STRUCTURED LOGGING + METRICS
# ============================================================
import logging as _logging

def configure_logging(app):
    """Set up production logging."""
    handler = _logging.StreamHandler()
    handler.setFormatter(_logging.Formatter(
        '%(asctime)s %(levelname)s [%(module)s] %(message)s'
    ))
    handler.setLevel(_logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(_logging.INFO)

def _ensure_metrics_table():
    """Create metrics table if missing."""
    try:
        db = get_db()
        db.execute("""CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric TEXT NOT NULL,
            value REAL DEFAULT 1,
            tenant_slug TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        db.commit()
    except Exception:
        pass

def track(metric, value=1, slug=None):
    """Fire-and-forget metrics tracking."""
    try:
        _ensure_metrics_table()
        db = get_db()
        db.execute(
            "INSERT INTO metrics (metric, value, tenant_slug) VALUES (?,?,?)",
            (metric, value, slug)
        )
        db.commit()
    except Exception:
        pass  # Never break the app over metrics

# Request timing middleware
import time as _req_time
@app.before_request
def _before_req():
    from flask import g
    g._req_start = _req_time.time()

@app.after_request
def _after_req(response):
    from flask import g
    if not request.path.startswith('/static'):
        elapsed = (_req_time.time() - getattr(g, '_req_start', _req_time.time())) * 1000
        lvl = 'WARNING' if elapsed > 800 else 'DEBUG'
        if lvl == 'WARNING':
            app.logger.warning(
                f"SLOW_REQUEST {request.method} {request.path} "
                f"{response.status_code} {elapsed:.0f}ms"
            )
    return response



# ============================================================
# SEO — Sitemap + Robots.txt
# ============================================================
@app.route('/sitemap.xml')
def sitemap():
    """Auto-generated XML sitemap for SEO."""
    host = request.host_url.rstrip('/')
    urls = [
        {'loc': f"{host}/",          'priority': '1.0', 'changefreq': 'weekly'},
        {'loc': f"{host}/login",     'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': f"{host}/signup",    'priority': '0.9', 'changefreq': 'monthly'},
        {'loc': f"{host}/pricing",   'priority': '0.8', 'changefreq': 'monthly'},
    ]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml.append(f"  <url>")
        xml.append(f"    <loc>{u['loc']}</loc>")
        xml.append(f"    <changefreq>{u['changefreq']}</changefreq>")
        xml.append(f"    <priority>{u['priority']}</priority>")
        xml.append(f"  </url>")
    xml.append('</urlset>')
    return '\n'.join(xml), 200, {'Content-Type': 'application/xml'}

@app.route('/robots.txt')
def robots():
    """robots.txt for search engine crawling guidance."""
    host = request.host_url.rstrip('/')
    content = f"""User-agent: *
Allow: /
Disallow: /admin
Disallow: /overseer
Disallow: /api/
Sitemap: {host}/sitemap.xml
"""
    return content, 200, {'Content-Type': 'text/plain'}



# ============================================================
# BACKGROUND EMAIL QUEUE PROCESSOR
# Uses threading.Timer — no external deps needed
# ============================================================
import threading as _sched_threading

def _run_email_scheduler():
    """Process email queue every 10 minutes."""
    try:
        with app.app_context():
            process_email_queue()
    except Exception as e:
        app.logger.error(f"EMAIL_SCHEDULER_ERROR: {e}")
    finally:
        # Reschedule
        t = _sched_threading.Timer(600, _run_email_scheduler)  # 10 minutes
        t.daemon = True
        t.start()

def start_email_scheduler():
    """Start the background email processor."""
    t = _sched_threading.Timer(60, _run_email_scheduler)  # First run after 1min
    t.daemon = True
    t.start()
    app.logger.info("EMAIL_SCHEDULER_STARTED: will process every 10 minutes")


# GLOBAL ERROR HANDLERS
# ============================================================
@app.errorhandler(404)
def not_found_error(e):
    if request.path.startswith('/api/'):
        return __import__('flask').jsonify({'error': 'Not found'}), 404
    return render_template('404.html') if os.path.exists(
        os.path.join(app.template_folder or 'templates', '404.html')
    ) else ('<h1>404 - Page Not Found</h1>', 404)

@app.errorhandler(500)
def internal_error(e):
    app.logger.error(f"UNHANDLED_500: {str(e)}", exc_info=True)
    if request.path.startswith('/api/'):
        return __import__('flask').jsonify({'error': 'Internal server error'}), 500
    return '<h1>500 - Something went wrong. We are looking into it.</h1>', 500

@app.errorhandler(429)
def rate_limit_error(e):
    return __import__('flask').jsonify({'error': 'Too many requests. Please slow down.'}), 429


# ============================================================
# EMAIL SYSTEM — Onboarding sequences
# ============================================================
import smtplib, threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

_SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
_SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
_SMTP_USER = os.environ.get('SMTP_USER', '')
_SMTP_PASS = os.environ.get('SMTP_PASS', '')
_FROM_EMAIL = os.environ.get('FROM_EMAIL', 'jay@liberty-emporium.com')
_FROM_NAME  = os.environ.get('FROM_NAME', 'Jay Alexander - Liberty Inventory')

def _send_email_worker(to_email, subject, html_body):
    """Internal blocking send — run in thread."""
    if not _SMTP_USER or not _SMTP_PASS:
        app.logger.info(f"EMAIL_SKIPPED (no SMTP config): {subject} -> {to_email}")
        return
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = f"{_FROM_NAME} <{_FROM_EMAIL}>"
        msg['To']      = to_email
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=10) as srv:
            srv.starttls()
            srv.login(_SMTP_USER, _SMTP_PASS)
            srv.sendmail(_FROM_EMAIL, to_email, msg.as_string())
        app.logger.info(f"EMAIL_SENT: {subject} -> {to_email}")
    except Exception as e:
        app.logger.error(f"EMAIL_FAILED: {subject} -> {to_email}: {e}")

def send_email(to_email, subject, html_body):
    """Non-blocking email send via background thread."""
    t = threading.Thread(target=_send_email_worker,
                         args=(to_email, subject, html_body), daemon=True)
    t.start()

def _schedule_email_queue():
    """Create email_queue table in main DB if needed."""
    try:
        db = get_db()
        db.execute("""CREATE TABLE IF NOT EXISTS email_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            template TEXT NOT NULL,
            context TEXT DEFAULT '{}',
            send_at TEXT NOT NULL,
            sent INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        db.commit()
    except Exception as e:
        app.logger.error(f"EMAIL_QUEUE_INIT_ERROR: {e}")

def queue_email(user_email, template, context_dict, delay_days=0, delay_hours=0):
    """Schedule an email for future delivery."""
    import json as _json
    from datetime import datetime, timedelta
    send_at = (datetime.utcnow() +
               timedelta(days=delay_days, hours=delay_hours)).isoformat()
    try:
        _schedule_email_queue()
        db = get_db()
        db.execute(
            "INSERT INTO email_queue (user_email, template, context, send_at) VALUES (?,?,?,?)",
            (user_email, template, _json.dumps(context_dict), send_at)
        )
        db.commit()
    except Exception as e:
        app.logger.error(f"QUEUE_EMAIL_ERROR: {e}")

def queue_onboarding_sequence(email, name, store_name, slug, temp_password, app_url):
    """Queue the full 14-day onboarding sequence for a new trial user."""
    ctx = {
        'name': name, 'store_name': store_name, 'slug': slug,
        'temp_password': temp_password, 'app_url': app_url,
        'login_url': f"{app_url}/login",
        'dashboard_url': f"{app_url}/dashboard",
    }
    queue_email(email, 'welcome',          ctx, delay_days=0)
    queue_email(email, 'quick_start',      ctx, delay_days=1)
    queue_email(email, 'feature_spotlight',ctx, delay_days=3)
    queue_email(email, 'check_in',         ctx, delay_days=5)
    queue_email(email, 'upgrade_reminder', ctx, delay_days=10)
    queue_email(email, 'last_chance',      ctx, delay_days=13)

def process_email_queue():
    """Send due emails from the queue. Call this periodically."""
    import json as _json
    from datetime import datetime
    try:
        _schedule_email_queue()
        db = get_db()
        now = datetime.utcnow().isoformat()
        rows = db.execute(
            "SELECT id, user_email, template, context FROM email_queue "
            "WHERE send_at <= ? AND sent = 0 LIMIT 20", (now,)
        ).fetchall()
        for row in rows:
            ctx = _json.loads(row['context'] or '{}')
            html = _build_email_template(row['template'], ctx)
            if html:
                subj = _EMAIL_SUBJECTS.get(row['template'], 'Message from Liberty Inventory')
                send_email(row['user_email'], subj, html)
            db.execute("UPDATE email_queue SET sent = 1 WHERE id = ?", (row['id'],))
        if rows:
            db.commit()
            app.logger.info(f"EMAIL_QUEUE_PROCESSED: {len(rows)} emails sent")
    except Exception as e:
        app.logger.error(f"PROCESS_EMAIL_QUEUE_ERROR: {e}")

_EMAIL_SUBJECTS = {
    'welcome':           "You're in! Here's your first step — Liberty Inventory",
    'quick_start':       "3 minutes to your first inventory item",
    'feature_spotlight': "The feature 80%% of our users love most",
    'check_in':         "Stuck? We're here to help",
    'upgrade_reminder':  "Your trial ends in 4 days — keep your store",
    'last_chance':       "Final day: Don't lose your Liberty Inventory store",
}

def _build_email_template(template, ctx):
    """Build HTML email from template name and context."""
    name = ctx.get('name', 'there')
    store = ctx.get('store_name', 'your store')
    dashboard = ctx.get('dashboard_url', '#')
    login_url = ctx.get('login_url', '#')
    pw = ctx.get('temp_password', '')

    BASE = """<!DOCTYPE html><html><body style="font-family:-apple-system,sans-serif;
max-width:600px;margin:0 auto;padding:24px;color:#1f2937;">
{body}
<hr style="margin:32px 0;border:none;border-top:1px solid #e5e7eb;">
<p style="font-size:12px;color:#9ca3af;">
Liberty Inventory · <a href="{dashboard}">Dashboard</a>
</p></body></html>"""

    BTN = '<a href="{url}" style="display:inline-block;background:#2e7d6e;color:white;'           'padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;'           'margin:16px 0;">{label}</a>'

    bodies = {
        'welcome': f"""
<h2 style="color:#2e7d6e;">Welcome to Liberty Inventory! 🎉</h2>
<p>Hi {name},</p>
<p>Your <strong>{store}</strong> store is ready. You have a full 14-day free trial — no credit card needed.</p>
<p><strong>Your login details:</strong><br>
Email: {ctx.get('user_email', ctx.get('email', ''))}<br>
Password: <code>{pw}</code></p>
<p>Your first step:</p>
{BTN.format(url=dashboard, label="Go to Your Dashboard →")}
<p style="margin-top:24px;color:#6b7280;">Questions? Just reply to this email.<br>– Jay</p>
""",
        'quick_start': f"""
<h2 style="color:#2e7d6e;">Your first inventory item takes 2 minutes</h2>
<p>Hi {name},</p>
<p>Ready to add your first item to <strong>{store}</strong>? It's the fastest way to see what Liberty Inventory can do.</p>
{BTN.format(url=dashboard, label="Add Your First Item →")}
<p style="color:#6b7280;">Takes less than 2 minutes. – Jay</p>
""",
        'feature_spotlight': f"""
<h2 style="color:#2e7d6e;">The feature our users love most 🏷️</h2>
<p>Hi {name},</p>
<p>The #1 thing thrift store owners tell us saves them the most time: <strong>bulk pricing by category</strong>.</p>
<p>Set a price range for a category once, and every new item defaults to it. No more typing the same price 50 times.</p>
{BTN.format(url=dashboard, label="Try It In Your Store →")}
<p style="color:#6b7280;">– Jay</p>
""",
        'check_in': f"""
<h2 style="color:#2e7d6e;">Need a hand? 👋</h2>
<p>Hi {name},</p>
<p>Just checking in on your <strong>{store}</strong> trial. If you've hit a snag or have questions, reply to this email — I read every one.</p>
{BTN.format(url=dashboard, label="Go to Dashboard →")}
<p style="color:#6b7280;">– Jay</p>
""",
        'upgrade_reminder': f"""
<h2 style="color:#2e7d6e;">Your trial ends in 4 days ⏰</h2>
<p>Hi {name},</p>
<p>Your free trial of Liberty Inventory ends in 4 days. To keep <strong>{store}</strong> and your inventory data, upgrade to our hosting plan.</p>
<p><strong>$20/month</strong> — everything included, cancel any time.</p>
{BTN.format(url=login_url.replace('/login', '/upgrade'), label="Keep My Store — Upgrade Now →")}
<p style="color:#6b7280;">– Jay</p>
""",
        'last_chance': f"""
<h2 style="color:#2e7d6e;">Final day — don't lose your data 🚨</h2>
<p>Hi {name},</p>
<p>Today is the last day of your Liberty Inventory trial. After today, your <strong>{store}</strong> data will be archived.</p>
<p>Upgrade now to keep everything and continue running your store.</p>
{BTN.format(url=login_url.replace('/login', '/upgrade'), label="Save My Store — $20/mo →")}
<p style="color:#6b7280;">– Jay</p>
""",
    }

    body = bodies.get(template)
    if not body:
        return None
    return BASE.format(body=body, dashboard=dashboard)


@app.route('/admin/process-emails')
def admin_process_emails():
    """Process pending email queue — call via cron or heartbeat."""
    process_email_queue()
    return __import__('flask').jsonify({'status': 'ok'})

# Start email scheduler on app init
start_email_scheduler()


# ── Admin-only API token UI routes ───────────────────────────────────────────
# ── API Generator (Admin only) ──────────────────────────────────────────────

@app.route('/admin/api-generator')
@login_required
@admin_required
def admin_api_generator():
    """Dedicated API Generator page — admin only."""
    # Load existing tokens
    keys = load_api_keys()
    admin_keys = {k: v for k, v in keys.items() if v.get('created_by') == 'admin'}
    return render_template('admin_api_generator.html', api_keys=admin_keys, base_url=request.host_url.rstrip('/'), **ctx())

@app.route('/admin/api-generator/generate', methods=['POST'])
@login_required
@admin_required
def admin_generate_api_key():
    """Generate a new API key — admin only."""
    import secrets as _sec
    from datetime import datetime as _dt
    label = request.form.get('label', 'Testing Key').strip() or 'Testing Key'
    raw_key = 'lib_' + _sec.token_urlsafe(32)
    keys = load_api_keys()
    keys[raw_key] = {
        'name': label,
        'created_by': 'admin',
        'created_at': _dt.utcnow().isoformat(),
        'active': True,
    }
    save_api_keys(keys)
    flash(f'API key generated: {raw_key}', 'success')
    return redirect(url_for('admin_api_generator') + '?new_key=' + raw_key)

@app.route('/admin/api-generator/revoke/<path:key>', methods=['POST'])
@login_required
@admin_required
def admin_revoke_api_key(key):
    """Revoke an API key — admin only."""
    keys = load_api_keys()
    if key in keys:
        del keys[key]
        save_api_keys(keys)
        flash('API key revoked.', 'success')
    return redirect(url_for('admin_api_generator'))

@app.route('/api/token/ui', methods=['POST'])
@login_required
@admin_required
def api_token_ui_generate():
    """Legacy endpoint kept for backward compat — redirects to new system."""
    import secrets as _s
    raw_key = 'lib_' + _s.token_urlsafe(32)
    keys = load_api_keys()
    from datetime import datetime as _dt2
    keys[raw_key] = {'name': 'ui-generated', 'created_by': 'admin', 'created_at': _dt2.utcnow().isoformat(), 'active': True}
    save_api_keys(keys)
    return jsonify({'success': True, 'api_token': raw_key})

@app.route('/api/token/ui', methods=['DELETE'])
@login_required
@admin_required
def api_token_ui_revoke():
    keys = load_api_keys()
    to_del = [k for k, v in keys.items() if v.get('name') == 'ui-generated']
    for k in to_del:
        del keys[k]
    save_api_keys(keys)
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(debug=True)

# ── API Keys ───────────────────────────────────────────────────────────────

API_KEYS_FILE = os.path.join(DATA_DIR, 'api_keys.json')

def load_api_keys():
    if os.path.exists(API_KEYS_FILE):
        with open(API_KEYS_FILE) as f:
            return json.load(f)
    return {}

def save_api_keys(keys):
    with open(API_KEYS_FILE, 'w') as f:
        json.dump(keys, f, indent=2)

def require_api_key(f):
    """Decorator to require valid API key. Accepts X-API-Key header, Authorization: Bearer, or ?api_key= param."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        # Also accept Authorization: Bearer <key>
        if not api_key:
            auth = request.headers.get('Authorization', '')
            if auth.startswith('Bearer '):
                api_key = auth[7:].strip()
        if not api_key:
            return jsonify({'error': 'API key required. Pass as X-API-Key header or Authorization: Bearer <key>'}), 401
        keys = load_api_keys()
        if api_key not in keys:
            return jsonify({'error': 'Invalid API key'}), 401
        g.api_key = api_key
        g.api_key_name = keys[api_key].get('name', 'API Key')
        return f(*args, **kwargs)
    return decorated

# ── API Routes ─────────────────────────────────────────────────────────────

@app.route("/api/keys", methods=["GET"])
@rate_limit
@login_required
@admin_required
def list_api_keys():
    """List all API keys (masked)"""
    keys = load_api_keys()
    masked = {k[:8]+'...': {'name': v.get('name'), 'created': v.get('created')} for k, v in keys.items()}
    return jsonify(masked)

@app.route('/api/keys', methods=['POST'])
@rate_limit
@login_required
@admin_required
def create_api_key():
    """Create new API key"""
    data = request.get_json()
    name = data.get('name', 'API Key')
    
    import secrets
    key = secrets.token_urlsafe(32)
    
    keys = load_api_keys()
    keys[key] = {
        'name': name,
        'created': str(datetime.datetime.now()),
        'created_by': session.get('username')
    }
    save_api_keys(keys)
    
    return jsonify({'api_key': key, 'name': name, 'message': 'Save this key - it won\'t be shown again!'})

@app.route('/api/keys/<key>', methods=['DELETE'])
@login_required
@admin_required
def delete_api_key(key):
    """Delete API key"""
    keys = load_api_keys()
    if key in keys:
        del keys[key]
        save_api_keys(keys)
        return jsonify({'message': 'Key deleted'})
    return jsonify({'error': 'Key not found'}), 404

# ── Inventory API ──────────────────────────────────────────────────────────

@app.route('/api/inventory', methods=['GET'])
@rate_limit
@require_api_key
def api_get_inventory():
    """Get all products"""
    products = load_inventory()
    return jsonify({'count': len(products), 'products': products})

@app.route('/api/inventory/<sku>', methods=['GET'])
@rate_limit
@require_api_key
def api_get_product(sku):
    """Get single product"""
    products = load_inventory()
    product = next((p for p in products if p['SKU'] == sku), None)
    if product:
        return jsonify(product)
    return jsonify({'error': 'Product not found'}), 404

@app.route('/api/inventory', methods=['POST'])
@rate_limit
@require_api_key
def api_create_product():
    """Create new product"""
    data = request.get_json()
    products = load_inventory()
    
    # Check SKU doesn't exist
    if any(p['SKU'] == data.get('SKU') for p in products):
        return jsonify({'error': 'SKU already exists'}), 400
    
    products.append(data)
    save_inventory(products)
    
    return jsonify({'message': 'Product created', 'sku': data.get('SKU')})

@app.route('/api/inventory/<sku>', methods=['PUT'])
@require_api_key
def api_update_product(sku):
    """Update product"""
    data = request.get_json()
    products = load_inventory()
    
    for i, p in enumerate(products):
        if p['SKU'] == sku:
            products[i].update(data)
            save_inventory(products)
            return jsonify({'message': 'Product updated'})
    
    return jsonify({'error': 'Product not found'}), 404

@app.route('/api/inventory/<sku>', methods=['DELETE'])
@require_api_key
def api_delete_product(sku):
    """Delete product"""
    products = load_inventory()
    
    for i, p in enumerate(products):
        if p['SKU'] == sku:
            del products[i]
            save_inventory(products)
            return jsonify({'message': 'Product deleted'})
    
    return jsonify({'error': 'Product not found'}), 404

@app.route('/api/stats', methods=['GET'])
@rate_limit
@require_api_key
def api_stats():
    """Get inventory stats"""
    products = load_inventory()
    stats = get_stats()
    return jsonify(stats)

# ── Ad API ─────────────────────────────────────────────────────────────────

@app.route('/api/ads/generate', methods=['POST'])
@rate_limit
@require_api_key
def api_generate_ad():
    """Generate ad for product"""
    data = request.get_json()
    sku = data.get('sku')
    
    products = load_inventory()
    product = next((p for p in products if p['SKU'] == sku), None)
    if not product:
        return jsonify({'error': 'Product not found'}), 404
    
    # Call AI ad generator
    # (Would integrate with existing ad_generator logic)
    return jsonify({'sku': sku, 'status': 'generated', 'message': 'Ad generation endpoint ready'})


# ── API Settings Endpoint ──
@app.route('/api/save-settings', methods=['GET', 'POST'])
@rate_limit
def api_save_settings():
    """Save API keys from settings popup to app_config.json. GET returns current model."""
    if request.method == 'GET':
        return jsonify({'openrouter_model': get_openrouter_model()})
    data = request.get_json() or {}
    
    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    
    # Load existing config
    app_cfg = {}
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file, 'r') as f:
                app_cfg = json.load(f)
        except:
            pass
    
    # Update with new keys
    for key in ['OPENROUTER_API_KEY', 'OPENROUTER_MODEL', 'ANTHROPIC_API_KEY', 'XAI_API_KEY', 'GROQ_API_KEY']:
        if data.get(key):
            if key == 'OPENROUTER_API_KEY':
                app_cfg['openrouter_api_key'] = data[key].strip()
            elif key == 'OPENROUTER_MODEL':
                app_cfg['openrouter_model'] = data[key].strip()
            elif key == 'ANTHROPIC_API_KEY':
                app_cfg['anthropic_api_key'] = data[key].strip()
            elif key == 'XAI_API_KEY':
                app_cfg['xai_api_key'] = data[key].strip()
            elif key == 'GROQ_API_KEY':
                app_cfg['groq_api_key'] = data[key].strip()
    
    # Save
    try:
        with open(app_config_file, 'w') as f:
            json.dump(app_cfg, f, indent=2)
        return jsonify({'success': True, 'message': 'API keys saved!'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── AI Assistant (OpenRouter) ────────────────────────────────────────────────

def _get_ai_api_key_for_chat(slug=None):
    """Get OpenRouter API key for chat, checking per-store then system-wide."""
    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    app_cfg = {}
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file) as f:
                app_cfg = json.load(f)
        except: pass
    # Check per-store key first
    if slug:
        cfg = load_client_config(slug) or {}
        key = cfg.get('openrouter_api_key', '').strip()
        if key:
            return key
    # Fall back to system-wide key
    return app_cfg.get('openrouter_api_key', '').strip()


def _get_ai_model(slug=None):
    """Get the model to use for chat (from config or default)."""
    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    app_cfg = {}
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file) as f:
                app_cfg = json.load(f)
        except: pass
    if slug:
        cfg = load_client_config(slug) or {}
        model = cfg.get('ai_chat_model', '').strip()
        if model:
            return model
    return app_cfg.get('ai_chat_model', 'openai/gpt-4o-mini').strip()



# ═══════════════════════════════════════════════════════════════
# AI CEO MEMORY SYSTEM
# ═══════════════════════════════════════════════════════════════

def get_ai_memory_path(slug):
    """Returns path to ai_memory.json for a tenant."""
    if not slug:
        return None
    d = os.path.join(CUSTOMERS_DIR, slug)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'ai_memory.json')

def load_ai_memory(slug):
    """Load AI CEO memory for a tenant."""
    path = get_ai_memory_path(slug)
    if not path or not os.path.exists(path):
        return {
            'boss_name': '',
            'business_goals': [],
            'preferences': [],
            'decisions': [],
            'lessons_learned': [],
            'conversation_count': 0,
            'created_at': '',
            'last_updated': '',
        }
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def save_ai_memory(slug, memory):
    """Save AI CEO memory for a tenant."""
    path = get_ai_memory_path(slug)
    if not path:
        return
    memory['last_updated'] = datetime.datetime.now().isoformat()
    with open(path, 'w') as f:
        json.dump(memory, f, indent=2)

def memory_to_context(memory):
    """Convert AI memory dict to a context string for the system prompt."""
    if not memory:
        return ""
    parts = []
    if memory.get('boss_name'):
        parts.append(f"Boss name: {memory['boss_name']}")
    if memory.get('business_goals'):
        parts.append(f"Business goals: {'; '.join(memory['business_goals'][:5])}")
    if memory.get('preferences'):
        parts.append(f"Boss preferences: {'; '.join(memory['preferences'][:5])}")
    if memory.get('lessons_learned'):
        parts.append(f"Things you have learned about this business: {'; '.join(memory['lessons_learned'][-5:])}")
    if memory.get('decisions'):
        parts.append(f"Recent decisions made: {'; '.join(str(d) for d in memory['decisions'][-3:])}")
    if memory.get('conversation_count'):
        parts.append(f"You have had {memory['conversation_count']} previous conversations with this boss.")
    return "\n".join(parts)

def extract_memory_updates(reply, memory):
    """
    Try to extract memory-worthy info from AI reply.
    Very simple heuristic — looks for certain patterns.
    Returns updated memory dict.
    """
    import re as _re
    memory = dict(memory)
    # Bump conversation count
    memory['conversation_count'] = memory.get('conversation_count', 0) + 1
    # Extract if AI mentions learning something
    learn_patterns = [
        r"I(?:'ll| will) remember that (.+?)(?:\.|$)",
        r"Note(?:d|:) (.+?)(?:\.|$)",
        r"I see that (.+?)(?:\.|$)",
    ]
    for pat in learn_patterns:
        match = _re.search(pat, reply, _re.IGNORECASE)
        if match:
            lesson = match.group(1).strip()[:120]
            lessons = memory.get('lessons_learned', [])
            if lesson not in lessons:
                lessons.append(lesson)
                memory['lessons_learned'] = lessons[-20:]  # keep last 20
    return memory

# ── Telegram notification for tenants ─────────────────────────

def send_telegram_message(bot_token, chat_id, text):
    """Send a message via Telegram bot to a chat/user."""
    import urllib.request as _ur
    import urllib.error as _ue
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}).encode()
    try:
        req = _ur.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        with _ur.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        return result.get('ok', False)
    except Exception as e:
        print(f"[Telegram] Error: {e}")
        return False

@app.route('/api/reset-model', methods=['POST'])
@login_required
def api_reset_model():
    """Reset OpenRouter model to the reliable default (gemini-flash-1.5)."""
    import json as _j
    app_config_file = os.path.join(DATA_DIR, 'app_config.json')
    app_cfg = {}
    if os.path.exists(app_config_file):
        try:
            with open(app_config_file) as f:
                app_cfg = _j.load(f)
        except Exception:
            pass
    app_cfg['openrouter_model'] = 'google/gemini-flash-1.5'
    with open(app_config_file, 'w') as f:
        _j.dump(app_cfg, f, indent=2)
    return jsonify({'ok': True, 'model': 'google/gemini-flash-1.5'})


@app.route('/api/bot/telegram', methods=['POST'])
@rate_limit
def api_bot_telegram():
    """Let the AI send a Telegram message to the store owner."""
    data = request.get_json() or {}
    slug = session.get('impersonating_slug') or session.get('store_slug') or None
    if not slug:
        return jsonify({'error': 'No store context'}), 400
    cfg = load_client_config(slug) or {}
    bot_token = cfg.get('telegram_bot_token', '')
    chat_id   = cfg.get('telegram_chat_id', '')
    if not bot_token or not chat_id:
        return jsonify({'error': 'Telegram not configured. Add bot token and chat ID in Settings.'}), 400
    message = data.get('message', '').strip()
    if not message:
        return jsonify({'error': 'No message'}), 400
    ok = send_telegram_message(bot_token, chat_id, f"📦 <b>{cfg.get('store_name','Store')} AI:</b>\n{message}")
    return jsonify({'ok': ok})

@app.route('/api/bot/chat', methods=['POST'])
@rate_limit
def api_bot_chat():
    """AI assistant via OpenRouter — can control inventory, add customers, etc."""
    import urllib.request as _ur
    import urllib.error as _ue
    import base64 as _b64

    data = request.get_json() or {}
    user_message = data.get('message', '').strip()
    history      = data.get('history', [])  # [{role, content}, ...]
    image_b64    = data.get('image', None)   # base64 image from paperclip
    image_mime   = data.get('image_mime', 'image/jpeg')

    if not user_message and not image_b64:
        return jsonify({'error': 'No message provided'}), 400

    slug = session.get('impersonating_slug') or session.get('store_slug') or None
    api_key = _get_ai_api_key_for_chat(slug)
    model   = _get_ai_model(slug)

    if not api_key:
        return jsonify({'error': 'No OpenRouter API key configured. Add one in Settings ⚙️ → API Keys.'}), 400

    # Build store context
    # Page context tells AI where the user is
    page_context = data.get('page', '')
    is_logged_in = bool(session.get('logged_in') or session.get('store_logged_in'))

    try:
        products  = load_inventory() if is_logged_in else []
        total_val = sum(float(p.get('Price') or 0) for p in products)
        available = sum(1 for p in products if p.get('Status','').lower() == 'available')
        sold      = sum(1 for p in products if p.get('Status','').lower() == 'sold')
        store_cfg = load_store_config()
        client_cfg = {}
        store_name = 'Liberty Inventory'
        if slug:
            client_cfg = load_client_config(slug) or {}
            store_name = client_cfg.get('store_name', 'Liberty Inventory')
        elif not is_logged_in:
            store_name = 'Liberty Inventory'
        else:
            store_name = store_cfg.get('store_name', 'Liberty Inventory')

        # Load AI CEO memory
        ai_memory = load_ai_memory(slug) if slug else {}
        memory_ctx = memory_to_context(ai_memory)

        # Build page-specific guidance
        if not is_logged_in or 'login' in page_context:
            page_guidance = (
                "The user is on the LOGIN page and is NOT yet logged in. "
                "Help them understand: demo credentials are admin/admin1 or they can sign up free. "
                "Guide them warmly to log in. Do not discuss inventory. "
                "If they seem confused, offer to walk them through the login step by step."
            )
        elif 'dashboard' in page_context:
            page_guidance = "The user is on the DASHBOARD. Help them understand the stats and navigate the app."
        elif 'inventory' in page_context or 'product' in page_context:
            page_guidance = "The user is viewing INVENTORY. Help them manage products, add items, update prices."
        elif 'settings' in page_context:
            page_guidance = "The user is in SETTINGS. Help them configure their store, API keys, and preferences."
        elif 'signup' in page_context or 'wizard' in page_context:
            page_guidance = "The user is SIGNING UP. Welcome them warmly, explain the trial, help them get started."
        else:
            page_guidance = "Help the user with whatever they need."

        # Summary of inventory
        items_summary = '; '.join(
            f"{p.get('Title','?')} (${p.get('Price','?')}, {p.get('Status','?')})"
            for p in products[:20]
        ) if products else 'No inventory loaded yet.'

        context = (
            f"You are the AI CEO assistant for {store_name}. "
            f"You are intelligent, warm, and genuinely helpful. You learn from every conversation. "
            f"\n\nCURRENT PAGE: {page_context or 'general'}. {page_guidance}"
            f"\n\nSTORE STATS: {len(products)} items, {available} available, {sold} sold, "
            f"total value ${total_val:.2f}."
        )
        if items_summary and is_logged_in:
            context += f"\nRecent inventory: {items_summary}."
        if memory_ctx:
            context += f"\n\nYOUR MEMORY ABOUT THIS BOSS:\n{memory_ctx}"
        context += (
            f"\n\nYou can: add inventory, update prices, analyze sales, write descriptions, "
            f"identify items from photos, send Telegram messages to the boss, and give CEO-level business advice. "
            f"When you learn something important about the boss or business, remember it. "
            f"Be concise, warm, and action-oriented. Speak like a trusted CEO advisor."
        )
    except Exception as e:
        context = f"You are the AI CEO assistant for a retail/thrift store. Help with everything. Error loading context: {e}"

    # Build messages
    messages = [{'role': 'system', 'content': context}]
    for h in history[-10:]:
        if h.get('role') in ('user', 'assistant') and h.get('content'):
            messages.append({'role': h['role'], 'content': h['content']})

    # Current user message (with optional image)
    if image_b64:
        user_content = [
            {'type': 'text', 'text': user_message or 'What is this item? Suggest a price and description for my thrift store.'},
            {'type': 'image_url', 'image_url': {'url': f'data:{image_mime};base64,{image_b64}'}}
        ]
    else:
        user_content = user_message

    messages.append({'role': 'user', 'content': user_content})

    payload = json.dumps({
        'model':    model,
        'messages': messages,
        'stream':   False
    }).encode()

    try:
        req = _ur.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=payload,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type':  'application/json',
                'HTTP-Referer':  'https://liberty-emporium.ai',
                'X-Title':       'Liberty Inventory AI',
            }
        )
        with _ur.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read())
        reply = result['choices'][0]['message']['content']
        # Update AI memory from this conversation
        if slug:
            try:
                ai_memory = load_ai_memory(slug)
                ai_memory = extract_memory_updates(reply, ai_memory)
                if not ai_memory.get('created_at'):
                    ai_memory['created_at'] = datetime.datetime.now().isoformat()
                save_ai_memory(slug, ai_memory)
            except Exception:
                pass
        return jsonify({'reply': reply})
    except _ue.HTTPError as e:
        body = ''
        try: body = e.read().decode()
        except: pass
        return jsonify({'error': f'OpenRouter error {e.code}: {body or e.reason}'}), 502
    except _ue.URLError as e:
        return jsonify({'error': f'Connection error: {e.reason}'}), 502
    except Exception as e:
        return jsonify({'error': str(e)}), 502
