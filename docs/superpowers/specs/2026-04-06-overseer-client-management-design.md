# Overseer Client Management System — Design Spec

**Date:** 2026-04-06  
**Author:** Jay Alexander  
**Status:** Approved

---

## Goal

Build a multi-tenant client management layer on top of the existing Liberty Emporium app. Jay (overseer) can provision, monitor, impersonate, and manage all client stores from one dashboard. Each paying client gets their own isolated store with their own inventory, branding, and login. Everything runs in the same Railway deployment.

---

## Architecture

### Data Storage Per Client

Each provisioned client store lives in:

```
customers/
  {slug}/
    config.json       ← store name, colors, tagline, plan, status, contact info, notes, created_at
    inventory.csv     ← their products (same schema as Liberty Emporium's inventory.csv)
    uploads/          ← their product images
    users.json        ← their login account(s) [email, hashed password, role]
```

The existing `customers/{slug}.json` (flat file from wizard) is migrated into `customers/{slug}/config.json` during provisioning. The `customers/leads.json` file stays as-is for pre-purchase leads.

### User Model Extension

Current `users.json` entries get two new optional fields:
- `role`: `"overseer"` | `"admin"` | `"staff"` | `"client"` — defaults to existing behavior if absent
- `store_slug`: slug of the client store this user belongs to (only set for `role: "client"`)

Jay's existing admin account gets `role: "overseer"` added.

### Session Context

Two new session keys:
- `session['store_slug']` — set when a client logs in; identifies their store
- `session['impersonating_slug']` — set when overseer impersonates a client; overrides all data paths

Helper function `active_store_slug()`:
```python
def active_store_slug():
    """Returns the slug of the currently active client store, or None for Liberty Emporium."""
    return session.get('impersonating_slug') or session.get('store_slug') or None
```

All data-reading helpers (`load_inventory`, `UPLOAD_FOLDER`, etc.) check `active_store_slug()` and route to the client's folder when non-None.

### Impersonation Banner

When `session['impersonating_slug']` is set, `base.html` shows a persistent yellow banner at the top of every page:

> 🔀 Managing **{Store Name}** — [Exit to Overseer]

Clicking "Exit to Overseer" calls `/overseer/exit-impersonate`, clears the session key, and redirects to `/overseer`.

---

## Routes

### Overseer Routes (all require `role: overseer`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/overseer` | Overseer dashboard — all clients grid |
| GET | `/overseer/client/<slug>` | Single client detail page |
| POST | `/overseer/client/create` | Provision a new client store |
| POST | `/overseer/client/<slug>/impersonate` | Start impersonating a client |
| GET | `/overseer/exit-impersonate` | End impersonation, return to overseer |
| POST | `/overseer/client/<slug>/update` | Update client config/notes/plan |
| POST | `/overseer/client/<slug>/suspend` | Toggle client active/suspended status |
| POST | `/overseer/client/<slug>/delete` | Delete client store (with confirmation) |
| POST | `/overseer/client/<slug>/reset-password` | Generate new temp password for client |

### Client Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/my-store` | Redirect to client's dashboard (requires `role: client`) |

### Modified Existing Routes

The existing `/dashboard`, `/edit/<sku>`, `/product/<sku>`, `/ads`, `/ad-vault`, `/listing-generator`, and all inventory routes are updated to respect `active_store_slug()` — routing data reads/writes to the client's folder during impersonation.

---

## Templates

### `overseer_dashboard.html`

- Stats bar: Total Clients, Active, Suspended, Total Revenue (sum of plan prices)
- Client grid: one card per store showing store name, plan badge, status badge, industry, date added, contact email
- Each card has: **Manage** (impersonate), **Details**, **Suspend** buttons
- **+ Add New Client** button opens a modal/form
- Extends `base.html`, has its own how-to slides

### `overseer_client.html`

- Full client profile: store name, slug, contact info, plan, status, industry, colors, tagline
- Internal notes textarea (Jay only)
- **Log in as this store** button → POST `/overseer/client/<slug>/impersonate`
- **Edit** inline fields for branding, plan, status
- **Reset Password** button → shows generated temp password
- **Suspend / Unsuspend** toggle
- **Delete Store** button with typed confirmation (`type the store name to confirm`)
- Inventory summary: product count, last updated
- Extends `base.html`, has how-to slides

### `client_dashboard.html`

- Simplified version of the existing dashboard
- Shows the client's own inventory grid
- No admin-only controls (no user management, no settings, no leads)
- Shows their store name and branding
- Navigation: Dashboard, Add Product, Listings, Ad Generator, Ad Vault
- Impersonation banner shows when Jay is managing them
- Extends `base.html`

---

## Client Login Flow

1. Client visits `/login` and enters their email + password
2. Login handler checks `users.json` as normal — if matched user has `role: "client"`, sets `session['store_slug']` to their `store_slug`
3. Redirect goes to `/my-store` instead of `/dashboard`
4. `/my-store` renders `client_dashboard.html` with data from `customers/{slug}/`

Overseer and admin/staff users are unaffected — login works exactly as before.

---

## New Client Provisioning Flow

Jay fills out the **+ Add New Client** form in the overseer dashboard:

**Fields:**
- Store Name (required)
- Contact Email (required — becomes their login)
- Temporary Password (required — Jay shares this with client)
- Plan: Starter / Pro / Enterprise
- Industry (dropdown — same options as wizard)
- Primary Color (color picker)
- Tagline (optional)
- Notes (optional — internal only)

**On submit:**
1. Slugify store name, ensure unique
2. Create `customers/{slug}/` folder structure
3. Write `customers/{slug}/config.json` with all fields + `status: "active"`, `created_at`
4. Copy industry sample products into `customers/{slug}/inventory.csv`
5. Create `customers/{slug}/uploads/` directory
6. Write `customers/{slug}/users.json` with the client's hashed email + password entry (`role: "client"`, `store_slug: slug`)
7. Flash success with the client's login URL

---

## Impersonation Flow

1. Jay clicks **Manage** on a client card (or **Log in as this store** on detail page)
2. POST to `/overseer/client/<slug>/impersonate`
3. Sets `session['impersonating_slug'] = slug`
4. Redirects to `/dashboard`
5. All data operations now use `customers/{slug}/` paths
6. Yellow impersonation banner visible on every page
7. Jay clicks **Exit to Overseer** → clears `session['impersonating_slug']` → redirects to `/overseer`

---

## Data Isolation

Helper `get_store_paths(slug=None)` returns a dict of paths for a given store:

```python
def get_store_paths(slug=None):
    if slug:
        base = os.path.join(CUSTOMERS_DIR, slug)
        return {
            'inventory': os.path.join(base, 'inventory.csv'),
            'uploads':   os.path.join(base, 'uploads'),
            'users':     os.path.join(base, 'users.json'),
            'config':    os.path.join(base, 'config.json'),
        }
    return {
        'inventory': INVENTORY_FILE,
        'uploads':   UPLOAD_FOLDER,
        'users':     USERS_FILE,
        'config':    STORE_CONFIG_FILE,
    }
```

Existing `load_inventory()`, `save_inventory()`, `serve_upload()` functions updated to call `get_store_paths(active_store_slug())` instead of using global constants directly.

---

## Decorators

New decorator `overseer_required`:
```python
def overseer_required(f):
    # Redirects to /dashboard if user role != 'overseer'
```

New decorator `client_required`:
```python
def client_required(f):
    # Redirects to /login if no store_slug in session
```

---

## Suspended Clients

When a client store is suspended:
- `config.json` has `status: "suspended"`
- Client login attempt shows: "Your store has been suspended. Contact support."
- Store's public demo page (`/store/{slug}`) shows a "temporarily unavailable" message
- Overseer can still impersonate and view/edit a suspended store

---

## Security Notes

- Impersonation only available to users with `role: "overseer"` — no other role can set `impersonating_slug`
- Client users can only read/write their own `store_slug` — enforced by `active_store_slug()` returning `session['store_slug']` only (not arbitrary slugs)
- Client cannot access `/overseer/*`, `/admin/*`, or `/dashboard` (Liberty Emporium's own dashboard) — redirected to `/my-store`
- Passwords hashed with `werkzeug.security.generate_password_hash` (same as existing system)

---

## What Is NOT in Scope

- Email sending (welcome emails, password reset emails) — Jay manually shares credentials
- Billing/subscription tracking beyond plan name stored in config
- Client-to-client isolation beyond file path routing (no row-level DB security needed — each client has their own files)
- Public API for clients
