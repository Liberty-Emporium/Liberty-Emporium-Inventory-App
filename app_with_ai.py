import os
import csv
import json
import uuid
import shutil
import base64
import hashlib
import datetime
import io
import tempfile
import threading
import time
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, send_file, send_from_directory, make_response)
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
app.secret_key = os.environ.get('SECRET_KEY', 'liberty-emporium-secret-2026')

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

INVENTORY_FILE = os.path.join(DATA_DIR, 'inventory.csv')
UPLOAD_FOLDER  = os.path.join(DATA_DIR, 'uploads')
BACKUP_FOLDER  = os.path.join(DATA_DIR, 'backups')
ADS_FOLDER     = os.path.join(DATA_DIR, 'ads')
MUSIC_FOLDER   = os.path.join(DATA_DIR, 'music')
USERS_FILE     = os.path.join(DATA_DIR, 'users.json')
CUSTOMERS_DIR  = os.path.join(DATA_DIR, 'customers')
PENDING_FILE   = os.path.join(DATA_DIR, 'pending_users.json')
SALE_FILE      = os.path.join(DATA_DIR, 'sale_state.json')

for d in [UPLOAD_FOLDER, BACKUP_FOLDER, ADS_FOLDER, MUSIC_FOLDER]:
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

def get_ai_api_key():
    """Get the unified Claude API key.
    Checks: 1) app config JSON (admin-entered) → 2) store_config.json → 3) env var.
    This is the SINGLE source for ALL AI features.
    """
    # 1. Check app config file (set by admin via settings page)
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

    # 2. Check store_config.json
    cfg = load_store_config()
    key = cfg.get('anthropic_api_key', '').strip()
    if key:
        return key

    # 3. Fall back to environment variable (avoid infinite recursion)
    return os.environ.get('ANTHROPIC_API_KEY', '').strip()

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
    'store_name': 'Liberty Emporium & Thrift',
    'tagline': 'Inventory Management',
    'contact_email': 'alexanderjay70@gmail.com',
    'jay_email': 'alexanderjay70@gmail.com',
    'primary_color': '#2c3e50',
    'secondary_color': '#27ae60',
    'accent_color': '#4f46e5',
    'logo_url': '',  # empty = use emoji fallback
    'logo_emoji': '🏪',
    'store_description': 'RetailTrack — A beautiful inventory management app for your store.',
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
    return hashlib.sha256(pw.encode()).hexdigest()

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
    )

# ── Health check (no login required, for Railway) ─────────────────────────────
@app.route('/healthz')
def healthz():
    return 'ok', 200

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
def login():
    # Already logged in? Redirect to appropriate dashboard
    if session.get('logged_in') and not session.get('is_guest'):
        if session.get('store_slug'):
            return redirect(url_for('my_store'))
        return redirect(url_for('dashboard'))
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
        if username in users and users[username]['password'] == hash_password(password):
            user_record = users[username]
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
                    if su.get('password') == hash_password(password):
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
    return render_template('login.html', **ctx())

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

@app.route('/guest')
def guest():
    session['logged_in'] = True
    session['username']  = 'guest'
    session['is_guest']  = True
    return redirect(url_for('dashboard'))

@app.route('/signup', methods=['GET','POST'])
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

@app.route('/new', methods=['GET','POST'])
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
                file.save(os.path.join(get_store_paths(active_store_slug())['uploads'], filename))
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
        delete_type='product', back_url=url_for('product_view', sku=sku), **ctx())

@app.route('/delete/<sku>', methods=['POST'])
@login_required
def delete_product(sku):
    if session.get('is_guest'):
        flash('Guests cannot delete products.', 'error')
        return redirect(url_for('dashboard'))
    products = load_inventory()
    products = [p for p in products if p['SKU'] != sku]
    save_inventory(products)
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
@login_required
def ai_analyze():
    # Accept key from the request first (user-supplied), fall back to server env var
    api_key = request.form.get('api_key', '').strip() or get_ai_api_key()
    if not api_key:
        return jsonify({'error': 'No API key provided. Ask the admin to configure the Claude API key in App Settings.'})
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
    try:
        import urllib.request as ur
        import json as _json
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
        # Attach token usage so the frontend can calculate cost
        usage = result.get('usage', {})
        parsed['_usage'] = {
            'input_tokens':  usage.get('input_tokens', 0),
            'output_tokens': usage.get('output_tokens', 0),
        }
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
        "You write ad copy for a thrift and antique store called Liberty Emporium.\n"
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
                img_fpath = os.path.join(get_store_paths(active_store_slug())['uploads'], img_fname)
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

    store_info = 'Liberty Emporium & Thrift, 125 W Swannanoa Ave, Liberty NC 27298'

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
@login_required
@admin_required
def reject_user(username):
    pending = [p for p in load_pending() if p['username'] != username]
    save_pending(pending)
    flash(f'User {username} rejected.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/remove/<username>', methods=['POST'])
@login_required
@admin_required
def remove_user(username):
    users = load_users()
    users.pop(username, None)
    save_users(users)
    flash(f'User {username} removed.', 'success')
    return redirect(url_for('admin_users'))

# ── Admin – Backups ───────────────────────────────────────────────────────────
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
@login_required
@admin_required
def restore_backup(filename):
    src = os.path.join(BACKUP_FOLDER, filename)
    if os.path.exists(src):
        shutil.copy2(src, INVENTORY_FILE)
        flash(f'Inventory restored from {filename}!', 'success')
    return redirect(url_for('admin_backups'))

@app.route('/admin/backups/manual', methods=['POST'])
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

@app.route('/admin/settings/stripe', methods=['POST'])
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
@login_required
@admin_required
def admin_settings_smtp_test():
    cfg = get_smtp_config()
    ok, err = send_smtp_email(
        to=cfg['smtp_user'],
        subject='RetailTrack — SMTP Test',
        body='Your email settings are working correctly.'
    )
    if ok:
        flash('Test email sent successfully!', 'success')
    else:
        flash(f'Email failed: {err}', 'error')
    return redirect(url_for('admin_settings'))

@app.route('/admin/branding', methods=['GET','POST'])
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
CUSTOMERS_DIR = os.path.join(BASE_DIR, 'customers')
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
def start_trial():
    """Start a 14-day free trial — no payment collected. Jay follows up after trial ends."""
    if request.method == 'POST':
        store_name    = request.form.get('store_name', '').strip()
        contact_email = request.form.get('contact_email', '').strip()
        slug          = request.form.get('slug', '').strip()
        trial_start   = datetime.datetime.now().isoformat()
        trial_end     = (datetime.datetime.now() + datetime.timedelta(days=14)).strftime('%B %d, %Y')

        # Save as a lead so Jay can follow up
        leads = load_leads()
        leads.append({
            'store_name':    store_name,
            'contact_email': contact_email,
            'slug':          slug,
            'trial_start':   trial_start,
            'trial_end':     trial_end,
            'type':          'trial',
            'status':        'active',
            'created_at':    trial_start,
        })
        save_leads(leads)

        return render_template('trial_confirmation.html',
            store_name=store_name,
            contact_email=contact_email,
            trial_end=trial_end,
            slug=slug,
            **ctx()
        )
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
    stores = list_client_stores()
    plan_prices = {'starter': 299, 'pro': 499, 'enterprise': 799}
    total_revenue = sum(plan_prices.get(s.get('plan', 'starter'), 0) for s in stores)
    active_count    = sum(1 for s in stores if s.get('status') == 'active')
    suspended_count = sum(1 for s in stores if s.get('status') == 'suspended')
    return render_template('overseer_dashboard.html',
        stores=stores,
        total_revenue=total_revenue,
        active_count=active_count,
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
@login_required
@overseer_required
def overseer_reset_password(slug):
    import secrets
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
    import secrets as _sec
    temp_pw = _sec.token_urlsafe(10)
    email = cfg.get('contact_email', '')
    if email in users:
        users[email]['password'] = hash_password(temp_pw)
        with open(users_file, 'w') as f:
            json.dump(users, f, indent=2)
        flash(f'Password reset. Temp password: {temp_pw}', 'success')
    else:
        flash('User email not found in store users.', 'error')
    return redirect(url_for('overseer_client_detail', slug=slug))

@app.route('/overseer/client/<slug>/update', methods=['POST'])
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
    setup_fees = len(stores) * 99

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
    system_prompt = f"""You are the Overseer Assistant for RetailTrack, a SaaS inventory management app run by Jay Alexander (Liberty Emporium Programs).

{context}

You help Jay run his business. You can:
1. Answer questions about clients, trials, and revenue using the data above.
2. Draft follow-up emails — return them as JSON on its own line: {{"type":"email","to":"client@email.com","subject":"...","body":"..."}}
3. Trigger actions — return them as JSON on its own line: {{"action":"suspend","slug":"store-slug"}} or {{"action":"unsuspend","slug":"store-slug"}} or {{"action":"reset_password","slug":"store-slug"}}

Rules:
- Be concise and direct. Jay is busy.
- When drafting emails, be warm and professional — sign off as "— Jay, Liberty Emporium Programs"
- For suspend/unsuspend/reset_password, output ONLY the JSON on its own line (no extra text on that line).
- Never suggest deleting a store — that requires manual confirmation.
- If you don't know something, say so honestly."""

    payload = {
        'model': 'claude-haiku-4-5-20251001',
        'max_tokens': 600,
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
        return jsonify({'reply': f'AI error: {e}', 'type': 'text'})

    # Parse reply — look for action or email JSON on its own line
    import re as _re
    for line in reply_text.splitlines():
        line = line.strip()
        if line.startswith('{') and line.endswith('}'):
            try:
                parsed = json.loads(line)
                if 'action' in parsed:
                    slug   = parsed.get('slug', '')
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


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True)
