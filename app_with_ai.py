import os
import csv
import json
import uuid
import shutil
import base64
import hashlib
import datetime
import io
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, send_file, send_from_directory)
from werkzeug.utils import secure_filename

# Load .env file
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

app = Flask(__name__, template_folder='templetes')
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

# Use persistent storage on Railway (/data is the mounted persistent disk)
# Falls back to local directory if not running on Railway
DATA_DIR = os.environ.get('RAILWAY_DATA_DIR', '/data' if os.path.exists('/data') else BASE_DIR)

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

# Migrate any existing data files from BASE_DIR to DATA_DIR on first boot
for _fname in ['inventory.csv', 'users.json', 'pending_users.json', 'sale_state.json']:
    _src = os.path.join(BASE_DIR, _fname)
    _dst = os.path.join(DATA_DIR, _fname)
    if os.path.exists(_src) and not os.path.exists(_dst):
        shutil.copy2(_src, _dst)

# ── Config ────────────────────────────────────────────────────────────────────
STORE_NAME    = 'Liberty Emporium & Thrift'
DEMO_MODE     = os.environ.get('DEMO_MODE', 'false').lower() == 'true'
CONTACT_EMAIL = os.environ.get('CONTACT_EMAIL', 'alexanderjay70@gmail.com')
ALLOWED_EXT   = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
MAX_BACKUPS   = 20

CATEGORIES = ['Furniture','Electronics','Clothing','Jewelry','Home Decor',
              'Books','Kitchen','Toys','Tools','Collectibles','Art','Miscellaneous']
CONDITIONS = ['New','Like New','Good','Fair','Poor']
STATUSES   = ['Available','Sold','Reserved','Pending']

ADMIN_USER  = 'admin'
ADMIN_PASS  = os.environ.get('ADMIN_PASSWORD', 'admin123')
ADMIN_EMAIL = 'alexanderjay70@gmail.com'

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
    )

# ── Context processor ─────────────────────────────────────────────────────────
@app.context_processor
def inject_globals():
    return dict(
        store_name=STORE_NAME,
        demo_mode=DEMO_MODE,
        demo_contact_email=CONTACT_EMAIL,
        stats=get_stats(),
        sale_state=load_sale(),
    )

# ── Auth Routes ───────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET','POST'])
def login():
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

# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route('/')
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
    api_key = request.form.get('api_key', '').strip() or os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'No API key provided. Enter your Claude API key in the AI box above.'})
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

@app.route('/generate-video-ad', methods=['POST'])
@login_required
def generate_video_ad():
    import subprocess, tempfile, textwrap

    # ── Parse inputs ──────────────────────────────────────────────────────────
    try:
        products  = json.loads(request.form.get('products', '[]'))
        style     = request.form.get('style', 'slideshow')          # slideshow | kenburns
        duration  = max(10, min(60, int(request.form.get('duration', 30))))
        template  = request.form.get('template', 'default')         # seasonal template
        format_str = request.form.get('format', '1920x1080')         # video format WxH
        cta_text  = request.form.get('cta_text', '').strip()
        tagline   = request.form.get('tagline', '').strip()
        logo_position = request.form.get('logo_position', 'top-right')
        logo_size = request.form.get('logo_size', 'medium')
    except Exception as e:
        return jsonify({'error': f'Bad request: {e}'})

    # ── Template color schemes ─────────────────────────────────────
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
    
    # ── Format dimensions ──────────────────────────────────────────
    formats = {
        '1920x1080': (1920, 1080),  # YouTube / Universal 16:9
        '1080x1350': (1080, 1350),  # Instagram 4:5
        '1080x1920': (1080, 1920),  # TikTok / Vertical 9:16
        '1200x628':  (1200, 628),   # Facebook 16:9 Link
        '1080x1080': (1080, 1080),  # Square 1:1
    }
    
    template_config = templates.get(template, templates['default'])
    video_size = formats.get(format_str, formats['1920x1080'])

    # Determine music path
    music_file_upload = request.files.get('music_file')
    music_track_name  = request.form.get('music_track', '').strip()

    if not products:
        return jsonify({'error': 'No products provided.'})
    if not music_file_upload and not music_track_name:
        return jsonify({'error': 'No music track selected.'})

    # Check ffmpeg
    ffmpeg_path = '/usr/bin/ffmpeg'
    if not os.path.exists(ffmpeg_path):
        return jsonify({'error': 'Video generation not available on this server (ffmpeg missing).'})

    generated = []
    tmp_files = []   # track temp files to clean up
    
    try:
        # Save uploaded music to a temp file if provided
        if music_file_upload:
            tmp_music = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
            music_file_upload.save(tmp_music.name)
            tmp_files.append(tmp_music.name)
            if os.path.getsize(tmp_music.name) > 20 * 1024 * 1024:
                return jsonify({'error': 'Uploaded MP3 must be under 20 MB.'})
            music_path = tmp_music.name
        else:
            music_path = os.path.join(MUSIC_FOLDER, os.path.basename(music_track_name))
            if not os.path.exists(music_path):
                return jsonify({'error': f'Music track not found: {music_track_name}'})

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

        for p in products:
            sku         = p.get('sku', 'UNKNOWN')
            title       = p.get('title', 'Untitled')
            price       = p.get('price', '0.00')
            description = p.get('description', '')
            image_url   = p.get('image', '')

            # ── Build frame image with Pillow ─────────────────────────────────
            from PIL import Image as _Img, ImageDraw as _Draw, ImageFont as _Font
            import io as _io

            frame = _Img.new('RGB', (W, H), color=tuple(int(template_config['bg_dark'].lstrip('#')[i:i+2], 16) for i in (0, 2, 4)))

            # Calculate layout based on aspect ratio
            is_vertical = H > W
            if is_vertical:
                # Vertical layout (TikTok, Instagram Reels)
                img_height = int(H * 0.6)
                img_width = int(W)
                text_y_start = img_height + int(H * 0.08)
                overlay_height = H - img_height
            else:
                # Horizontal layout (YouTube, Facebook, Square)
                img_height = H
                img_width = int(W * 0.65)
                text_y_start = int(H * 0.2)
                overlay_width = W - img_width

            # Paste product image if available
            if image_url:
                img_filename = image_url.split('/')[-1]
                img_path = os.path.join(UPLOAD_FOLDER, img_filename)
                if os.path.exists(img_path):
                    try:
                        prod_img = _Img.open(img_path)
                        prod_img = fix_image_orientation(prod_img)
                        prod_img = prod_img.convert('RGB')
                        prod_img.thumbnail((img_width, img_height), _Img.LANCZOS)
                        if is_vertical:
                            frame.paste(prod_img, (0, 0))
                        else:
                            frame.paste(prod_img, (0, (H - prod_img.height) // 2))
                    except Exception:
                        pass

            # Add dark overlay for text (for horizontal: right side, for vertical: bottom)
            if is_vertical:
                overlay = _Img.new('RGBA', (W, overlay_height), (0, 0, 0, 0))
                _Draw.Draw(overlay).rectangle([(0, 0), (W, overlay_height)], fill=(int(template_config['bg_dark'].lstrip('#')[0:2], 16), int(template_config['bg_dark'].lstrip('#')[2:4], 16), int(template_config['bg_dark'].lstrip('#')[4:6], 16), 230))
                frame.paste(_Img.new('RGB', (W, overlay_height), color=tuple(int(template_config['bg_dark'].lstrip('#')[i:i+2], 16) for i in (0, 2, 4))), (0, img_height))
            else:
                overlay = _Img.new('RGBA', (W, H), (0, 0, 0, 0))
                _Draw.Draw(overlay).rectangle([(img_width, 0), (W, H)], fill=(int(template_config['bg_dark'].lstrip('#')[0:2], 16), int(template_config['bg_dark'].lstrip('#')[2:4], 16), int(template_config['bg_dark'].lstrip('#')[4:6], 16), 230))
                frame = _Img.alpha_composite(frame.convert('RGBA'), overlay).convert('RGB')

            draw = _Draw.Draw(frame)

            # Font sizing based on video size
            font_title_size = max(28, int(W * 0.022))
            font_price_size = max(42, int(W * 0.033))
            font_small_size = max(16, int(W * 0.014))
            font_cta_size = max(14, int(W * 0.010))

            try:
                font_title = _Font.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', font_title_size)
                font_price = _Font.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', font_price_size)
                font_small = _Font.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', font_small_size)
                font_cta = _Font.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', font_cta_size)
            except Exception:
                font_title = _Font.load_default()
                font_price = font_title
                font_small = font_title
                font_cta = font_title

            # Parse template accent color
            accent_color = tuple(int(template_config['accent'].lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
            text_color = (255, 255, 255)

            if is_vertical:
                # Vertical text layout
                text_x = int(W * 0.05)
                y = text_y_start
            else:
                # Horizontal text layout on the right side
                text_x = img_width + int((W - img_width) * 0.05)
                y = text_y_start

            # Wrap title
            lines = textwrap.wrap(title, width=15 if is_vertical else 20)
            for line in lines[:2]:
                draw.text((text_x, y), line, font=font_title, fill=accent_color)
                y += font_title_size + 8

            # Price
            y += int(font_title_size * 0.3)
            draw.text((text_x, y), f'${price}', font=font_price, fill=text_color)
            y += font_price_size + 12

            # ── Product Description ───────────────────────────────────────────
            if description:
                desc_lines = textwrap.wrap(description, width=18 if is_vertical else 24)
                for desc_line in desc_lines[:3]:  # Limit to 3 lines
                    draw.text((text_x, y), desc_line, font=font_small, fill=(200, 200, 220))
                    y += font_small_size + 5
                y += int(font_small_size * 0.5)  # Extra spacing after description

            # ── Custom CTA text ───────────────────────────────────────────────
            if cta_text:
                cta_lines = textwrap.wrap(cta_text, width=15 if is_vertical else 20)
                for cta_line in cta_lines[:2]:
                    draw.text((text_x, y), cta_line, font=font_cta, fill=accent_color)
                    y += font_cta_size + 6

            # Store name and tagline footer
            y = H - (font_small_size * 2 + 12)
            draw.text((text_x, y), 'Liberty Emporium', font=font_small, fill=(160, 160, 200))
            footer_text = tagline or '125 W Swannanoa Ave'
            draw.text((text_x, y + font_small_size + 4), footer_text, font=font_small, fill=(120, 120, 160))

            # ── Add watermark/logo if provided ───────────────────────────────
            if logo_path and os.path.exists(logo_path):
                try:
                    logo_img = _Img.open(logo_path)
                    logo_img = fix_image_orientation(logo_img)
                    logo_img = logo_img.convert('RGBA')
                    
                    # Determine logo size based on size setting
                    size_map = {'small': 60, 'medium': 90, 'large': 120}
                    target_size = size_map.get(logo_size, 90)
                    
                    # Scale relative to video size
                    target_size = int(target_size * (W / 1280.0))
                    
                    # Maintain aspect ratio
                    logo_img.thumbnail((target_size, target_size), _Img.LANCZOS)
                    
                    # Calculate position based on logo_position setting
                    padding = int(W * 0.015)
                    pos_map = {
                        'top-left': (padding, padding),
                        'top-right': (W - logo_img.width - padding, padding),
                        'bottom-left': (padding, H - logo_img.height - padding),
                        'bottom-right': (W - logo_img.width - padding, H - logo_img.height - padding)
                    }
                    pos = pos_map.get(logo_position, pos_map['top-right'])
                    
                    # Paste logo onto frame
                    frame = frame.convert('RGBA')
                    frame.paste(logo_img, pos, logo_img)
                    frame = frame.convert('RGB')
                except Exception as logo_err:
                    pass

            # Save frame to temp file
            tmp_frame = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
            frame.save(tmp_frame.name, 'JPEG', quality=92)
            tmp_files.append(tmp_frame.name)

            # ── Run ffmpeg ────────────────────────────────────────────────────
            ts       = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            template_suffix = f"_{template}" if template != 'default' else ''
            format_suffix = f"_{format_str.replace('x', 'x')}" if format_str != '1920x1080' else ''
            out_name = f'video_ad_{sku}{template_suffix}{format_suffix}_{ts}.mp4'
            out_path = os.path.join(ADS_FOLDER, out_name)

            if style == 'kenburns':
                vf = f"zoompan=z='min(zoom+0.0015,1.5)':d={duration * 25}:s={W}x{H},fps=25"
                cmd = [
                    ffmpeg_path, '-y',
                    '-loop', '1', '-i', tmp_frame.name,
                    '-i', music_path,
                    '-vf', vf,
                    '-c:v', 'libx264', '-c:a', 'aac',
                    '-t', str(duration),
                    '-pix_fmt', 'yuv420p',
                    '-shortest',
                    out_path
                ]
            else:  # slideshow (default)
                cmd = [
                    ffmpeg_path, '-y',
                    '-loop', '1', '-i', tmp_frame.name,
                    '-i', music_path,
                    '-tune', 'stillimage', '-c:v', 'libx264',
                    '-c:a', 'aac',
                    '-t', str(duration),
                    '-pix_fmt', 'yuv420p',
                    '-shortest',
                    out_path
                ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return jsonify({'error': f'ffmpeg failed: {result.stderr[-500:]}'})

            generated.append({'filename': out_name, 'product_title': title})

        # Return success
        return jsonify({'success': True, 'files': generated})

    except Exception as e:
        # Catch any unexpected errors and return JSON instead of error page
        import traceback
        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        app.logger.error(f"Video generation error: {error_msg}")
        return jsonify({'error': f'Video generation failed: {str(e)}'}), 500

    finally:
        # Clean up temp files
        try:
            for f in tmp_files if 'tmp_files' in locals() else []:
                try:
                    os.unlink(f)
                except Exception:
                    pass
        except Exception:
            pass

# ── Listing Generator ─────────────────────────────────────────────────────────
@app.route('/listing-generator')
@login_required
def listing_generator():
    products = load_inventory()
    return render_template('listing_generator.html', products=products, **ctx())

@app.route('/generate-listing', methods=['POST'])
@login_required
def generate_listing():
    data      = request.get_json()
    product   = data.get('product', {})
    platform  = data.get('platform', 'facebook')
    api_key   = os.environ.get('ANTHROPIC_API_KEY')

    title     = product.get('title', '')
    price     = product.get('price', '0')
    category  = product.get('category', '')
    condition = product.get('condition', '')
    desc      = product.get('description', '')
    sku       = product.get('sku', '')

    store_info = 'Liberty Emporium & Thrift, 125 W Swannanoa Ave, Liberty NC 27298'

    platform_prompts = {
        'facebook': (
            f'Write a Facebook Marketplace listing for this thrift store item.\n'
            f'Item: {title}\nPrice: ${price}\nCondition: {condition}\nCategory: {category}\nDescription: {desc}\n'
            f'Store: {store_info}\n\n'
            f'Return JSON only with keys: title, price, condition, description, location.\n'
            f'Make the description engaging and friendly, 3-5 sentences.'
        ),
        'craigslist': (
            f'Write a Craigslist listing for this thrift store item.\n'
            f'Item: {title}\nPrice: ${price}\nCondition: {condition}\nCategory: {category}\nDescription: {desc}\n'
            f'Store: {store_info}\n\n'
            f'Return JSON only with keys: title, price, condition, description, location.\n'
            f'Keep it straightforward and factual.'
        ),
        'instagram': (
            f'Write an Instagram caption for this thrift store item.\n'
            f'Item: {title}\nPrice: ${price}\nCondition: {condition}\nDescription: {desc}\n'
            f'Store: {store_info}\n\n'
            f'Return JSON only with keys: title, price, condition, description, location.\n'
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
        'anthropic_key_set': bool(os.environ.get('ANTHROPIC_API_KEY')),
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

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True)
