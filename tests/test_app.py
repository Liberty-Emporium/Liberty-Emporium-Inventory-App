"""
Tests for Liberty Inventory App
Covers: health, public routes, auth, inventory CRUD routes, security headers
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('DATABASE_URL', '')
os.environ.setdefault('STRIPE_SECRET_KEY', 'sk_test_placeholder')
os.environ.setdefault('STRIPE_PUBLISHABLE_KEY', 'pk_test_placeholder')

import app_with_ai as inv


@pytest.fixture
def client(tmp_path):
    inv.app.config['TESTING'] = True
    inv.app.config['SECRET_KEY'] = 'test-secret-key'
    # Liberty Inventory uses file-based storage (CSV/JSON) + USER_KEYS_DB
    inv.DATA_DIR = str(tmp_path)
    inv.USER_KEYS_DB = str(tmp_path / 'user_api_keys.db')
    inv.UPLOAD_FOLDER = str(tmp_path / 'uploads')
    inv.BACKUP_FOLDER = str(tmp_path / 'backups')
    inv.ADS_FOLDER = str(tmp_path / 'ads')
    inv.MUSIC_FOLDER = str(tmp_path / 'music')
    inv.CUSTOMERS_DIR = str(tmp_path / 'customers')
    inv.USERS_FILE = str(tmp_path / 'users.json')
    inv.INVENTORY_FILE = str(tmp_path / 'inventory.csv')
    for d in [inv.UPLOAD_FOLDER, inv.BACKUP_FOLDER, inv.ADS_FOLDER, inv.MUSIC_FOLDER, inv.CUSTOMERS_DIR]:
        os.makedirs(d, exist_ok=True)
    with inv.app.test_client() as c:
        with inv.app.app_context():
            inv.init_user_keys_db()
        yield c


# ── Health ────────────────────────────────────────────────────────────────────

def test_health_returns_ok(client):
    res = client.get('/health')
    assert res.status_code == 200

def test_healthz_returns_ok(client):
    assert client.get('/healthz').status_code == 200

def test_ping_returns_200(client):
    assert client.get('/ping').status_code == 200


# ── Public pages ──────────────────────────────────────────────────────────────

def test_index_returns_200(client):
    assert client.get('/').status_code == 200

def test_login_page_returns_200(client):
    assert client.get('/login').status_code == 200

def test_signup_page_returns_200(client):
    assert client.get('/signup').status_code == 200

def test_guest_page_returns_200(client):
    res = client.get('/guest', follow_redirects=True)
    assert res.status_code == 200


# ── Auth — protected routes ───────────────────────────────────────────────────

def test_dashboard_requires_login(client):
    res = client.get('/dashboard', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_add_product_requires_login(client):
    res = client.get('/new', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_ads_requires_login(client):
    res = client.get('/ads', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_listing_generator_requires_login(client):
    res = client.get('/listing-generator', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_edit_product_requires_login(client):
    res = client.get('/edit/SOMESKU', follow_redirects=False)
    assert res.status_code in (302, 401)


# ── Login flow ────────────────────────────────────────────────────────────────

def test_login_wrong_credentials(client):
    res = client.post('/login', data={
        'username': 'nobody',
        'password': 'badpass'
    }, follow_redirects=True)
    assert res.status_code == 200
    assert (b'invalid' in res.data.lower() or
            b'incorrect' in res.data.lower() or
            b'wrong' in res.data.lower() or
            b'error' in res.data.lower())


# ── Inventory routes with non-existent SKU ────────────────────────────────────

def test_product_detail_unknown_sku_404(client):
    res = client.get('/product/FAKESKUABC')
    assert res.status_code in (404, 302, 401)

def test_delete_unknown_sku_requires_auth(client):
    res = client.post('/delete/FAKESKUABC', follow_redirects=False)
    assert res.status_code in (302, 401, 403, 404)


# ── Security headers ──────────────────────────────────────────────────────────

def test_x_content_type_header_on_index(client):
    res = client.get('/')
    assert 'X-Content-Type-Options' in res.headers

def test_x_frame_options_on_index(client):
    res = client.get('/')
    assert 'X-Frame-Options' in res.headers
