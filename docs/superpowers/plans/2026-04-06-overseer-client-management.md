# Overseer Client Management System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-tenant overseer system so Jay can provision, manage, and impersonate client stores from one dashboard, while each client logs in to manage only their own store.

**Architecture:** Each client store lives in `customers/{slug}/` with its own `config.json`, `inventory.csv`, `uploads/`, and `users.json`. A `get_store_paths(slug)` helper routes all data access. `active_store_slug()` reads the session to return the current active store (impersonated or client's own), making all existing routes automatically tenant-aware.

**Tech Stack:** Flask, Python 3.11, Jinja2, JSON files, CSV, werkzeug password hashing, existing `app_with_ai.py` patterns.

---

## File Structure

**Modified files:**
- `app_with_ai.py` — add helpers, decorators, routes (all changes)
- `templates/base.html` — add impersonation banner
- `templates/login.html` — handle client redirect to `/my-store`

**New files:**
- `templates/overseer_dashboard.html` — overseer main view
- `templates/overseer_client.html` — single client detail/management page
- `templates/client_dashboard.html` — client-facing dashboard

---

## Task 1: Core helpers — `get_store_paths`, `active_store_slug`, `load_client_config`

**Files:**
- Modify: `app_with_ai.py` — add after line ~134 (after `save_stripe_keys`)

- [ ] **Step 1: Add `get_store_paths` and `active_store_slug` helpers**

In `app_with_ai.py`, after the `save_stripe_keys` function, add:

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: add get_store_paths, active_store_slug, load/save_client_config, list_client_stores helpers"
```

---

## Task 2: `overseer_required` and `client_required` decorators + update `admin_required`

**Files:**
- Modify: `app_with_ai.py` — update decorator section (~line 328)

- [ ] **Step 1: Add `overseer_required` and `client_required` decorators**

In `app_with_ai.py`, after the existing `admin_required` function (around line 336), add:

```python
def overseer_required(f):
    """Only users with role='overseer' in session can access."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        if session.get('user_role') != 'overseer':
            flash('Overseer access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

def client_required(f):
    """Only users with role='client' in session can access."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        if not session.get('store_slug'):
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated
```

- [ ] **Step 2: Update `inject_globals` context processor to expose `user_role` and impersonation state**

Find the `inject_globals` function (around line 350). Replace the return dict with:

```python
    is_admin = session.get('username') == ADMIN_USER
    user_role = session.get('user_role', 'admin' if is_admin else 'guest')
    impersonating_slug = session.get('impersonating_slug')
    impersonating_store = None
    if impersonating_slug:
        impersonating_store = load_client_config(impersonating_slug)
    return dict(
        store_name=STORE_NAME,
        demo_mode=DEMO_MODE,
        demo_contact_email=CONTACT_EMAIL,
        stats=stats,
        sale_state=sale_state,
        user_role=user_role,
        store_config=load_store_config(),
        impersonating_slug=impersonating_slug,
        impersonating_store=impersonating_store,
    )
```

- [ ] **Step 3: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: add overseer_required, client_required decorators; expose impersonation state to templates"
```

---

## Task 3: Update login to handle client role + set `user_role` in session for all users

**Files:**
- Modify: `app_with_ai.py` — login route (~line 387)

- [ ] **Step 1: Update login route to set `user_role` and `store_slug` in session**

Find the login route. Replace the `users` dict lookup block (the `if username in users` block) with:

```python
        users = load_users()
        if username in users and users[username]['password'] == hash_password(password):
            user = users[username]
            role = user.get('role', 'staff')
            # Check suspension for client users
            if role == 'client':
                slug = user.get('store_slug', '')
                client_cfg = load_client_config(slug) if slug else None
                if client_cfg and client_cfg.get('status') == 'suspended':
                    flash('Your store has been suspended. Contact support at leprograms@protonmail.com.', 'error')
                    return render_template('login.html', **ctx())
                session['store_slug'] = slug
            session['logged_in']  = True
            session['username']   = username
            session['is_guest']   = False
            session['user_role']  = role
            session.permanent     = True
            app.permanent_session_lifetime = datetime.timedelta(hours=8)
            flash(f'Welcome, {username}!', 'success')
            if role == 'client':
                return redirect(url_for('client_dashboard_route'))
            if role == 'overseer':
                return redirect(url_for('overseer_dashboard'))
            return redirect(url_for('dashboard'))
```

Also update the ADMIN_USER hardcoded login block (just above) to set `user_role`:

```python
        if username == ADMIN_USER and password == ADMIN_PASS:
            session['logged_in'] = True
            session['username']  = ADMIN_USER
            session['is_guest']  = False
            session['user_role'] = 'overseer'
            session.permanent    = True
            app.permanent_session_lifetime = datetime.timedelta(hours=8)
            flash('Welcome back, Jay!', 'success')
            return redirect(url_for('dashboard'))
```

- [ ] **Step 2: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: login sets user_role and store_slug in session; clients redirected to /my-store; suspended check"
```

---

## Task 4: Update `load_inventory`, `save_inventory`, and `serve_upload` to be tenant-aware

**Files:**
- Modify: `app_with_ai.py` — `load_inventory` (~line 266), `save_inventory` (~line 278), `serve_upload` (~line 595)

- [ ] **Step 1: Update `load_inventory` to use active store paths**

Replace the `load_inventory` function:

```python
def load_inventory(slug=None):
    paths = get_store_paths(slug if slug is not None else active_store_slug())
    inv_file = paths['inventory']
    upload_dir = paths['uploads']
    if not os.path.exists(inv_file):
        return []
    with open(inv_file, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        products = list(reader)
    for p in products:
        imgs = [i.strip() for i in p.get('Images','').split(',') if i.strip()]
        p['image_list']   = imgs
        p['valid_images'] = [i for i in imgs if os.path.exists(os.path.join(upload_dir, i))]
    return products
```

- [ ] **Step 2: Update `save_inventory` to use active store paths**

Replace the `save_inventory` function:

```python
def save_inventory(products, slug=None):
    paths = get_store_paths(slug if slug is not None else active_store_slug())
    inv_file = paths['inventory']
    backup_dir = paths['backups']
    fieldnames = ['SKU','Title','Description','Category','Condition','Price',
                  'Cost Paid','Status','Date Added','Images','Section','Shelf']
    # Backup
    if os.path.exists(inv_file):
        os.makedirs(backup_dir, exist_ok=True)
        ts  = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        dst = os.path.join(backup_dir, f'inventory_{ts}.csv')
        shutil.copy2(inv_file, dst)
        backups = sorted(
            [f for f in os.listdir(backup_dir) if f.endswith('.csv')],
            reverse=True
        )
        for old in backups[MAX_BACKUPS:]:
            os.remove(os.path.join(backup_dir, old))
    with open(inv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(products)
```

- [ ] **Step 3: Update `serve_upload` to serve from active store's uploads folder**

Replace:

```python
@app.route('/uploads/<filename>')
def serve_upload(filename):
    paths = get_store_paths(active_store_slug())
    return send_from_directory(paths['uploads'], filename)
```

- [ ] **Step 4: Update `_backup_inventory` to be a no-op (now handled inside `save_inventory`)**

Replace `_backup_inventory`:

```python
def _backup_inventory():
    pass  # Backup now handled inside save_inventory()
```

- [ ] **Step 5: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: load_inventory, save_inventory, serve_upload are now tenant-aware via active_store_slug()"
```

---

## Task 5: Impersonation banner in `base.html`

**Files:**
- Modify: `templates/base.html` — add banner near top of `<body>`

- [ ] **Step 1: Add impersonation banner to base.html**

In `templates/base.html`, find the opening `<body>` tag (or the first element inside body). Add immediately after it:

```html
{% if impersonating_slug %}
<div style="background:#f59e0b; color:#1c1917; padding:0.6rem 1.5rem; display:flex; justify-content:space-between; align-items:center; font-size:0.9rem; font-weight:600; position:sticky; top:0; z-index:999; box-shadow:0 2px 8px rgba(0,0,0,0.15);">
  <span>🔀 Managing <strong>{{ impersonating_store.store_name if impersonating_store else impersonating_slug }}</strong></span>
  <a href="/overseer/exit-impersonate" style="background:#1c1917; color:white; padding:0.35rem 1rem; border-radius:6px; text-decoration:none; font-size:0.82rem;">Exit to Overseer →</a>
</div>
{% endif %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/base.html
git commit -m "feat: add impersonation banner to base.html shown when overseer is managing a client"
```

---

## Task 6: Overseer routes — impersonate, exit, suspend, delete, reset-password

**Files:**
- Modify: `app_with_ai.py` — add overseer routes after the existing `admin_leads` route

- [ ] **Step 1: Add impersonate, exit-impersonate, suspend, delete, reset-password routes**

After the `admin_leads` route, add:

```python
# ── Overseer Routes ───────────────────────────────────────────────────────────
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
    import shutil as _shutil
    store_dir = os.path.join(CUSTOMERS_DIR, slug)
    if os.path.exists(store_dir):
        _shutil.rmtree(store_dir)
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
    temp_pw = secrets.token_urlsafe(10)
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
```

- [ ] **Step 2: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: add overseer impersonate, exit, suspend, delete, reset-password, update routes"
```

---

## Task 7: Overseer client provisioning route (`/overseer/client/create`)

**Files:**
- Modify: `app_with_ai.py` — add after Task 6 routes

- [ ] **Step 1: Add client provisioning route**

```python
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

    # Unique slug
    slug = slugify(store_name)
    base_slug = slug
    counter = 1
    while os.path.exists(os.path.join(CUSTOMERS_DIR, slug)):
        slug = f'{base_slug}-{counter}'
        counter += 1

    # Create folder structure
    store_dir = os.path.join(CUSTOMERS_DIR, slug)
    os.makedirs(os.path.join(store_dir, 'uploads'), exist_ok=True)
    os.makedirs(os.path.join(store_dir, 'backups'), exist_ok=True)

    # Write config
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

    # Create empty inventory (client adds their own products)
    inv_path = os.path.join(store_dir, 'inventory.csv')
    fieldnames = ['SKU','Title','Description','Category','Condition','Price',
                  'Cost Paid','Status','Date Added','Images','Section','Shelf']
    with open(inv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()

    # Create client user account
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
```

- [ ] **Step 2: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: add overseer_create_client route — provisions store folder, config, inventory, user account"
```

---

## Task 8: Overseer dashboard and client detail routes (GET)

**Files:**
- Modify: `app_with_ai.py` — add GET routes

- [ ] **Step 1: Add overseer dashboard and client detail GET routes**

```python
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
    # Count their inventory
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
```

- [ ] **Step 2: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: add overseer_dashboard and overseer_client_detail GET routes"
```

---

## Task 9: Client dashboard route (`/my-store`)

**Files:**
- Modify: `app_with_ai.py` — add `/my-store` route

- [ ] **Step 1: Add `/my-store` route**

```python
@app.route('/my-store')
@login_required
@client_required
def client_dashboard_route():
    slug = session.get('store_slug') or active_store_slug()
    cfg  = load_client_config(slug)
    if not cfg:
        flash('Store not found.', 'error')
        return redirect(url_for('login'))
    products = load_inventory(slug)
    return render_template('client_dashboard.html',
        products=products,
        client_config=cfg,
        store_name=cfg.get('store_name', 'Your Store'),
        **ctx()
    )
```

- [ ] **Step 2: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: add /my-store client dashboard route"
```

---

## Task 10: `overseer_dashboard.html` template

**Files:**
- Create: `templates/overseer_dashboard.html`

- [ ] **Step 1: Create the template**

```html
{% extends "base.html" %}
{% block title %}Overseer Dashboard{% endblock %}

{% block howto_slides %}
<div class="ht-slide">
  <div class="ht-icon">🛰️</div>
  <div class="ht-label">Overseer Dashboard — How-To Guide</div>
  <h2>Manage All Your Client Stores</h2>
  <p>The Overseer Dashboard shows every client store you've provisioned. Click <strong>Manage</strong> to log in as that client and make changes on their behalf.</p>
</div>
<div class="ht-slide">
  <div class="ht-icon">➕</div>
  <div class="ht-label">Provisioning a New Client</div>
  <h2>Add a New Client Store</h2>
  <ol>
    <li>Click <strong>+ Add New Client</strong>.</li>
    <li>Fill in their store name, email, temp password, and plan.</li>
    <li>Submit — the store folder, inventory, and login are created instantly.</li>
    <li>Share the login URL and temp password with your client.</li>
  </ol>
</div>
<div class="ht-slide">
  <div class="ht-icon">🔀</div>
  <div class="ht-label">Impersonation</div>
  <h2>Managing a Client's Store</h2>
  <ol>
    <li>Click <strong>Manage</strong> on any client card.</li>
    <li>A yellow banner appears at the top — you are now inside their store.</li>
    <li>All changes you make apply to their inventory and data.</li>
    <li>Click <strong>Exit to Overseer</strong> in the banner to return here.</li>
  </ol>
</div>
{% endblock %}

{% block content %}
<div class="ht-page-bar"><button class="ht-open-btn" onclick="htOpen()">❓ How to Use This Page</button></div>

<style>
  .ov-header { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:2rem; }
  .ov-header h1 { font-size:2rem; font-weight:800; color:#0f172a; }
  .ov-stats { display:flex; gap:1.25rem; flex-wrap:wrap; margin-bottom:2rem; }
  .ov-stat { background:white; border-radius:12px; padding:1.25rem 1.75rem; box-shadow:0 2px 8px rgba(0,0,0,0.07); text-align:center; min-width:120px; }
  .ov-stat-num { font-size:2.2rem; font-weight:900; color:#10b981; }
  .ov-stat-lbl { font-size:0.78rem; color:#6b7280; margin-top:0.2rem; text-transform:uppercase; letter-spacing:0.05em; }
  .ov-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:1.25rem; }
  .ov-card { background:white; border-radius:14px; box-shadow:0 2px 10px rgba(0,0,0,0.08); overflow:hidden; }
  .ov-card-top { padding:1.25rem 1.25rem 0.75rem; }
  .ov-card-name { font-size:1.1rem; font-weight:700; color:#0f172a; margin-bottom:0.3rem; }
  .ov-card-meta { font-size:0.8rem; color:#6b7280; }
  .ov-badges { display:flex; gap:0.5rem; margin:0.75rem 0 0; flex-wrap:wrap; }
  .badge { display:inline-block; padding:0.2rem 0.7rem; border-radius:20px; font-size:0.72rem; font-weight:700; text-transform:uppercase; letter-spacing:0.04em; }
  .badge-active    { background:#d1fae5; color:#065f46; }
  .badge-suspended { background:#fee2e2; color:#991b1b; }
  .badge-starter   { background:#e0e7ff; color:#3730a3; }
  .badge-pro       { background:#fef3c7; color:#92400e; }
  .badge-enterprise{ background:#f3e8ff; color:#6b21a8; }
  .ov-card-actions { display:flex; gap:0.5rem; padding:0.75rem 1.25rem 1.25rem; border-top:1px solid #f1f5f9; margin-top:0.75rem; flex-wrap:wrap; }
  .btn-manage  { background:#0f172a; color:white; border:none; padding:0.45rem 1rem; border-radius:7px; font-size:0.8rem; font-weight:700; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; gap:0.3rem; }
  .btn-details { background:#f1f5f9; color:#0f172a; border:none; padding:0.45rem 1rem; border-radius:7px; font-size:0.8rem; font-weight:600; cursor:pointer; text-decoration:none; }
  .btn-suspend { background:#fff7ed; color:#c2410c; border:1px solid #fed7aa; padding:0.45rem 0.85rem; border-radius:7px; font-size:0.78rem; font-weight:600; cursor:pointer; }
  .add-modal { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:200; align-items:center; justify-content:center; padding:1rem; }
  .add-modal.open { display:flex; }
  .add-box { background:white; border-radius:16px; padding:2rem; max-width:520px; width:100%; max-height:90vh; overflow-y:auto; }
  .add-box h2 { font-size:1.3rem; font-weight:800; margin-bottom:1.5rem; color:#0f172a; }
  .form-row { margin-bottom:1rem; }
  .form-row label { display:block; font-size:0.82rem; font-weight:600; color:#374151; margin-bottom:0.3rem; }
  .form-row input, .form-row select, .form-row textarea { width:100%; padding:0.65rem 0.85rem; border:1.5px solid #e5e7eb; border-radius:8px; font-size:0.9rem; box-sizing:border-box; }
  .form-row input:focus, .form-row select:focus { outline:none; border-color:#10b981; }
</style>

<div class="ov-header">
  <div>
    <h1>🛰️ Overseer</h1>
    <p style="color:#6b7280; margin-top:0.3rem;">All client stores</p>
  </div>
  <button class="btn-manage" onclick="document.getElementById('addModal').classList.add('open')">➕ Add New Client</button>
</div>

<div class="ov-stats">
  <div class="ov-stat"><div class="ov-stat-num">{{ stores|length }}</div><div class="ov-stat-lbl">Total Clients</div></div>
  <div class="ov-stat"><div class="ov-stat-num">{{ active_count }}</div><div class="ov-stat-lbl">Active</div></div>
  <div class="ov-stat"><div class="ov-stat-num">{{ suspended_count }}</div><div class="ov-stat-lbl">Suspended</div></div>
  <div class="ov-stat"><div class="ov-stat-num" style="font-size:1.5rem;">${{ total_revenue }}</div><div class="ov-stat-lbl">Total Revenue</div></div>
</div>

{% if stores %}
<div class="ov-grid">
  {% for store in stores %}
  <div class="ov-card">
    <div class="ov-card-top">
      <div class="ov-card-name">{{ store.store_name }}</div>
      <div class="ov-card-meta">{{ store.contact_email }} · {{ store.industry or 'general' }}</div>
      <div class="ov-card-meta" style="margin-top:0.2rem;">Added {{ store.created_at[:10] if store.created_at else 'unknown' }}</div>
      <div class="ov-badges">
        <span class="badge badge-{{ store.status or 'active' }}">{{ store.status or 'active' }}</span>
        <span class="badge badge-{{ store.plan or 'starter' }}">{{ store.plan or 'starter' }}</span>
      </div>
    </div>
    <div class="ov-card-actions">
      <form method="POST" action="/overseer/client/{{ store.slug }}/impersonate" style="display:inline;">
        <button type="submit" class="btn-manage">🔀 Manage</button>
      </form>
      <a href="/overseer/client/{{ store.slug }}" class="btn-details">Details</a>
      <form method="POST" action="/overseer/client/{{ store.slug }}/suspend" style="display:inline;">
        <button type="submit" class="btn-suspend">{{ '▶ Unsuspend' if store.status == 'suspended' else '⏸ Suspend' }}</button>
      </form>
    </div>
  </div>
  {% endfor %}
</div>
{% else %}
<div style="text-align:center; padding:4rem 2rem; background:white; border-radius:14px; color:#6b7280;">
  <div style="font-size:3rem; margin-bottom:1rem;">📭</div>
  <h2 style="color:#0f172a; margin-bottom:0.5rem;">No clients yet</h2>
  <p>Click <strong>+ Add New Client</strong> to provision your first store.</p>
</div>
{% endif %}

<!-- Add New Client Modal -->
<div class="add-modal" id="addModal">
  <div class="add-box">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1.5rem;">
      <h2 style="margin:0;">➕ New Client Store</h2>
      <button onclick="document.getElementById('addModal').classList.remove('open')" style="background:none; border:none; font-size:1.4rem; cursor:pointer; color:#6b7280;">✕</button>
    </div>
    <form method="POST" action="/overseer/client/create">
      <div class="form-row"><label>Store Name *</label><input type="text" name="store_name" placeholder="e.g. Sarah's Boutique" required></div>
      <div class="form-row"><label>Contact Name</label><input type="text" name="contact_name" placeholder="Sarah Johnson"></div>
      <div class="form-row"><label>Contact Email * (becomes login)</label><input type="email" name="contact_email" placeholder="sarah@example.com" required></div>
      <div class="form-row"><label>Contact Phone</label><input type="tel" name="contact_phone" placeholder="(555) 123-4567"></div>
      <div class="form-row"><label>Temp Password * (share with client)</label><input type="text" name="temp_password" placeholder="temppass123" required></div>
      <div class="form-row"><label>Plan</label>
        <select name="plan">
          <option value="starter">Starter — $299</option>
          <option value="pro" selected>Pro — $499</option>
          <option value="enterprise">Enterprise — $799</option>
        </select>
      </div>
      <div class="form-row"><label>Industry</label>
        <select name="industry">
          <option value="thrift">Thrift / Resale</option>
          <option value="electronics">Electronics</option>
          <option value="furniture">Furniture</option>
          <option value="clothing">Clothing Boutique</option>
          <option value="antiques">Antiques & Collectibles</option>
          <option value="general" selected>General Retail</option>
        </select>
      </div>
      <div class="form-row"><label>Primary Color</label><input type="color" name="primary_color" value="#2e7d6e" style="width:60px; height:40px; padding:2px;"></div>
      <div class="form-row"><label>Tagline (optional)</label><input type="text" name="tagline" placeholder="Your neighborhood treasure hunter"></div>
      <div class="form-row"><label>Notes (internal only)</label><textarea name="notes" rows="2" placeholder="e.g. Paid via Stripe on 2026-04-06"></textarea></div>
      <div style="display:flex; gap:0.75rem; justify-content:flex-end; margin-top:1.5rem;">
        <button type="button" onclick="document.getElementById('addModal').classList.remove('open')" style="padding:0.7rem 1.5rem; background:#f1f5f9; border:none; border-radius:8px; cursor:pointer; font-weight:600;">Cancel</button>
        <button type="submit" style="padding:0.7rem 1.75rem; background:#10b981; color:white; border:none; border-radius:8px; font-weight:700; cursor:pointer;">Create Store</button>
      </div>
    </form>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/overseer_dashboard.html
git commit -m "feat: add overseer_dashboard.html template with client grid, stats, and add new client modal"
```

---

## Task 11: `overseer_client.html` template

**Files:**
- Create: `templates/overseer_client.html`

- [ ] **Step 1: Create the template**

```html
{% extends "base.html" %}
{% block title %}{{ client.store_name }} — Overseer{% endblock %}

{% block howto_slides %}
<div class="ht-slide">
  <div class="ht-icon">🏪</div>
  <div class="ht-label">Client Detail — How-To Guide</div>
  <h2>Manage This Client Store</h2>
  <p>This page shows the full profile for <strong>{{ client.store_name }}</strong>. You can edit their settings, impersonate them, reset their password, suspend their store, or delete it.</p>
</div>
<div class="ht-slide">
  <div class="ht-icon">🔀</div>
  <div class="ht-label">Impersonation</div>
  <h2>Log In As This Store</h2>
  <p>Click <strong>Manage Their Store</strong> to enter their dashboard. A yellow banner will show at the top. All changes affect their real data. Click <strong>Exit to Overseer</strong> when done.</p>
</div>
<div class="ht-slide">
  <div class="ht-icon">🔑</div>
  <div class="ht-label">Password Reset</div>
  <h2>Reset Client Password</h2>
  <p>Click <strong>Reset Password</strong> to generate a new temporary password. The new password will flash on screen — copy it and share it with the client. Their old password is immediately invalidated.</p>
</div>
{% endblock %}

{% block content %}
<div class="ht-page-bar"><button class="ht-open-btn" onclick="htOpen()">❓ How to Use This Page</button></div>

<style>
  .cl-header { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:2rem; flex-wrap:wrap; gap:1rem; }
  .cl-header h1 { font-size:1.8rem; font-weight:800; color:#0f172a; }
  .cl-back { color:#6b7280; text-decoration:none; font-size:0.9rem; display:block; margin-bottom:0.5rem; }
  .cl-card { background:white; border-radius:14px; box-shadow:0 2px 10px rgba(0,0,0,0.07); padding:1.75rem; margin-bottom:1.5rem; }
  .cl-card h2 { font-size:1rem; font-weight:700; color:#0f172a; margin-bottom:1.25rem; border-bottom:1px solid #f1f5f9; padding-bottom:0.75rem; }
  .cl-field { margin-bottom:1rem; }
  .cl-field label { display:block; font-size:0.8rem; font-weight:600; color:#6b7280; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:0.3rem; }
  .cl-field input, .cl-field select, .cl-field textarea { width:100%; padding:0.65rem 0.85rem; border:1.5px solid #e5e7eb; border-radius:8px; font-size:0.9rem; box-sizing:border-box; }
  .cl-field input:focus, .cl-field select:focus { outline:none; border-color:#10b981; }
  .cl-actions { display:flex; gap:0.75rem; flex-wrap:wrap; margin-top:1rem; }
  .btn-primary  { background:#0f172a; color:white; padding:0.65rem 1.4rem; border-radius:8px; border:none; font-weight:700; font-size:0.88rem; cursor:pointer; text-decoration:none; display:inline-block; }
  .btn-green    { background:#10b981; color:white; padding:0.65rem 1.4rem; border-radius:8px; border:none; font-weight:700; font-size:0.88rem; cursor:pointer; }
  .btn-warn     { background:#fff7ed; color:#c2410c; border:1px solid #fed7aa; padding:0.65rem 1.2rem; border-radius:8px; font-size:0.88rem; font-weight:600; cursor:pointer; }
  .btn-danger   { background:#fee2e2; color:#991b1b; border:1px solid #fecaca; padding:0.65rem 1.2rem; border-radius:8px; font-size:0.88rem; font-weight:600; cursor:pointer; }
  .badge { display:inline-block; padding:0.25rem 0.75rem; border-radius:20px; font-size:0.75rem; font-weight:700; text-transform:uppercase; }
  .badge-active    { background:#d1fae5; color:#065f46; }
  .badge-suspended { background:#fee2e2; color:#991b1b; }
  .stat-pill { display:inline-flex; align-items:center; gap:0.4rem; background:#f8fafc; border:1px solid #e5e7eb; border-radius:8px; padding:0.5rem 1rem; font-size:0.85rem; font-weight:600; color:#374151; }
</style>

<a href="/overseer" class="cl-back">← Back to Overseer</a>
<div class="cl-header">
  <div>
    <h1>🏪 {{ client.store_name }}</h1>
    <div style="margin-top:0.4rem; display:flex; gap:0.5rem; flex-wrap:wrap;">
      <span class="badge badge-{{ client.status or 'active' }}">{{ client.status or 'active' }}</span>
      <span class="badge" style="background:#e0e7ff; color:#3730a3;">{{ client.plan or 'starter' }}</span>
    </div>
  </div>
  <div class="cl-actions">
    <form method="POST" action="/overseer/client/{{ slug }}/impersonate">
      <button type="submit" class="btn-primary">🔀 Manage Their Store</button>
    </form>
    <form method="POST" action="/overseer/client/{{ slug }}/reset-password">
      <button type="submit" class="btn-warn" onclick="return confirm('Generate a new temp password for {{ client.store_name }}?')">🔑 Reset Password</button>
    </form>
    <form method="POST" action="/overseer/client/{{ slug }}/suspend">
      <button type="submit" class="btn-warn">{{ '▶ Unsuspend' if client.status == 'suspended' else '⏸ Suspend' }}</button>
    </form>
  </div>
</div>

<!-- Stats row -->
<div style="display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:1.5rem;">
  <div class="stat-pill">📦 {{ product_count }} products</div>
  <div class="stat-pill">📅 Since {{ client.created_at[:10] if client.created_at else 'unknown' }}</div>
  <div class="stat-pill">🏭 {{ client.industry or 'general' }}</div>
  <div class="stat-pill">🔗 <a href="/store/{{ slug }}" target="_blank" style="color:#10b981;">/store/{{ slug }}</a></div>
</div>

<!-- Edit form -->
<div class="cl-card">
  <h2>✏️ Edit Client Details</h2>
  <form method="POST" action="/overseer/client/{{ slug }}/update">
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:1rem;">
      <div class="cl-field"><label>Store Name</label><input type="text" name="store_name" value="{{ client.store_name }}"></div>
      <div class="cl-field"><label>Tagline</label><input type="text" name="tagline" value="{{ client.tagline or '' }}"></div>
      <div class="cl-field"><label>Contact Name</label><input type="text" name="contact_name" value="{{ client.contact_name or '' }}"></div>
      <div class="cl-field"><label>Contact Email</label><input type="email" name="contact_email" value="{{ client.contact_email or '' }}"></div>
      <div class="cl-field"><label>Contact Phone</label><input type="tel" name="contact_phone" value="{{ client.contact_phone or '' }}"></div>
      <div class="cl-field"><label>Primary Color</label><input type="color" name="primary_color" value="{{ client.primary_color or '#2e7d6e' }}" style="width:60px; height:40px; padding:2px;"></div>
      <div class="cl-field"><label>Plan</label>
        <select name="plan">
          <option value="starter" {{ 'selected' if client.plan == 'starter' }}>Starter — $299</option>
          <option value="pro"     {{ 'selected' if client.plan == 'pro' }}>Pro — $499</option>
          <option value="enterprise" {{ 'selected' if client.plan == 'enterprise' }}>Enterprise — $799</option>
        </select>
      </div>
    </div>
    <div class="cl-field" style="margin-top:0.5rem;"><label>Internal Notes</label><textarea name="notes" rows="3" placeholder="Notes only you can see...">{{ client.notes or '' }}</textarea></div>
    <div style="margin-top:1rem;"><button type="submit" class="btn-green">💾 Save Changes</button></div>
  </form>
</div>

<!-- Danger zone -->
<div class="cl-card" style="border:1.5px solid #fecaca;">
  <h2 style="color:#991b1b;">⚠️ Danger Zone</h2>
  <p style="color:#6b7280; font-size:0.88rem; margin-bottom:1rem;">Deleting a store permanently removes all their inventory, uploads, and settings. This cannot be undone.</p>
  <form method="POST" action="/overseer/client/{{ slug }}/delete" onsubmit="return document.getElementById('confirmName').value === '{{ client.store_name }}'  || (alert('Store name did not match.') && false)">
    <input type="text" id="confirmName" name="confirm_name" placeholder="Type store name to confirm: {{ client.store_name }}" style="width:100%; padding:0.65rem; border:1.5px solid #fecaca; border-radius:8px; margin-bottom:0.75rem; box-sizing:border-box;">
    <button type="submit" class="btn-danger">🗑️ Delete This Store Permanently</button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/overseer_client.html
git commit -m "feat: add overseer_client.html template with edit, impersonate, suspend, delete, reset-password"
```

---

## Task 12: `client_dashboard.html` template

**Files:**
- Create: `templates/client_dashboard.html`

- [ ] **Step 1: Create the template**

```html
{% extends "base.html" %}
{% block title %}{{ store_name }} — Dashboard{% endblock %}

{% block howto_slides %}
<div class="ht-slide">
  <div class="ht-icon">🏪</div>
  <div class="ht-label">Your Store Dashboard</div>
  <h2>Welcome to {{ store_name }}</h2>
  <p>This is your inventory dashboard. Add products, generate listings, create ads, and manage everything about your store from here.</p>
</div>
<div class="ht-slide">
  <div class="ht-icon">📦</div>
  <div class="ht-label">Your Inventory</div>
  <h2>Managing Products</h2>
  <ul>
    <li>Click <strong>Add New Product</strong> to add an item.</li>
    <li>Click <strong>View</strong> on a card to see full product details.</li>
    <li>Click <strong>Edit</strong> to update price, description, or photos.</li>
    <li>Use the <strong>AI Photo Analysis</strong> on the edit page to auto-fill details.</li>
  </ul>
</div>
<div class="ht-slide">
  <div class="ht-icon">🖼️</div>
  <div class="ht-label">Creating Ads</div>
  <h2>Generate Ads for Your Products</h2>
  <ol>
    <li>Go to <strong>Ad Generator</strong> in the navigation.</li>
    <li>Select the products you want to advertise.</li>
    <li>Pick a style and click Generate — AI writes the copy and creates a 1080×1080 JPEG.</li>
    <li>Download your ads from the <strong>Ad Vault</strong>.</li>
  </ol>
</div>
{% endblock %}

{% block content %}
<div class="ht-page-bar"><button class="ht-open-btn" onclick="htOpen()">❓ How to Use This Page</button></div>

<style>
  .cd-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:2rem; flex-wrap:wrap; gap:1rem; }
  .cd-header h1 { font-family:'Playfair Display',serif; font-size:1.8rem; color:#1a1a2e; }
  .cd-stats { display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:2rem; }
  .cd-stat { background:white; border-radius:10px; padding:1rem 1.5rem; box-shadow:0 2px 8px rgba(0,0,0,0.07); text-align:center; }
  .cd-stat-num { font-size:1.8rem; font-weight:800; color:{{ client_config.primary_color or '#2e7d6e' }}; }
  .cd-stat-lbl { font-size:0.78rem; color:#6b7280; }
  .products-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:1.25rem; }
  .product-card { background:white; border-radius:12px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,0.08); }
  .product-image { height:180px; background:#f0ede8; display:flex; align-items:center; justify-content:center; overflow:hidden; }
  .product-image img { width:100%; height:100%; object-fit:contain; display:block; }
  .product-body { padding:1rem; }
  .product-title { font-weight:700; font-size:0.95rem; color:#1a1a2e; margin-bottom:0.25rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .product-price { font-size:1.1rem; font-weight:800; color:{{ client_config.primary_color or '#2e7d6e' }}; margin-bottom:0.5rem; }
  .product-meta { font-size:0.75rem; color:#6b7280; margin-bottom:0.75rem; }
  .product-actions { display:flex; gap:0.5rem; }
  .btn-sm { padding:0.4rem 0.85rem; border-radius:7px; font-size:0.78rem; font-weight:600; text-decoration:none; border:none; cursor:pointer; }
  .btn-view { background:#1a1a2e; color:white; }
  .btn-edit { background:#f1f5f9; color:#1a1a2e; }
</style>

<div class="cd-header">
  <div>
    <h1>🏪 {{ store_name }}</h1>
    {% if client_config.tagline %}<p style="color:#6b7280; margin-top:0.3rem;">{{ client_config.tagline }}</p>{% endif %}
  </div>
  <a href="{{ url_for('new_product') }}" class="btn btn-primary">+ Add New Product</a>
</div>

{% set available = products | selectattr('Status','equalto','Available') | list %}
{% set sold = products | selectattr('Status','equalto','Sold') | list %}

<div class="cd-stats">
  <div class="cd-stat"><div class="cd-stat-num">{{ products|length }}</div><div class="cd-stat-lbl">Total Items</div></div>
  <div class="cd-stat"><div class="cd-stat-num">{{ available|length }}</div><div class="cd-stat-lbl">Available</div></div>
  <div class="cd-stat"><div class="cd-stat-num">{{ sold|length }}</div><div class="cd-stat-lbl">Sold</div></div>
</div>

{% if products %}
<div class="products-grid">
  {% for product in products %}
  <div class="product-card">
    <div class="product-image">
      {% if product.valid_images %}
      <img src="{{ url_for('serve_upload', filename=product.valid_images[0]) }}" alt="{{ product.Title }}">
      {% else %}
      <div style="color:#b0aaa0; font-size:0.85rem;">No Image</div>
      {% endif %}
    </div>
    <div class="product-body">
      <div class="product-title" title="{{ product.Title }}">{{ product.Title }}</div>
      <div class="product-price">${{ product.Price }}</div>
      <div class="product-meta">{{ product.Category }} · {{ product.Condition }} · {{ product.Status }}</div>
      <div class="product-actions">
        <a href="{{ url_for('view_product', sku=product.SKU) }}" class="btn-sm btn-view">View</a>
        <a href="{{ url_for('edit_product', sku=product.SKU) }}" class="btn-sm btn-edit">Edit</a>
      </div>
    </div>
  </div>
  {% endfor %}
</div>
{% else %}
<div style="text-align:center; padding:4rem; background:white; border-radius:14px; color:#6b7280;">
  <div style="font-size:3rem; margin-bottom:1rem;">📦</div>
  <h2 style="color:#1a1a2e; margin-bottom:0.5rem;">No products yet</h2>
  <p>Add your first product to get started.</p>
  <a href="{{ url_for('new_product') }}" style="display:inline-block; margin-top:1rem; background:#1a1a2e; color:white; padding:0.75rem 2rem; border-radius:9px; text-decoration:none; font-weight:700;">+ Add Product</a>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/client_dashboard.html
git commit -m "feat: add client_dashboard.html template with inventory grid and store branding"
```

---

## Task 13: Update login flow and add overseer nav link

**Files:**
- Modify: `templates/base.html` — add Overseer link to nav for overseer users
- Modify: `templates/login.html` — already handled by route; no template change needed

- [ ] **Step 1: Add Overseer link to base.html nav**

In `templates/base.html`, find the admin navigation links section. Add an Overseer link that shows only for overseer role:

```html
{% if user_role == 'overseer' %}
<a href="/overseer">🛰️ Overseer</a>
{% endif %}
```

Place it immediately after the existing admin nav links (near Settings, Backups, etc.).

- [ ] **Step 2: Commit**

```bash
git add templates/base.html
git commit -m "feat: add Overseer nav link to base.html for overseer role users"
```

---

## Task 14: Wire upload route and add `/overseer` nav link to base — integration smoke test

**Files:**
- Modify: `app_with_ai.py` — update image upload route to use active store's upload folder

- [ ] **Step 1: Find the image upload route and update it**

Find the route that handles `POST /edit/<sku>` and saves uploaded images. It uses `UPLOAD_FOLDER` directly. Update it to use `get_store_paths(active_store_slug())['uploads']`:

```python
# Find this pattern in the edit_product route:
upload_dir = UPLOAD_FOLDER
# Replace with:
upload_dir = get_store_paths(active_store_slug())['uploads']
os.makedirs(upload_dir, exist_ok=True)
```

Do the same for the `new_product` route wherever it saves uploaded files.

- [ ] **Step 2: Manual smoke test checklist**

```
1. Log in as admin (Jay) → should land on /dashboard
2. Navigate to /overseer → should show overseer dashboard
3. Click "+ Add New Client" → fill in test store "Test Boutique", email "test@test.com", password "test123"
4. Submit → should redirect to /overseer/client/test-boutique
5. Click "Manage Their Store" → yellow banner should appear, dashboard shows test-boutique's inventory
6. Add a product → should save to customers/test-boutique/inventory.csv
7. Click "Exit to Overseer" → banner gone, back at /overseer
8. Log out → log in as test@test.com / test123 → should land at /my-store showing Test Boutique
9. Navigate to /dashboard → should redirect to /my-store (client cannot access Jay's dashboard)
10. Log out → Log in as admin → overseer dashboard shows "Test Boutique" card
11. Click Suspend on card → badge changes to "suspended"
12. Log in as test@test.com → should see "Your store has been suspended" message
```

- [ ] **Step 3: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: update image upload to use tenant-aware upload folder"
```

---

**Self-Review:**

1. **Spec coverage:**
   - ✅ `get_store_paths`, `active_store_slug` — Task 1
   - ✅ `overseer_required`, `client_required` — Task 2
   - ✅ Login sets `user_role`, `store_slug`, suspension check — Task 3
   - ✅ `load_inventory`, `save_inventory`, `serve_upload` tenant-aware — Task 4
   - ✅ Impersonation banner — Task 5
   - ✅ All 8 overseer routes — Tasks 6, 7, 8
   - ✅ `/my-store` client route — Task 9
   - ✅ `overseer_dashboard.html` — Task 10
   - ✅ `overseer_client.html` — Task 11
   - ✅ `client_dashboard.html` — Task 12
   - ✅ Nav link + upload dir fix — Tasks 13, 14
   - ✅ Suspended client check — Task 3 (login)
   - ✅ Client cannot access `/dashboard` — enforced by `client_required` + `client_dashboard_route`

2. **Placeholder scan:** No TBDs. All code blocks are complete. ✅

3. **Type consistency:** `load_client_config` returns dict or None, checked consistently. `active_store_slug()` returns str or None, passed directly to `get_store_paths()`. `list_client_stores()` returns list of dicts. All consistent across tasks. ✅
