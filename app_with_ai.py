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
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, send_file, send_from_directory)
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
stripe_enabled = bool(STRIPE_SECRET_KEY and STRIPE_SECRET_KEY.startswith('sk_live_'))

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
    if not os.path.exists(INVENTORY_FILE):
        return []
    with open(INVENTORY_FILE, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        products = list(reader)
    for p in products:
        imgs = [i.strip() for i in p.get('Images','').split(',') if i.strip()]
        p['image_list']   = imgs
        p['valid_images'] = [i for i in imgs if os.path.exists(os.path.join(UPLOAD_FOLDER, i))]
    return products

def save_inventory(products):
    fieldnames = ['SKU','Title','Description','Category','Condition','Price',
                  'Cost Paid','Status','Date Added','Images','Section','Shelf']
    _backup_inventory()
    with open(INVENTORY_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(products)

def _backup_inventory():
    if not os.path.exists(INVENTORY_FILE):
        return
    ts  = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = os.path.join(BACKUP_FOLDER, f'inventory_{ts}.csv')
    shutil.copy2(INVENTORY_FILE, dst)
    backups = sorted(
        [f for f in os.listdir(BACKUP_FOLDER) if f.endswith('.csv')],
        reverse=True
    )
    for old in backups[MAX_BACKUPS:]:
        os.remove(os.path.join(BACKUP_FOLDER, old))

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
    return dict(
        store_name=STORE_NAME,
        demo_mode=DEMO_MODE,
        demo_contact_email=CONTACT_EMAIL,
        stats=stats,
        sale_state=sale_state,
        user_role='admin' if is_admin else 'guest',
        store_config=load_store_config(),
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
    # Already logged in? Redirect to dashboard
    if session.get('logged_in') and not session.get('is_guest'):
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        if username == ADMIN_USER and password == ADMIN_PASS:
            session['logged_in'] = True
            session['username']  = ADMIN_USER
            session['is_guest']  = False
            session.permanent    = True
            app.permanent_session_lifetime = datetime.timedelta(hours=8)
            flash('Welcome back, Admin!', 'success')
            return redirect(url_for('dashboard'))
        users = load_users()
        if username in users and users[username]['password'] == hash_password(password):
            session['logged_in'] = True
            session['username']  = username
            session['is_guest']  = False
            session.permanent    = True
            app.permanent_session_lifetime = datetime.timedelta(hours=8)
            flash(f'Welcome, {username}!', 'success')
            return redirect(url_for('dashboard'))
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
                file.save(os.path.join(UPLOAD_FOLDER, filename))
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
                file.save(os.path.join(UPLOAD_FOLDER, filename))
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
    return send_from_directory(UPLOAD_FOLDER, filename)

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
            filepath = os.path.join(UPLOAD_FOLDER, filename)
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
        filepath  = os.path.join(UPLOAD_FOLDER, filename)
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

# ── Ad Generator ──────────────────────────────────────────────────────────────
@app.route('/ads')
@login_required
def ad_generator():
    products = load_inventory()
    return render_template('ad_generator.html', products=products, **ctx())

@app.route('/generate-ads', methods=['POST'])
@login_required
def generate_ads():
    data     = request.get_json()
    products = data.get('products', [])
    style    = data.get('style', 'elegant')
    use_json_response = request.headers.get('Accept') == 'application/json' or request.is_json

    style_configs = {
        'elegant': {'bg': '#1a1a2e', 'accent': '#f0c040', 'header': '#16213e'},
        'bright':  {'bg': '#ffffff', 'accent': '#e74c3c', 'header': '#2c3e50'},
        'nature':  {'bg': '#2d4a3e', 'accent': '#a8d5a2', 'header': '#1a3329'},
        'modern':  {'bg': '#2c3e50', 'accent': '#3498db', 'header': '#1a252f'},
    }
    cfg     = style_configs.get(style, style_configs['elegant'])
    bg_hex  = cfg['bg']
    acc_hex = cfg['accent']
    hdr_hex = cfg['header']

    generated = []
    for p in products:
        sku           = p.get('sku', 'UNKNOWN')
        title         = p.get('title', 'Untitled')
        price         = p.get('price', '0.00')
        description   = p.get('description', '')
        image_url     = p.get('image', '')
        product_url   = f"https://libertye.pythonanywhere.com/product/{sku}"

        ts            = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        html_filename = f"ad_{sku}_{ts}.html"
        html_filepath = os.path.join(ADS_FOLDER, html_filename)

        if image_url:
            img_tag = f'<img src="{image_url}" alt="{title}" class="ad-img">'
        else:
            img_tag = f'<div style="background:{hdr_hex};height:300px;display:flex;align-items:center;justify-content:center;color:{acc_hex};font-size:4rem;">🏪</div>'

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title} — Liberty Emporium</title>
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ background:{bg_hex}; font-family:'Segoe UI',sans-serif; display:flex; align-items:center; justify-content:center; min-height:100vh; }}
    .ad-wrap {{ width:600px; background:{hdr_hex}; border-radius:16px; overflow:hidden; box-shadow:0 8px 32px rgba(0,0,0,0.5); }}
    .ad-img {{ width:100%; display:block; }}
    .ad-body {{ padding:1.5rem; }}
    .ad-title {{ color:{acc_hex}; font-size:1.4rem; font-weight:700; margin-bottom:0.5rem; }}
    .ad-price {{ color:white; font-size:2rem; font-weight:900; margin-bottom:0.75rem; }}
    .ad-desc  {{ color:#ccc; font-size:0.95rem; line-height:1.5; margin-bottom:1rem; }}
    .ad-footer {{ background:{hdr_hex}; padding:1rem 1.5rem; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.75rem; border-top:1px solid rgba(255,255,255,0.1); }}
    .ad-address {{ color:{acc_hex}; font-size:0.85rem; }}
    .view-btn {{ background:{acc_hex}; color:{bg_hex}; border:none; padding:0.75rem 1.75rem; border-radius:8px; font-size:1rem; font-weight:bold; cursor:pointer; text-decoration:none; display:inline-block; }}
    .view-btn:hover {{ opacity:0.85; }}
  </style>
</head>
<body>
  <div class="ad-wrap">
    {img_tag}
    <div class="ad-body">
      <div class="ad-title">{title}</div>
      <div class="ad-price">${price}</div>
      <div class="ad-desc">{description[:200]}</div>
    </div>
    <div class="ad-footer">
      <div class="ad-address">📍 125 W Swannanoa Ave, Liberty NC 27298</div>
      <a href="{product_url}" class="view-btn" target="_blank">🛍️ View Product</a>
    </div>
  </div>
</body>
</html>"""
        with open(html_filepath, 'w') as hf:
            hf.write(html_content)
        generated.append({'filename': html_filename, 'product_title': title, 'type': 'html'})

    if use_json_response:
        return jsonify({'success': True, 'files': generated})
    return render_template('ads.html', generated=[g['filename'] for g in generated], **ctx())

@app.route('/ads/<filename>')
def view_ad(filename):
    return send_from_directory(ADS_FOLDER, filename)

@app.route('/download-ad/<filename>')
@login_required
def download_ad(filename):
    return send_from_directory(ADS_FOLDER, filename, as_attachment=True)

# ── Music Library ─────────────────────────────────────────────────────────────
@app.route('/music')
@login_required
def list_music():
    tracks = []
    for fname in sorted(os.listdir(MUSIC_FOLDER)):
        if fname.lower().endswith('.mp3'):
            display = os.path.splitext(fname)[0]
            display = display.replace('-', ' ').replace('_', ' ')
            display = ' '.join(w.capitalize() for w in display.split())
            if len(display) > 40:
                display = display[:40]
            tracks.append({'filename': fname, 'display_name': display})
    return jsonify(tracks)


@app.route('/music/<filename>')
@login_required
def serve_music(filename):
    return send_from_directory(MUSIC_FOLDER, filename)

@app.route('/upload-music-temp', methods=['POST'])
def upload_music_temp():
    """Receives a music file upload and stores it in a temp location.
    Returns a token the client passes back to /generate-video-ad."""
    import tempfile as _tf
    try:
        if 'music_file' not in request.files:
            return jsonify({'error': 'No file provided.'}), 400
        f = request.files['music_file']
        if not f or f.filename == '':
            return jsonify({'error': 'No file selected.'}), 400
        if f.content_length and f.content_length > 20 * 1024 * 1024:
            return jsonify({'error': 'File must be under 20 MB.'}), 400
        upload_dir = os.path.join(DATA_DIR, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        # Use a unique filename based on time + random
        import uuid, time
        fname = f"music_{int(time.time())}_{uuid.uuid4().hex[:8]}.mp3"
        fpath = os.path.join(upload_dir, fname)
        f.save(fpath)
        if os.path.getsize(fpath) > 20 * 1024 * 1024:
            os.unlink(fpath)
            return jsonify({'error': 'File must be under 20 MB.'}), 400
        return jsonify({'token': fname})
    except Exception as e:
        app.logger.error(f"Music upload error: {e}", exc_info=True)
        return jsonify({'error': f'Upload failed: {str(e)}'}), 500

def _draw_text_layer(W, H, store_name, title, price, description, cta_text, tagline,
                     font_bold_path, font_reg_path, template_config):
    """Return a transparent RGBA PIL Image with all text for a full-bleed ad."""
    from PIL import Image as _Img, ImageDraw as _Draw, ImageFont as _Font
    import textwrap as _tw

    layer = _Img.new('RGBA', (W, H), (0, 0, 0, 0))
    draw  = _Draw.Draw(layer)

    # Scale fonts by width for visual impact — hard clip prevents overflow
    sz_store = max(18, int(W * 0.016))
    sz_title = max(48, int(W * 0.048))
    sz_price = max(64, int(W * 0.072))
    sz_desc  = max(20, int(W * 0.020))
    sz_cta   = max(24, int(W * 0.026))

    try:
        f_store = _Font.truetype(font_reg_path,  sz_store)
        f_title = _Font.truetype(font_bold_path, sz_title)
        f_price = _Font.truetype(font_bold_path, sz_price)
        f_desc  = _Font.truetype(font_reg_path,  sz_desc)
        f_cta   = _Font.truetype(font_bold_path, sz_cta)
    except Exception:
        default = _Font.load_default()
        f_store = f_title = f_price = f_desc = f_cta = default

    def _hex(h, a=255):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4)) + (a,)

    accent = _hex(template_config['accent'])
    white  = (255, 255, 255, 255)
    dimmed = (200, 200, 220, 255)
    desc_c = (220, 220, 235, 255)
    shadow = (0, 0, 0, 160)

    # Hard clip: never draw text below this line
    bottom_limit = H - int(H * 0.02)

    def _txt(pos, text, font, fill):
        """Draw text with drop shadow. Silently skips if below bottom_limit."""
        if pos[1] + font.size > bottom_limit:
            return
        sx = max(1, font.size // 30)
        sy = max(1, font.size // 30)
        draw.text((pos[0] + sx, pos[1] + sy), text, font=font, fill=shadow)
        draw.text(pos, text, font=font, fill=fill)

    grad_top = int(H * 0.50)   # gradient covers bottom 50% — enough room for big text
    x = int(W * 0.05)
    y = grad_top + int(H * 0.020)

    _txt((x, y), store_name, f_store, dimmed)
    y += sz_store + max(6, int(sz_store * 0.4))

    chars_t = max(10, int((W * 0.88) / (sz_title * 0.58)))
    for line in _tw.wrap(title, width=chars_t)[:2]:
        _txt((x, y), line, f_title, accent)
        y += sz_title + max(4, int(sz_title * 0.08))
    y += max(4, int(sz_title * 0.10))

    _txt((x, y), f'${price}', f_price, white)
    y += sz_price + max(6, int(sz_price * 0.12))

    if description:
        chars_d = max(15, int((W * 0.88) / (sz_desc * 0.58)))
        for line in _tw.wrap(description, width=chars_d)[:1]:  # 1 line only
            _txt((x, y), line, f_desc, desc_c)
            y += sz_desc + max(4, int(sz_desc * 0.2))
        y += max(4, int(sz_desc * 0.2))

    if cta_text:
        chars_c = max(10, int((W * 0.88) / (sz_cta * 0.58)))
        for line in _tw.wrap(cta_text, width=chars_c)[:2]:
            _txt((x, y), line, f_cta, accent)
            y += sz_cta + max(4, int(sz_cta * 0.2))

    if tagline:
        ty = H - sz_store - int(H * 0.025)
        _txt((x, ty), tagline, f_store, (180, 180, 200, 255))

    return layer


# ── AI Voiceover for Video Ads ────────────────────────────────────────────────

def _build_humanlike_script(product, store_name, index):
    """Build a natural-sounding, conversational ad script for one product.
    
    Uses light SSML — only pauses for pacing. No pitch warping or rate shifting.
    Let the neural voice do its thing naturally.
    """
    title = product.get('title', 'this item')
    price = product.get('price', '0.00')
    desc = product.get('description', '')
    condition = product.get('condition', '')
    category = product.get('category', '')
    
    # Clean condition for speech
    cond = condition.lower() if condition else ''
    cond = cond.replace('new', 'brand new').replace('good', 'good shape').replace('like new', 'almost new')
    
    # Conversational product descriptions
    product_lines = [
        f"Take a look at this <emphasis level='moderate'>{title}</emphasis>.<break time='300ms'/>",
        f"Check this out — <emphasis level='moderate'>{title}</emphasis>.<break time='300ms'/>",
        f"Now this one's really nice — <emphasis level='moderate'>{title}</emphasis>.<break time='300ms'/>",
    ]
    
    # Build script piece by piece
    # Only include description if it exists and is short
    desc_text = ""
    if desc:
        # Truncate and clean
        clean_desc = desc.strip().rstrip('.')
        if len(clean_desc) > 100:
            clean_desc = clean_desc[:97] + "..."
        desc_text = f"<break time='200ms'/>{clean_desc}."
    
    # Condition
    cond_text = ""
    if cond:
        cond_text = f"<break time='200ms'/>It's in {cond} condition."
    
    # Price
    # Make price sound natural
    try:
        price_num = float(price)
        if price_num == int(price_num):
            price_speech = f"${int(price_num)}"
        else:
            price_speech = f"${price_num:.2f}"
    except:
        price_speech = f"${price}"
    
    price_text = f"<break time='250ms'/>Only <emphasis level='strong'>{price_speech}</emphasis>."
    
    # Assemble
    intro = [
        f"<speak>Welcome to <emphasis level='moderate'>{store_name}</emphasis>.<break time='400ms'/>We've got something special today.<break time='300ms'/>",
        f"<speak>Hey there, welcome to <emphasis level='moderate'>{store_name}</emphasis>!<break time='300ms'/>Let me show you what we've got.<break time='200ms'/>",
    ]
    
    outro = [
        f"<break time='500ms'/>That's what's waiting for you at <emphasis level='moderate'>{store_name}</emphasis>.<break time='300ms'/>Come see us today.</speak>",
        f"<break time='500ms'/>Stop by <emphasis level='moderate'>{store_name}</emphasis> and grab these deals while they last.<break time='200ms'/>See you soon!</speak>",
    ]
    
    line = product_lines[index % len(product_lines)]
    
    # First product gets intro, last gets outro
    if index == 0:
        return intro[0] + line + desc_text + cond_text + price_text
    else:
        return line + desc_text + cond_text + price_text + (outro[index % len(outro)] if index > 0 else "")


def _generate_product_voiceover(product, store_name, tmp_files, index, voice='en-US-ChristopherNeural'):
    """Generate a natural-sounding voiceover for one product using Edge TTS."""
    import asyncio
    import edge_tts
    
    try:
        # Try nest_asyncio first (for Flask compatibility)
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        pass
    
    # Build conversational script with SSML
    script = _build_humanlike_script(product, store_name, index)
    
    # Generate TTS
    out_file = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False).name
    tmp_files.append(out_file)
    
    try:
        communicate = edge_tts.Communicate(script, voice)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(communicate.save(out_file))
        loop.close()
        
        if not os.path.exists(out_file) or os.path.getsize(out_file) < 100:
            return None, 0, ''
        
        # Get duration
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', out_file],
            capture_output=True, text=True
        )
        duration = float(result.stdout.strip())
        return out_file, duration, script
    except Exception as e:
        app.logger.warning(f"Voiceover failed: {e}")
        if os.path.exists(out_file):
            os.unlink(out_file)
        return None, 0, ''


# ── Free Royalty-Free Music Generation ────────────────────────────────────────

def _generate_custom_voiceover(text, tmp_files, voice='en-US-ChristopherNeural'):
    """Generate voiceover from a custom user-written script using Edge TTS."""
    import asyncio
    import edge_tts
    
    out_file = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False).name
    tmp_files.append(out_file)
    
    # Wrap in minimal SSML for natural delivery
    script = f'<speak>{text}</speak>'
    
    try:
        communicate = edge_tts.Communicate(script, voice)
        asyncio.run(communicate.save(out_file))
        
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', out_file],
            capture_output=True, text=True
        )
        duration = float(result.stdout.strip())
        return out_file, duration
    except Exception as e:
        app.logger.warning(f"Custom voiceover failed: {e}")
        return None, 0


def _generate_builtin_music(duration_seconds, tmp_files):
    """Generate a simple upbeat royalty-free music track using ffmpeg tone synthesis."""
    out_file = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False).name
    tmp_files.append(out_file)
    
    # Generate a pleasant 4-chord loop: C-G-Am-F pattern with simple melody
    # Using sine waves with envelope for a soft, professional sound
    total_dur = max(duration_seconds, 15)
    
    # Create a simple but pleasant background: alternating bass notes + soft pad
    cmd = [
        'ffmpeg', '-y',
        '-f', 'lavfi', '-i', f'sine=frequency=220:duration={total_dur}:sample_rate=44100',
        '-f', 'lavfi', '-i', f'sine=frequency=330:duration={total_dur}:sample_rate=44100',
        '-filter_complex',
        f'[0:a]volume=0.08[a0];[1:a]volume=0.04[a1];[a0][a1]amix=inputs=2:duration=longest[aout]',
        '-map', '[aout]',
        '-b:a', '128k',
        out_file,
    ]
    
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if os.path.exists(out_file) and os.path.getsize(out_file) > 1000:
            return out_file
    except Exception:
        pass
    
    return None


# ── Professional Video Intro/Outro Cards ──────────────────────────────────────

def _hex_rgb_for(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _draw_text_with_shadow(draw, text, pos, font, color):
    """Draw text with subtle shadow for depth."""
    x, y = pos
    draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 160))
    if len(color) == 3:
        color = color + (255,)
    draw.text((x, y), text, font=font, fill=color)


def _fit_font_size(text, font_path, max_width, max_size):
    """Find the largest font size that fits within max_width."""
    from PIL import ImageFont as _Font
    size = max_size
    while size > 12:
        font = _Font.truetype(font_path, size)
        bbox = font.getbbox(text)
        w = bbox[2] - bbox[0]
        if w <= max_width - 40:
            return size
        size -= 2
    return 24


def _make_intro_card(W, H, store_name, tagline, template_config, font_bold_path, font_reg_path):
    """Create an intro card for the video ad."""
    from PIL import Image as _Img, ImageDraw as _Draw, ImageFont as _Font
    
    accent_rgb = _hex_rgb_for(template_config['bg_dark'])
    accent = _hex_rgb_for(template_config['accent'])
    
    layer = _Img.new('RGB', (W, H), accent_rgb)
    draw = _Draw.Draw(layer)
    
    cx, cy = W // 2, H // 2
    
    # Subtle darker radial gradient
    max_r = max(W, H)
    for r in range(max_r, 0, -3):
        alpha_int = max(0, int(40 - 40 * r / max_r))
        if alpha_int > 5:
            darker = tuple(max(0, c - alpha_int) for c in accent_rgb)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=darker)
    
    # Accent line
    line_y = cy - 10
    draw.line([(int(W * 0.3), line_y), (int(W * 0.7), line_y)], fill=accent, width=2)
    
    # Store name
    font_sz = _fit_font_size(store_name or 'Your Store', font_bold_path, W, int(W * 0.7))
    font = _Font.truetype(font_bold_path, font_sz)
    bbox = draw.textbbox((0, 0), store_name or 'Your Store', font=font)
    w_text = bbox[2] - bbox[0]
    h_text = bbox[3] - bbox[1]
    
    x = (W - w_text) // 2
    y = cy - h_text - 15
    _draw_text_with_shadow(draw, store_name or 'Your Store', (x, y), font, (255, 255, 255))
    
    # Tagline
    if tagline and len(tagline.strip()) > 2:
        font_sz_t = max(18, int(font_sz * 0.5))
        font_t = _Font.truetype(font_reg_path, font_sz_t)
        bbox_t = draw.textbbox((0, 0), tagline.strip(), font=font_t)
        w_t = bbox_t[2] - bbox_t[0]
        _draw_text_with_shadow(draw, tagline.strip(), ((W - w_t) // 2, cy + 20), font_t, (200, 200, 220))
    
    return layer


def _make_outro_card(W, H, store_name, cta_text, template_config, font_bold_path, font_reg_path):
    """Create a call-to-action outro card for the video ad."""
    from PIL import Image as _Img, ImageDraw as _Draw, ImageFont as _Font
    
    accent_rgb = _hex_rgb_for(template_config['bg_dark'])
    accent = _hex_rgb_for(template_config['accent'])
    overlay_text = _hex_rgb_for(template_config['overlay_text'])
    
    layer = _Img.new('RGB', (W, H), accent_rgb)
    draw = _Draw.Draw(layer)
    
    cx, cy = W // 2, H // 2
    max_r = max(W, H)
    for r in range(max_r, 0, -3):
        alpha_int = max(0, int(60 - 60 * r / max_r))
        if alpha_int > 5:
            darker = tuple(max(0, c - alpha_int) for c in accent_rgb)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=darker)
    
    # Accent bar
    bar_h = 3
    draw.rectangle([(int(W * 0.3), cy - 30), (int(W * 0.7), cy - 30 + bar_h * 2)], fill=accent)
    
    # "Available Now" label
    avail_sz = max(24, int(W * 0.022))
    avail_font = _Font.truetype(font_reg_path, avail_sz)
    available_text = "Available Now"
    bbox = draw.textbbox((0, 0), available_text, font=avail_font)
    w = bbox[2] - bbox[0]
    _draw_text_with_shadow(draw, available_text, ((W - w) // 2, int(H * 0.28)), avail_font, accent)
    
    # Store name
    name_sz = _fit_font_size(store_name or 'Your Store', font_bold_path, W, int(W * 0.6))
    name_font = _Font.truetype(font_bold_path, name_sz)
    bbox = draw.textbbox((0, 0), store_name or 'Your Store', font=name_font)
    w = bbox[2] - bbox[0]
    _draw_text_with_shadow(draw, store_name or 'Your Store', ((W - w) // 2, int(H * 0.38)), name_font, (255, 255, 255))
    
    # CTA text
    if cta_text:
        cta_sz = max(20, int(W * 0.02))
        cta_font = _Font.truetype(font_reg_path, cta_sz)
        tw = __import__('textwrap').TextWrapper(width=60)
        lines = tw.wrap(cta_text)
        ty = cy + 10
        for line in lines[:3]:
            bbox = draw.textbbox((0, 0), line, font=cta_font)
            w = bbox[2] - bbox[0]
            _draw_text_with_shadow(draw, line, ((W - w) // 2, ty), cta_font, overlay_text)
            ty += cta_sz + 8
    
    return layer


@app.route('/generate-video-ad', methods=['POST'])
@login_required
def generate_video_ad():
    import subprocess, tempfile, textwrap, shutil as _shutil, asyncio

    try:
        # ── Parse inputs ──────────────────────────────────────────────────────
        products  = json.loads(request.form.get('products', '[]'))
        style     = request.form.get('style', 'slideshow')
        duration  = max(10, min(60, int(request.form.get('duration', 30))))
        template  = request.form.get('template', 'default')
        format_str = request.form.get('format', '1920x1080')
        cta_text  = request.form.get('cta_text', '').strip()
        tagline   = request.form.get('tagline', '').strip()
        logo_position = request.form.get('logo_position', 'top-right')
        logo_size = request.form.get('logo_size', 'medium')
        enable_voiceover = request.form.get('voiceover', 'off') == 'on'
        store_name_vo = request.form.get('store_name_vo', '').strip()
        voice_select = request.form.get('voice_select', 'en-US-GuyNeural')
        
        # Voiceover toggle
        enable_voiceover = request.form.get('voiceover', 'off') == 'on'
        store_name_vo  = request.form.get('store_name_vo', '').strip()

        # ── Template color schemes ─────────────────────────────────────────────
        templates = {
            'default':       {'accent': '#f0c040', 'bg_dark': '#1a1a2e', 'overlay_text': '#f0f0f5'},
            'holiday':       {'accent': '#ff2d2d', 'bg_dark': '#0d3d22', 'overlay_text': '#ffff00'},
            'valentine':     {'accent': '#ff1493', 'bg_dark': '#4a0e4e', 'overlay_text': '#ffb6c1'},
            'spring':        {'accent': '#00d084', 'bg_dark': '#f5f5f5', 'overlay_text': '#2d5f3f'},
            'summer':        {'accent': '#ffa500', 'bg_dark': '#003d82', 'overlay_text': '#fff76d'},
            'fall':          {'accent': '#ff7f50', 'bg_dark': '#3d2817', 'overlay_text': '#ffe4b5'},
            'blackfriday':   {'accent': '#ffff00', 'bg_dark': '#000000', 'overlay_text': '#ff6600'},
            'backtoschool':  {'accent': '#4169e1', 'bg_dark': '#1a1a38', 'overlay_text': '#fff176'},
        }

        # ── Format dimensions ──────────────────────────────────────────────────
        formats = {
            '1920x1080': (1920, 1080),
            '1080x1350': (1080, 1350),
            '1080x1920': (1080, 1920),
            '1200x628':  (1200, 628),
            '1080x1080': (1080, 1080),
        }

        template_config = templates.get(template, templates['default'])
        video_size = formats.get(format_str, formats['1920x1080'])

        # Determine music path
        music_file_upload = request.files.get('music_file')
        music_track_name  = request.form.get('music_track', '').strip()
        music_token       = request.form.get('music_token', '').strip()

        if not products:
            return jsonify({'error': 'No products provided.'})
        if not music_file_upload and not music_track_name and not music_token:
            return jsonify({'error': 'No music track selected.'})

        # Find ffmpeg — works on both apt (/usr/bin) and nixpacks installs
        ffmpeg_path = _shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
        if not os.path.exists(ffmpeg_path):
            return jsonify({'error': 'ffmpeg not found on this server.'})

        generated = []
        tmp_files = []

        # ── AI Voiceover: generate narration per product ──────────────────────
        voiceover_durations = []
        custom_voice_script = request.form.get('voice_script', '').strip()
        
        if enable_voiceover:
            if custom_voice_script:
                # Use user's custom script — one big narration piece
                vo_file, vo_dur = _generate_custom_voiceover(
                    custom_voice_script, tmp_files, voice_select
                )
                if vo_file:
                    voiceover_durations.append({
                        'file': vo_file, 
                        'duration': vo_dur,
                        'script': custom_voice_script,
                        'is_custom': True
                    })
                if vo_file:
                    # Adjust duration to fit voiceover
                    duration = max(duration, int(vo_dur + 3))
            elif store_name_vo:
                # Use auto-generated per-product narration
                for i, p in enumerate(products):
                    vo_file, vo_dur, vo_script = _generate_product_voiceover(
                        p, store_name_vo, tmp_files, i, voice_select
                    )
                    if vo_file:
                        voiceover_durations.append({'file': vo_file, 'duration': vo_dur, 'script': vo_script})
                    else:
                        voiceover_durations.append(None)
                
                # Adjust duration to fit voiceovers
                valid_vo = [vd for vd in voiceover_durations if vd is not None]
                if valid_vo:
                    total_voice = sum(vd['duration'] for vd in valid_vo)
                    duration = max(duration, int(total_voice + 3))

        # Resolve music path — ALWAYS ensure we have audio
        music_path = None
        if music_token:
            # Pre-uploaded via /upload-music-temp
            music_path = os.path.join(DATA_DIR, 'uploads', os.path.basename(music_token))
            if not os.path.exists(music_path):
                music_path = None
            else:
                tmp_files.append(music_path)  # clean up after generation
        elif music_file_upload:
            # Direct file upload
            tmp_music = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
            music_file_upload.save(tmp_music.name)
            tmp_files.append(tmp_music.name)
            if os.path.getsize(tmp_music.name) > 20 * 1024 * 1024:
                return jsonify({'error': 'Uploaded MP3 must be under 20 MB.'})
            music_path = tmp_music.name
        elif music_track_name and music_track_name != 'none':
            # Built-in track
            music_path = os.path.join(MUSIC_FOLDER, os.path.basename(music_track_name))
            if not os.path.exists(music_path):
                music_path = None  # Will fall through to auto-generate

        # If no music file found, auto-generate one — silence is worse than bad music
        if not music_path:
            app.logger.info("No music track available, generating fallback music")
            generated_music = _generate_builtin_music(duration, tmp_files)
            if generated_music:
                music_path = generated_music
                tmp_files.append(generated_music)
            else:
                app.logger.warning("Fallback music generation also failed")

        # Save uploaded logo to a temp file if provided
        logo_path = None
        logo_file_upload = request.files.get('logo_file')
        if logo_file_upload:
            tmp_logo = tempfile.NamedTemporaryFile(suffix=os.path.splitext(logo_file_upload.filename)[1], delete=False)
            logo_file_upload.save(tmp_logo.name)
            tmp_files.append(tmp_logo.name)
            if os.path.getsize(tmp_logo.name) > 5 * 1024 * 1024:
                return jsonify({'error': 'Logo must be under 5 MB.'})
            logo_path = tmp_logo.name

        W, H = video_size

        from PIL import Image as _Img, ImageDraw as _Draw, ImageFont as _Font

        def _hex_rgb(h):
            h = h.lstrip('#')
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

        bg_dark_rgb = _hex_rgb(template_config['bg_dark'])
        font_bold   = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

        # Check if fonts exist on this server (they may not on Railway without fonts-dejavu-core)
        font_exists = os.path.exists(font_bold)

        font_reg    = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

        # ── Build one bg+text image pair per product ──────────────────────────
        bg_files   = []   # paths to bg JPEG files
        text_files = []   # paths to text PNG files
        titles     = []

        for p in products:
            sku         = p.get('sku', 'UNKNOWN')
            title       = p.get('title', 'Untitled')
            price       = p.get('price', '0.00')
            description = p.get('description', '')
            image_url   = p.get('image', '')
            titles.append(title)

            # Start with solid bg_dark fill
            bg_frame = _Img.new('RGB', (W, H), color=bg_dark_rgb)

            # Contain product photo: full product always visible, letterboxed with bg_dark
            if image_url:
                img_filename = image_url.split('/')[-1]
                img_path = os.path.join(UPLOAD_FOLDER, img_filename)
                if os.path.exists(img_path):
                    try:
                        prod_img = _Img.open(img_path)
                        prod_img = fix_image_orientation(prod_img)
                        prod_img = prod_img.convert('RGB')
                        scale = min(W / prod_img.width, H / prod_img.height)
                        new_w = int(prod_img.width * scale)
                        new_h = int(prod_img.height * scale)
                        prod_img = prod_img.resize((new_w, new_h), _Img.LANCZOS)
                        px = (W - new_w) // 2
                        py = (H - new_h) // 2
                        bg_frame.paste(prod_img, (px, py))
                    except Exception:
                        pass

            # Dark gradient overlay: bottom 50%, always black-based
            grad_h = int(H * 0.50)
            grad_y = H - grad_h
            grad_img  = _Img.new('RGBA', (W, grad_h), (0, 0, 0, 0))
            grad_draw = _Draw.Draw(grad_img)
            for row in range(grad_h):
                alpha = int(240 * row / grad_h)
                grad_draw.line([(0, row), (W - 1, row)], fill=(0, 0, 0, alpha))
            bg_frame = bg_frame.convert('RGBA')
            bg_frame.paste(grad_img, (0, grad_y), grad_img)
            bg_frame = bg_frame.convert('RGB')

            # Logo on background layer (optional)
            if logo_path and os.path.exists(logo_path):
                try:
                    logo_img = _Img.open(logo_path)
                    logo_img = fix_image_orientation(logo_img)
                    logo_img = logo_img.convert('RGBA')
                    size_map = {'small': 60, 'medium': 90, 'large': 120}
                    target_size = int(size_map.get(logo_size, 90) * (W / 1280.0))
                    logo_img.thumbnail((target_size, target_size), _Img.LANCZOS)
                    padding = int(W * 0.015)
                    pos_map = {
                        'top-left':     (padding, padding),
                        'top-right':    (W - logo_img.width - padding, padding),
                        'bottom-left':  (padding, H - logo_img.height - padding),
                        'bottom-right': (W - logo_img.width - padding, H - logo_img.height - padding),
                    }
                    pos = pos_map.get(logo_position, pos_map['top-right'])
                    bg_frame = bg_frame.convert('RGBA')
                    bg_frame.paste(logo_img, pos, logo_img)
                    bg_frame = bg_frame.convert('RGB')
                except Exception:
                    pass

            # Text layer: transparent RGBA PNG, text only
            text_layer = _draw_text_layer(
                W, H,
                store_name='Liberty Emporium',
                title=title, price=price,
                description=description,
                cta_text=cta_text,
                tagline=tagline,
                font_bold_path=font_bold,
                font_reg_path=font_reg,
                template_config=template_config,
            )

            tmp_bg   = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
            tmp_text = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            bg_frame.save(tmp_bg.name,     'JPEG', quality=92)
            text_layer.save(tmp_text.name, 'PNG')
            tmp_files.extend([tmp_bg.name, tmp_text.name])
            bg_files.append(tmp_bg.name)
            text_files.append(tmp_text.name)

        # ── Professional video generation with intro, crossfades & progress ───
        n = len(bg_files)
        # Reserve 2.5s for intro + 3s for outro
        intro_dur = 2.5
        outro_dur = 3.0
        product_dur = max(duration - intro_dur - outro_dur, 6.0)
        t_per = round(product_dur / n, 3)
        crossfade_dur = min(0.8, t_per * 0.25)  # 25% of per-product time, max 0.8s

        # Create intro card image
        store_display = request.form.get('store_name', 'Your Store').strip()
        intro_img = _make_intro_card(W, H, store_display, tagline, template_config, font_bold, font_reg)
        tmp_intro = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
        intro_img.save(tmp_intro.name, 'JPEG', quality=95)
        tmp_files.append(tmp_intro.name)

        # Create outro card image
        outro_cta = cta_text or 'Available Now — Visit Our Store Today!'
        outro_img = _make_outro_card(W, H, store_display, outro_cta, template_config, font_bold, font_reg)
        tmp_outro = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
        outro_img.save(tmp_outro.name, 'JPEG', quality=95)
        tmp_files.append(tmp_outro.name)

        # Total timeline
        total_dur = intro_dur + (n * t_per) + outro_dur
        fps = 25

        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        template_sfx = f'_{template}' if template != 'default' else ''
        format_sfx = f'_{format_str}' if format_str != '1920x1080' else ''
        sku_str = products[0].get('sku', 'UNKNOWN') if len(products) == 1 else f"{n}products"
        out_name = f'video_ad_{sku_str}{template_sfx}{format_sfx}_{ts}.mp4'
        out_path = os.path.join(ADS_FOLDER, out_name)

        # Build Ken Burns zoom directions: simple, tested expressions
        kb_configs = []
        directions = [
            ("zoompan=z='if(lte(on,1),1.0,zoom+0.003)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
             "zoom in slowly from center"),
            ("zoompan=z='if(lte(on,1),1.15,max(zoom-0.003,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
             "zoom out slowly from center"),
            ("zoompan=z='min(zoom+0.003,1.3)':x='if(lte(on,1),0,x+iw/100)':y='ih/2-(ih/zoom/2)'",
             "pan left to right"),
            ("zoompan=z='min(zoom+0.003,1.3)':x='if(lte(on,1),iw/5,max(0,x-iw/100))':y='ih/2-(ih/zoom/2)'",
             "pan right to left"),
        ]

        # Build ffmpeg inputs: intro + N bg + N text + outro [+ voiceover] [+ music]
        cmd = [ffmpeg_path, '-y']
        cmd += ['-loop', '1', '-framerate', str(fps), '-t', str(intro_dur), '-i', tmp_intro.name]
        for i in range(n):
            cmd += ['-loop', '1', '-framerate', str(fps), '-t', str(t_per), '-i', bg_files[i]]
            cmd += ['-loop', '1', '-framerate', str(fps), '-t', str(t_per), '-i', text_files[i]]
        cmd += ['-loop', '1', '-framerate', str(fps), '-t', str(outro_dur), '-i', tmp_outro.name]
        
        audio_inputs = 0  # track how many audio inputs we add
        
        # Add voiceover audio if enabled
        if enable_voiceover and voiceover_durations:
            for vd in voiceover_durations:
                if vd and vd.get('file'):
                    cmd += ['-i', vd['file']]
                    audio_inputs += 1
        
        # Add music if available
        if music_path:
            cmd += ['-i', music_path]
            audio_inputs += 1
        
        music_and_vo_start = 1 + 2 * n + 1  # after intro + 2*N + outro

        # Build filter_complex
        parts = []

        # Intro card: simple hold
        parts.append(f"[0:v]fps={fps}[intro]")

        # For each product: optionally apply Ken Burns + overlay text + progress bar
        for i in range(n):
            bg_idx = 1 + i
            txt_idx = 1 + n + i
            
            # Ken Burns with varying direction per product
            if style == 'kenburns':
                dur_frames = int(t_per * fps)
                zoom_expr = directions[i % len(directions)][0]
                parts.append(
                    f"[{bg_idx}:v]zoompan={zoom_expr}:d={dur_frames}:s={W}x{H}:fps={fps}[kb{i}]"
                )
                bg_ref = f"kb{i}"
            else:
                parts.append(f"[{bg_idx}:v]fps={fps}[bg{i}]")
                bg_ref = f"bg{i}"

            # Text overlay with fade-in
            parts.append(f"[{txt_idx}:v]fade=in:st=0:d=0.8:alpha=1[txt{i}]")
            parts.append(f"[{bg_ref}][txt{i}]overlay=0:0[prod{i}]")

        # Outro card
        outro_idx = 1 + 2 * n
        parts.append(f"[{outro_idx}:v]fps={fps}[outro]")

        # Chain: intro -> xfade products -> outro
        # First: intro + product0 with xfade
        parts.append(f"[intro][prod0]xfade=transition=fade:duration={crossfade_dur}:offset={intro_dur - crossfade_dur}[chain0]")

        for i in range(1, n):
            prev = f"chain{i-1}"
            prev_offset = intro_dur + sum(t_per for _ in range(i)) - crossfade_dur
            parts.append(f"[{prev}][prod{i}]xfade=transition=fade:duration={crossfade_dur}:offset={prev_offset:.3f}[chain{i}]")

        # Chain last product with outro
        last_chain = f"chain{n-1}" if n > 1 else f"chain0"
        outro_offset = intro_dur + (n * t_per) - crossfade_dur
        parts.append(f"[{last_chain}][outro]xfade=transition=fade:duration={crossfade_dur}:offset={outro_offset:.3f}[outv]")

        # Add progress bar: thin colored line at bottom that grows
        bar_h = max(4, int(H * 0.004))
        bar_y = H - bar_h - 2
        accent_rgb = _hex_rgb(template_config['accent'])
        parts.append(
            f"[outv]drawbox=x=0:y={bar_y}:w=iw*t/{total_dur:.3f}:h={bar_h}:color='0x{template_config['accent'].lstrip('#')}'@0.95:t=fill[final]"
        )
        map_label = '[final]'

        # Build filter_complex string
        fc = ';'.join(parts)

        # Handle audio: voiceover, music, both, or neither
        if audio_inputs == 0:
            # No audio sources
            fc_final = fc
            audio_args = ['-an']  # no audio track
        elif audio_inputs == 1:
            # Single audio source — direct map
            first_audio_idx = music_and_vo_start
            fc_final = fc
            audio_args = ['-map', f'{first_audio_idx}:a', '-c:a', 'aac', '-b:a', '128k', '-shortest']
        else:
            # Multiple audio sources — mix them into one
            mix_inputs = ''
            for i in range(audio_inputs):
                mix_inputs += f'[{music_and_vo_start + i}:a]'
            fc_final = fc + ';' + mix_inputs + f'amix=inputs={audio_inputs}:duration=longest:dropout_transition=0[mixed_a]'
            audio_args = ['-map', '[mixed_a]', '-c:a', 'aac', '-b:a', '128k', '-shortest']

        cmd += [
            '-filter_complex', fc_final,
            '-map', map_label,
        ] + audio_args + [
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '22',
            '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
            out_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return jsonify({'error': 'Video generation timed out (try a shorter duration or smaller format).'})
        if result.returncode != 0:
            return jsonify({'error': f'ffmpeg failed: {result.stderr[-800:]}'})

        generated.append({'filename': out_name, 'product_title': ' · '.join(titles)})

        # ── Return all generated files ─────────────────────────────────────────
        return jsonify({'success': True, 'files': generated})

    except Exception as e:
        import traceback
        app.logger.error(f"Video generation error: {str(e)}\n" + traceback.format_exc())
        return jsonify({'error': f'Video generation failed: {str(e)}'}), 500

    finally:
        for _f in (tmp_files if 'tmp_files' in dir() else []):
            try:
                os.unlink(_f)
            except Exception:
                pass

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
            sale_state = {
                'active':           True,
                'category':         request.form.get('category',''),
                'discount_percent': int(request.form.get('discount_percent', 10))
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

# ── Ad Vault (Video Storage) ──────────────────────────────────────────────────
@app.route('/ad-vault')
@login_required
def ad_vault():
    """Display all generated video ads."""
    videos = []
    if os.path.exists(ADS_FOLDER):
        for filename in os.listdir(ADS_FOLDER):
            if filename.lower().endswith('.mp4'):
                filepath = os.path.join(ADS_FOLDER, filename)
                try:
                    size_bytes = os.path.getsize(filepath)
                    size_mb = round(size_bytes / (1024 * 1024), 2)
                    mod_time = os.path.getmtime(filepath)
                    mod_date = datetime.datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M:%S')
                    
                    # Parse filename to extract SKU and template
                    # Format: video_ad_{SKU}_{TEMPLATE}_{FORMAT}_{TIMESTAMP}.mp4
                    parts = filename.replace('video_ad_', '').replace('.mp4', '').split('_')
                    sku = parts[0] if parts else 'Unknown'
                    
                    videos.append({
                        'filename': filename,
                        'sku': sku,
                        'size_mb': size_mb,
                        'mod_date': mod_date,
                        'display_name': filename.replace('.mp4', '').replace('video_ad_', '')
                    })
                except Exception:
                    pass
    
    # Sort by modification time (newest first)
    videos.sort(key=lambda v: v['mod_date'], reverse=True)
    
    return render_template('ad_vault.html', videos=videos, **ctx())

@app.route('/confirm-delete-video/<filename>')
@login_required
def confirm_delete_video(filename):
    if session.get('is_guest'):
        flash('Guests cannot delete videos.', 'error')
        return redirect(url_for('dashboard'))
    return render_template('confirm_delete.html', filename=filename,
        delete_type='video', back_url=url_for('ad_vault'), **ctx())

@app.route('/ad-vault/delete/<filename>', methods=['POST'])
@login_required
@admin_required
def delete_video(filename):
    """Delete a video from the ad vault."""
    if not filename.endswith('.mp4'):
        return jsonify({'error': 'Invalid file type'}), 400
    
    filepath = os.path.join(ADS_FOLDER, filename)
    if os.path.exists(filepath) and os.path.isfile(filepath):
        try:
            os.remove(filepath)
            return jsonify({'success': True, 'message': f'Video deleted: {filename}'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'File not found'}), 404

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
    return render_template('admin_settings.html', masked_key=masked, has_key=bool(key), **ctx())

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
    """Redirect to checkout for the chosen pricing tier."""
    plans = {
        'starter': {'name': 'Starter', 'price': 299, 'email': 'leprograms@protonmail.com'},
        'pro': {'name': 'Pro', 'price': 499, 'email': 'leprograms@protonmail.com'},
        'enterprise': {'name': 'Enterprise', 'price': 799, 'email': 'leprograms@protonmail.com'},
    }
    p = plans.get(plan, plans['pro'])
    
    # If Stripe is configured, do a proper checkout session
    if stripe_enabled and STRIPE_SECRET_KEY:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        try:
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': f"RetailTrack {p['name']}",
                            'description': f"One-time purchase of RetailTrack {p['name']} plan",
                        },
                        'unit_amount': p['price'] * 100,
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=url_for('payment_success', _external=True),
                cancel_url=url_for('wizard', _external=True),
                customer_email=plan,  # placeholder
                metadata={'plan': plan},
            )
            return redirect(checkout_session.url, code=303)
        except Exception as e:
            flash(f"Payment error: {e}", "error")
            return redirect(url_for('wizard'), 302)
    
    # Fallback: show a mailto link for manual payment
    subject = f"RetailTrack {p['name']} - ${p['price']}"
    body = f"Hi Jay,\n\nI'm interested in the RetailTrack {p['name']} plan (${p['price']}).\n\nPlease send me more info and setup details.\n\nThanks!"
    return redirect(f"mailto:leprograms@protonmail.com?subject={subject}&body={body}")

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

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True)
