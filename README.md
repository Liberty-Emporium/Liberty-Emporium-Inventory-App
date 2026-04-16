# Liberty Emporium Inventory Management

**AI-powered inventory management system for thrift stores with multi-tenant support.**

## Live Demo

- **Demo URL:** https://liberty-emporium-inventory-demo-app-production.up.railway.app
- **Production URL:** https://liberty-emporium-and-thrift-inventory-app-production.up.railway.app

## Login Credentials (Demo)
- Username: `admin`
- Password: `admin123`

---

## Features

### Core Inventory Management
- ✅ Add/edit/delete products
- ✅ Product images with AI analysis
- ✅ SKU tracking
- ✅ Category management (Clothing, Electronics, Furniture, etc.)
- ✅ Condition tracking (New, Like New, Good, Fair, Poor)
- ✅ Status tracking (Available, Sold, Reserved, Pending)

### AI Features
- ✅ AI-powered product descriptions (using Groq, Anthropic, xAI, Qwen)
- ✅ AI image analysis
- ✅ Auto-generate listings from product details
- ✅ Generate ad copy for products
- ✅ Multi-provider AI support (auto-detects provider)

### Marketing Tools
- ✅ Ad generator
- ✅ Ad vault
- ✅ Listing generator
- ✅ Export to Square
- ✅ Price tag generation

### Multi-Tenant System
- ✅ **Wizard** - Create new client stores via `Create new client store (wizard)` or overseer panel
- ✅ Individual store branding (colors, logo, tagline)
- ✅ Client login/management
- ✅ Overseer dashboard (super admin view)
- ✅ Client plans (Starter, Pro, Enterprise)
- ✅ Industry types (Thrift, Antique, Consignment, Electronics, etc.)

### Admin/Management
- ✅ User management
- ✅ Backup/restore
- ✅ Lead tracking
- ✅ Branding controls
- ✅ SMTP email configuration

---

## Routes

| Route | Description |
|-------|-------------|
| `/` | Landing page |
| `/login` | Demo login |
| `/dashboard` | Main dashboard |
| `/new` | Add new product |
| `/ad-vault` | View generated ads |
| `/listing-generator` | Generate listings |
| `/import-square` | Import from Square |
| `/seasonal-sale` | Manage sales |
| `/overseer` | Admin panel (create client stores) |
| `Create new client store (wizard)` | Create new client store |

---

## Tech Stack

- Python (Flask)
- SQLite
- Groq, Anthropic, xAI, Qwen APIs
- Deploy on Railway

---

## Multi-Tenant How-To

### Creating a New Client Store
1. Go to `/overseer` (logged in as admin)
2. Click **"➕ Add New Client"**
3. Fill in:
   - Store Name
   - Contact Email (login username)
   - Temporary Password
   - Plan (Starter/Pro/Enterprise)
   - Industry type
   - Brand colors
4. Click **"Provision Store"**
5. Client receives login details and can access their store at `/store/<slug>`

### Client Login
- Each client gets their own login at: `https://app.com/store/<store-slug>/login`
- Clients can only see their own products

---

## API Keys

To enable AI features, add environment variables:
- `GROQ_API_KEY` - Groq API key
- `ANTHROPIC_API_KEY` - Anthropic/Claude API key
- `XAI_API_KEY` - xAI API key

Or configure via the Settings popup in the app.

---

## Development

```bash
# Local setup
pip install -r requirements.txt
python app_with_ai.py

# Deploy to Railway
railway deploy
```

---

*Last updated: 2026-04-10*

---

## Using the Wizard to Create Client Stores

The **Wizard** (`Create new client store (wizard)`) is how you create new client stores in the multi-tenant system.

### Option 1: Direct Wizard
1. Go to `Create new client store (wizard)`
2. Fill in:
   - Store Name (e.g., "My Thrift Store")
   - Contact Email
   - Temporary Password
   - Plan (Starter/Pro/Enterprise)
   - Industry Type
   - Brand colors
3. Submit → Creates new client store!

### Option 2: Overseer Panel
1. Go to `/overseer` (logged in as admin)
2. Click "➕ Add New Client"
3. Same form as wizard

---

*Wizard documentation added: 2026-04-10*
# redeployed Thu Apr 16 22:07:35 UTC 2026
