# Overseer Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an AI chat panel embedded in the Overseer Dashboard that monitors 14-day trials, drafts payment follow-up emails, answers business questions, and executes management actions (suspend, unsuspend, reset password) on Jay's behalf.

**Architecture:** A two-column layout on `/overseer` — left is the existing client grid, right is the Overseer Assistant panel. On page load, the backend scans for expiring trials and pre-generates draft emails. Jay can also type any question or command into a chat input; messages are sent to `/overseer/assistant/chat` which builds a live business snapshot, calls Claude Haiku, and returns structured responses (plain text, email drafts, or action JSON). Emails are sent via `smtplib` using credentials stored in `app_config.json`.

**Tech Stack:** Flask, Python 3.11, Claude Haiku (`urllib.request` — same pattern as `generate_ad_copy`), `smtplib` (stdlib), Jinja2, vanilla JS (fetch + DOM), existing `app_with_ai.py` patterns.

---

## File Structure

**Modified:**
- `app_with_ai.py` — add `get_smtp_config`, `save_smtp_config`, `send_smtp_email`, `build_assistant_context`, `overseer_assistant_chat`, `overseer_assistant_send_email`, `overseer_assistant_alerts` functions/routes; update `admin_settings` route to pass SMTP config
- `templates/overseer_dashboard.html` — two-column layout, assistant panel, chat UI
- `templates/admin_settings.html` — add SMTP configuration section

---

## Task 1: SMTP config helpers

**Files:**
- Modify: `app_with_ai.py` — add after `save_stripe_keys` (~line 170)

- [ ] **Step 1: Add `get_smtp_config` and `save_smtp_config` to `app_with_ai.py`**

Find `save_stripe_keys` in `app_with_ai.py`. After its closing line, add:

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: add get_smtp_config, save_smtp_config, send_smtp_email helpers"
```

---

## Task 2: SMTP settings section in admin settings

**Files:**
- Modify: `app_with_ai.py` — update `admin_settings` route (~line 1644)
- Modify: `templates/admin_settings.html` — add SMTP form section

- [ ] **Step 1: Update `admin_settings` route to pass SMTP config**

Find the `admin_settings` function. Replace its `return render_template(...)` call with:

```python
    return render_template('admin_settings.html',
        anthropic_key_set=bool(get_ai_api_key()),
        stripe_secret_key=get_stripe_keys()[0],
        stripe_public_key=get_stripe_keys()[1],
        smtp_config=get_smtp_config(),
        **ctx()
    )
```

- [ ] **Step 2: Add POST handler for SMTP save**

Find `admin_settings_stripe` route. After its closing line, add:

```python
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
```

- [ ] **Step 3: Add SMTP section to `templates/admin_settings.html`**

Open `templates/admin_settings.html`. Find the closing `{% endblock %}` at the bottom of the `{% block content %}`. Just before it, add:

```html
<!-- ── SMTP Email Settings ──────────────────────────────────────────────── -->
<div class="settings-section" style="margin-top:2rem;">
  <h2 style="font-family:'Playfair Display',serif;font-size:1.3rem;color:#1a1a2e;margin:0 0 1rem;">📧 Email (SMTP)</h2>
  <p style="color:#555;font-size:0.9rem;margin-bottom:1.25rem;">Used by the Overseer Assistant to send follow-up emails to clients. For Gmail, use <strong>smtp.gmail.com</strong> port <strong>587</strong> and an <a href="https://support.google.com/accounts/answer/185833" target="_blank" style="color:#2e7d6e;">App Password</a>.</p>
  <form method="POST" action="/admin/settings/smtp">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem;">
      <div>
        <label style="display:block;font-size:0.82rem;font-weight:600;color:#444;margin-bottom:0.3rem;">SMTP Host</label>
        <input type="text" name="smtp_host" value="{{ smtp_config.smtp_host }}" placeholder="smtp.gmail.com" style="width:100%;padding:0.55rem 0.75rem;border:1.5px solid #d1c9bc;border-radius:8px;font-size:0.9rem;box-sizing:border-box;">
      </div>
      <div>
        <label style="display:block;font-size:0.82rem;font-weight:600;color:#444;margin-bottom:0.3rem;">Port</label>
        <select name="smtp_port" style="width:100%;padding:0.55rem 0.75rem;border:1.5px solid #d1c9bc;border-radius:8px;font-size:0.9rem;box-sizing:border-box;">
          <option value="587" {% if smtp_config.smtp_port == 587 %}selected{% endif %}>587 (TLS — recommended)</option>
          <option value="465" {% if smtp_config.smtp_port == 465 %}selected{% endif %}>465 (SSL)</option>
        </select>
      </div>
      <div>
        <label style="display:block;font-size:0.82rem;font-weight:600;color:#444;margin-bottom:0.3rem;">Email Address</label>
        <input type="email" name="smtp_user" value="{{ smtp_config.smtp_user }}" placeholder="you@gmail.com" style="width:100%;padding:0.55rem 0.75rem;border:1.5px solid #d1c9bc;border-radius:8px;font-size:0.9rem;box-sizing:border-box;">
      </div>
      <div>
        <label style="display:block;font-size:0.82rem;font-weight:600;color:#444;margin-bottom:0.3rem;">Password / App Password</label>
        <input type="password" name="smtp_password" value="{{ smtp_config.smtp_password }}" placeholder="••••••••••••••••" style="width:100%;padding:0.55rem 0.75rem;border:1.5px solid #d1c9bc;border-radius:8px;font-size:0.9rem;box-sizing:border-box;">
      </div>
    </div>
    <div style="display:flex;gap:0.75rem;">
      <button type="submit" style="background:#1a1a2e;color:white;border:none;padding:0.6rem 1.3rem;border-radius:8px;font-weight:700;cursor:pointer;">💾 Save Email Settings</button>
    </div>
  </form>
  <form method="POST" action="/admin/settings/smtp/test" style="margin-top:0.75rem;">
    <button type="submit" style="background:#f0ede8;color:#1a1a2e;border:none;padding:0.55rem 1.1rem;border-radius:8px;font-weight:600;cursor:pointer;">📨 Send Test Email</button>
  </form>
</div>
```

- [ ] **Step 4: Commit**

```bash
git add app_with_ai.py templates/admin_settings.html
git commit -m "feat: add SMTP settings section — save, test email, admin settings page"
```

---

## Task 3: Assistant context builder + chat route

**Files:**
- Modify: `app_with_ai.py` — add before `# ── Run` section

- [ ] **Step 1: Add `build_assistant_context` helper**

Before the `# ── Run` line at the bottom of `app_with_ai.py`, add:

```python
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

    mrr = len(active_stores) * 20
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

    lines += [
        "",
        "TRIAL SIGNUPS:",
    ]
    if trial_leads:
        for t in trial_leads:
            dr = t.get('_days_remaining')
            dr_str = f"{dr} days remaining" if dr is not None and dr >= 0 else (f"EXPIRED {abs(dr)} days ago" if dr is not None else "unknown")
            lines.append(
                f"  - {t.get('store_name','?')} | {t.get('contact_email','?')} | trial ends: {t.get('trial_end','?')[:10] if t.get('trial_end') else '?'} ({dr_str})"
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
```

- [ ] **Step 2: Add `/overseer/assistant/chat` route**

Directly after `build_assistant_context`, add:

```python
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
                        return jsonify({'reply': f'User email not found in store users.', 'type': 'text'})
                elif parsed.get('type') == 'email':
                    return jsonify({
                        'type':    'email',
                        'reply':   'Here\'s a draft email for you to review:',
                        'to':      parsed.get('to', ''),
                        'subject': parsed.get('subject', ''),
                        'body':    parsed.get('body', ''),
                    })
            except (json.JSONDecodeError, KeyError):
                pass

    return jsonify({'reply': reply_text, 'type': 'text'})
```

- [ ] **Step 3: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: add build_assistant_context and overseer_assistant_chat route"
```

---

## Task 4: Send email + proactive alerts routes

**Files:**
- Modify: `app_with_ai.py` — add after `overseer_assistant_chat`

- [ ] **Step 1: Add `/overseer/assistant/send-email` route**

```python
@app.route('/overseer/assistant/send-email', methods=['POST'])
@login_required
@overseer_required
def overseer_assistant_send_email():
    data    = request.get_json(force=True)
    to      = data.get('to', '').strip()
    subject = data.get('subject', '').strip()
    body    = data.get('body', '').strip()
    if not to or not subject or not body:
        return jsonify({'success': False, 'error': 'to, subject, and body are required.'})
    ok, err = send_smtp_email(to, subject, body)
    if ok:
        return jsonify({'success': True, 'message': f'Sent to {to}'})
    return jsonify({'success': False, 'error': err})
```

- [ ] **Step 2: Add `/overseer/assistant/alerts` route**

```python
@app.route('/overseer/assistant/alerts', methods=['POST'])
@login_required
@overseer_required
def overseer_assistant_alerts():
    """Return pre-generated alert cards for trials expiring within 3 days."""
    leads = load_leads()
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
            days_label    = "today" if days == 0 else (f"tomorrow" if days == 1 else f"in {days} days")
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
                    f"To keep your store running, the one-time setup fee is $99 and then just $20/month after that. "
                    f"I'll send you a payment link as soon as you're ready.\n\n"
                    f"Feel free to reply to this email with any questions.\n\n"
                    f"— Jay\nLiberty Emporium Programs"
                ),
            })
    alerts.sort(key=lambda a: a['days'])
    return jsonify({'alerts': alerts})
```

- [ ] **Step 3: Commit**

```bash
git add app_with_ai.py
git commit -m "feat: add overseer send-email and proactive alerts routes"
```

---

## Task 5: Two-column layout + assistant panel in overseer dashboard

**Files:**
- Modify: `templates/overseer_dashboard.html` — full rewrite of layout

- [ ] **Step 1: Replace the entire contents of `templates/overseer_dashboard.html`**

The existing file has a single-column layout. Replace it with the version below (keep all existing CSS classes, add the assistant panel):

```html
{% extends "base.html" %}

{% block title %}Overseer Dashboard — Liberty Emporium{% endblock %}

{% block howto_slides %}
<div class="ht-slide">
  <div class="ht-icon">🛡️</div>
  <div class="ht-label">Overseer Dashboard — How-To Guide</div>
  <h2>Manage All Client Stores</h2>
  <p>This is your command center. Provision new client stores, manage accounts, and let the AI Assistant handle follow-ups and business questions.</p>
</div>
<div class="ht-slide">
  <div class="ht-icon">🤖</div>
  <div class="ht-label">Overseer Assistant</div>
  <h2>Your AI Business Assistant</h2>
  <p>The assistant panel on the right monitors trials, drafts follow-up emails, and answers questions about your business. Type anything — "who's trial ends soon?", "draft a follow-up for Mike", "suspend Store X".</p>
</div>
<div class="ht-slide">
  <div class="ht-icon">📧</div>
  <div class="ht-label">Sending Emails</div>
  <h2>Review &amp; Send Draft Emails</h2>
  <ol>
    <li>The assistant drafts the email — you can edit any field.</li>
    <li>Click <strong>Send</strong> to deliver it from your email account.</li>
    <li>Set up your email credentials once in <strong>Admin → Settings</strong>.</li>
  </ol>
</div>
{% endblock %}

{% block content %}
<div class="ht-page-bar"><button class="ht-open-btn" onclick="htOpen()">❓ How to Use This Page</button></div>
<style>
  .ov-layout { display:grid; grid-template-columns:1fr 380px; gap:1.5rem; align-items:start; }
  @media(max-width:900px){ .ov-layout { grid-template-columns:1fr; } }

  /* Left column */
  .ov-header { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:1.5rem; flex-wrap:wrap; gap:1rem; }
  .ov-header h1 { font-family:'Playfair Display',serif; font-size:2rem; color:#1a1a2e; margin:0; }
  .ov-stats { display:flex; gap:0.75rem; flex-wrap:wrap; margin-bottom:1.5rem; }
  .ov-stat { background:white; border-radius:10px; padding:0.85rem 1.2rem; box-shadow:0 2px 8px rgba(0,0,0,0.08); text-align:center; min-width:110px; }
  .ov-stat-val { font-family:'Playfair Display',serif; font-size:1.8rem; font-weight:900; color:#1a1a2e; line-height:1; }
  .ov-stat-lbl { font-size:0.7rem; color:#7a7a8c; text-transform:uppercase; letter-spacing:0.07em; margin-top:0.2rem; }
  .ov-stat.revenue .ov-stat-val { color:#166534; }
  .client-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:1rem; }
  .client-card { background:white; border-radius:12px; box-shadow:0 2px 10px rgba(0,0,0,0.08); overflow:hidden; display:flex; flex-direction:column; }
  .client-card-header { padding:1rem 1.1rem 0.6rem; border-bottom:1px solid #f0ede8; }
  .client-card-name { font-family:'Playfair Display',serif; font-size:1rem; color:#1a1a2e; font-weight:700; margin:0 0 0.25rem; }
  .client-card-meta { font-size:0.75rem; color:#7a7a8c; }
  .client-card-body { padding:0.65rem 1.1rem; flex:1; }
  .badge { display:inline-block; padding:0.18rem 0.55rem; border-radius:20px; font-size:0.7rem; font-weight:700; margin-right:0.25rem; }
  .badge-active { background:#dcfce7; color:#166534; }
  .badge-suspended { background:#fee2e2; color:#991b1b; }
  .badge-starter { background:#e0f2fe; color:#075985; }
  .badge-pro { background:#fef9c3; color:#854d0e; }
  .badge-enterprise { background:#f3e8ff; color:#6b21a8; }
  .client-card-actions { padding:0.6rem 1.1rem 0.85rem; display:flex; gap:0.4rem; border-top:1px solid #f0ede8; }
  .btn-manage { background:#1a1a2e; color:white; border:none; padding:0.4rem 0.9rem; border-radius:7px; font-size:0.78rem; font-weight:700; cursor:pointer; text-decoration:none; }
  .btn-manage:hover { opacity:0.85; color:white; }
  .btn-details { background:#f0ede8; color:#1a1a2e; border:none; padding:0.4rem 0.9rem; border-radius:7px; font-size:0.78rem; font-weight:600; cursor:pointer; text-decoration:none; }
  .btn-details:hover { background:#e8e0d4; color:#1a1a2e; }
  .btn-add { background:linear-gradient(135deg,#2e7d6e,#10b981); color:white; border:none; padding:0.6rem 1.2rem; border-radius:9px; font-size:0.85rem; font-weight:700; cursor:pointer; display:inline-flex; align-items:center; gap:0.35rem; }
  .btn-add:hover { opacity:0.9; }
  .empty-state { text-align:center; padding:3rem 1.5rem; background:white; border-radius:12px; color:#7a7a8c; }
  .empty-state h2 { font-family:'Playfair Display',serif; color:#1a1a2e; margin-bottom:0.5rem; }

  /* Assistant panel */
  .asst-panel { background:white; border-radius:14px; box-shadow:0 3px 16px rgba(0,0,0,0.1); display:flex; flex-direction:column; height:640px; position:sticky; top:1rem; overflow:hidden; }
  .asst-header { padding:1rem 1.2rem; border-bottom:1px solid #f0ede8; display:flex; align-items:center; gap:0.6rem; }
  .asst-header h2 { font-family:'Playfair Display',serif; font-size:1rem; color:#1a1a2e; margin:0; flex:1; }
  .asst-dot { width:9px; height:9px; border-radius:50%; background:#10b981; flex-shrink:0; }
  .asst-alerts { padding:0.75rem 1rem 0; border-bottom:1px solid #f0ede8; max-height:200px; overflow-y:auto; }
  .asst-alerts:empty { display:none; padding:0; border:none; }
  .alert-card { background:#fffbeb; border:1.5px solid #fde68a; border-radius:10px; padding:0.75rem; margin-bottom:0.6rem; font-size:0.82rem; }
  .alert-card-title { font-weight:700; color:#92400e; margin-bottom:0.35rem; }
  .alert-card-meta { color:#78350f; margin-bottom:0.5rem; }
  .btn-review { background:#f59e0b; color:white; border:none; padding:0.35rem 0.8rem; border-radius:6px; font-size:0.75rem; font-weight:700; cursor:pointer; }
  .btn-review:hover { background:#d97706; }
  .asst-chat { flex:1; overflow-y:auto; padding:0.75rem 1rem; display:flex; flex-direction:column; gap:0.6rem; }
  .msg { max-width:88%; padding:0.6rem 0.85rem; border-radius:10px; font-size:0.85rem; line-height:1.5; word-break:break-word; }
  .msg-user { background:#1a1a2e; color:white; align-self:flex-end; border-radius:10px 10px 3px 10px; }
  .msg-ai { background:#f9f7f4; color:#1a1a2e; align-self:flex-start; border-radius:10px 10px 10px 3px; }
  .msg-ai strong { font-weight:700; }
  .msg-ai code { background:#e8e0d4; padding:0.1rem 0.3rem; border-radius:3px; font-size:0.82rem; }
  .email-draft { background:white; border:1.5px solid #d1c9bc; border-radius:10px; padding:0.85rem; margin-top:0.5rem; font-size:0.82rem; }
  .email-draft label { display:block; font-size:0.72rem; font-weight:700; text-transform:uppercase; color:#7a7a8c; margin-bottom:0.2rem; margin-top:0.5rem; }
  .email-draft input, .email-draft textarea { width:100%; border:1px solid #e8e0d4; border-radius:6px; padding:0.35rem 0.5rem; font-size:0.82rem; box-sizing:border-box; }
  .email-draft textarea { min-height:80px; resize:vertical; }
  .btn-send-email { background:#166534; color:white; border:none; padding:0.4rem 0.9rem; border-radius:6px; font-size:0.78rem; font-weight:700; cursor:pointer; margin-top:0.6rem; }
  .btn-send-email:hover { background:#14532d; }
  .send-status { font-size:0.78rem; margin-top:0.35rem; }
  .asst-input-row { padding:0.75rem 1rem; border-top:1px solid #f0ede8; display:flex; gap:0.5rem; }
  .asst-input { flex:1; border:1.5px solid #d1c9bc; border-radius:8px; padding:0.5rem 0.75rem; font-size:0.85rem; resize:none; height:38px; line-height:1.4; }
  .asst-input:focus { outline:none; border-color:#2e7d6e; }
  .btn-asst-send { background:#1a1a2e; color:white; border:none; padding:0.5rem 0.9rem; border-radius:8px; font-weight:700; font-size:0.82rem; cursor:pointer; white-space:nowrap; }
  .btn-asst-send:hover { opacity:0.85; }

  /* Add modal */
  .modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.55); z-index:900; align-items:center; justify-content:center; }
  .modal-overlay.open { display:flex; }
  .modal { background:white; border-radius:16px; padding:2rem; max-width:520px; width:90%; max-height:90vh; overflow-y:auto; }
  .modal h2 { font-family:'Playfair Display',serif; font-size:1.4rem; color:#1a1a2e; margin:0 0 1.25rem; }
  .form-row { margin-bottom:0.9rem; }
  .form-row label { display:block; font-size:0.82rem; font-weight:600; color:#444; margin-bottom:0.25rem; }
  .form-row input, .form-row select, .form-row textarea { width:100%; padding:0.5rem 0.7rem; border:1.5px solid #d1c9bc; border-radius:8px; font-size:0.88rem; box-sizing:border-box; }
  .form-row textarea { min-height:65px; resize:vertical; }
  .modal-actions { display:flex; gap:0.75rem; margin-top:1.25rem; justify-content:flex-end; }
  .btn-cancel { background:#f0ede8; color:#1a1a2e; border:none; padding:0.55rem 1.1rem; border-radius:8px; font-weight:600; cursor:pointer; }
  .btn-submit { background:#1a1a2e; color:white; border:none; padding:0.55rem 1.3rem; border-radius:8px; font-weight:700; cursor:pointer; }
  .color-row { display:flex; gap:0.75rem; align-items:center; }
  .color-row input[type=color] { width:44px; height:36px; padding:2px; border-radius:6px; border:1.5px solid #d1c9bc; cursor:pointer; }
</style>

<div class="ov-layout">
  <!-- ── LEFT: Client Grid ── -->
  <div>
    <div class="ov-header">
      <div>
        <h1>🛡️ Overseer Dashboard</h1>
        <p style="color:#7a7a8c;margin:0.3rem 0 0;font-size:0.9rem;">Manage all client stores.</p>
      </div>
      <button class="btn-add" onclick="document.getElementById('addModal').classList.add('open')">➕ Add New Client</button>
    </div>

    <div class="ov-stats">
      <div class="ov-stat">
        <div class="ov-stat-val">{{ stores|length }}</div>
        <div class="ov-stat-lbl">Total Clients</div>
      </div>
      <div class="ov-stat">
        <div class="ov-stat-val">{{ active_count }}</div>
        <div class="ov-stat-lbl">Active</div>
      </div>
      <div class="ov-stat">
        <div class="ov-stat-val">{{ suspended_count }}</div>
        <div class="ov-stat-lbl">Suspended</div>
      </div>
      <div class="ov-stat revenue">
        <div class="ov-stat-val">${{ total_revenue }}</div>
        <div class="ov-stat-lbl">MRR</div>
      </div>
    </div>

    {% if stores %}
    <div class="client-grid">
      {% for store in stores %}
      <div class="client-card">
        <div class="client-card-header">
          <div class="client-card-name">{{ store.store_name }}</div>
          <div class="client-card-meta">{{ store.contact_email }} &bull; {{ store.created_at[:10] if store.created_at else '—' }}</div>
        </div>
        <div class="client-card-body">
          <span class="badge badge-{{ store.status or 'active' }}">{{ (store.status or 'active')|title }}</span>
          <span class="badge badge-{{ store.plan or 'starter' }}">{{ (store.plan or 'starter')|title }}</span>
          {% if store.tagline %}<p style="font-size:0.78rem;color:#7a7a8c;margin:0.4rem 0 0;font-style:italic;">{{ store.tagline }}</p>{% endif %}
        </div>
        <div class="client-card-actions">
          <form method="POST" action="/overseer/client/{{ store.slug }}/impersonate" style="display:inline;">
            <button type="submit" class="btn-manage">🔀 Manage</button>
          </form>
          <a href="/overseer/client/{{ store.slug }}" class="btn-details">Details</a>
        </div>
      </div>
      {% endfor %}
    </div>
    {% else %}
    <div class="empty-state">
      <h2>🏪 No client stores yet</h2>
      <p>Click <strong>+ Add New Client</strong> to provision your first store.</p>
    </div>
    {% endif %}
  </div>

  <!-- ── RIGHT: Overseer Assistant ── -->
  <div class="asst-panel" id="asstPanel">
    <div class="asst-header">
      <div class="asst-dot" id="asstDot"></div>
      <h2>🤖 Overseer Assistant</h2>
    </div>

    <!-- Proactive alerts (populated on load) -->
    <div class="asst-alerts" id="asstAlerts"></div>

    <!-- Chat history -->
    <div class="asst-chat" id="asstChat">
      <div class="msg msg-ai">Hi Jay 👋 I'm watching your trials and clients. Ask me anything — or I'll surface alerts for you automatically.</div>
    </div>

    <!-- Input -->
    <div class="asst-input-row">
      <textarea class="asst-input" id="asstInput" placeholder="Ask anything — who's trial ends soon? Draft a follow-up for Mike..." rows="1"></textarea>
      <button class="btn-asst-send" id="asstSendBtn" onclick="sendMessage()">Send</button>
    </div>
  </div>
</div>

<!-- Add Client Modal -->
<div class="modal-overlay" id="addModal">
  <div class="modal">
    <h2>➕ Add New Client</h2>
    <form method="POST" action="/overseer/client/create">
      <div class="form-row"><label>Store Name *</label><input type="text" name="store_name" required placeholder="e.g. Treasure Trove Thrift"></div>
      <div class="form-row"><label>Contact Name</label><input type="text" name="contact_name" placeholder="Owner's full name"></div>
      <div class="form-row"><label>Contact Email (Login) *</label><input type="email" name="contact_email" required placeholder="client@email.com"></div>
      <div class="form-row"><label>Contact Phone</label><input type="text" name="contact_phone" placeholder="(555) 000-0000"></div>
      <div class="form-row"><label>Temporary Password *</label><input type="text" name="temp_password" required placeholder="Share this with the client"></div>
      <div class="form-row">
        <label>Plan</label>
        <select name="plan">
          <option value="starter">Starter — $299</option>
          <option value="pro">Pro — $499</option>
          <option value="enterprise">Enterprise — $799</option>
        </select>
      </div>
      <div class="form-row">
        <label>Industry</label>
        <select name="industry">
          <option value="thrift">Thrift Store</option>
          <option value="antique">Antique Shop</option>
          <option value="consignment">Consignment</option>
          <option value="electronics">Electronics</option>
          <option value="clothing">Clothing</option>
          <option value="general">General Retail</option>
        </select>
      </div>
      <div class="form-row">
        <label>Primary Color</label>
        <div class="color-row"><input type="color" name="primary_color" value="#2e7d6e"><span style="font-size:0.78rem;color:#7a7a8c;">Store branding color</span></div>
      </div>
      <div class="form-row"><label>Tagline (optional)</label><input type="text" name="tagline" placeholder="e.g. Quality finds at unbeatable prices"></div>
      <div class="form-row"><label>Internal Notes (optional)</label><textarea name="notes" placeholder="Notes only visible to you..."></textarea></div>
      <div class="modal-actions">
        <button type="button" class="btn-cancel" onclick="document.getElementById('addModal').classList.remove('open')">Cancel</button>
        <button type="submit" class="btn-submit">🚀 Provision Store</button>
      </div>
    </form>
  </div>
</div>

<script>
// ── Proactive alerts ──────────────────────────────────────────────────────────
function loadAlerts() {
  fetch('/overseer/assistant/alerts', { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      const container = document.getElementById('asstAlerts');
      if (!data.alerts || data.alerts.length === 0) return;
      data.alerts.forEach(alert => {
        const card = document.createElement('div');
        card.className = 'alert-card';
        card.innerHTML = `
          <div class="alert-card-title">⏰ ${escHtml(alert.store_name)} — trial ends ${escHtml(alert.days_label)}</div>
          <div class="alert-card-meta">${escHtml(alert.contact_email)}</div>
          <button class="btn-review" onclick="openDraft(${JSON.stringify(alert).replace(/"/g,'&quot;')})">📧 Review &amp; Send Draft</button>
        `;
        container.appendChild(card);
      });
    })
    .catch(() => {});
}

function openDraft(alert) {
  appendEmailDraft('ai', 'Trial ending soon — here\'s a draft:', alert.draft_to, alert.draft_subject, alert.draft_body);
}

// ── Chat ──────────────────────────────────────────────────────────────────────
function sendMessage() {
  const input = document.getElementById('asstInput');
  const msg   = input.value.trim();
  if (!msg) return;
  input.value = '';
  appendMsg('user', msg);
  const thinking = appendMsg('ai', '...');
  document.getElementById('asstSendBtn').disabled = true;

  fetch('/overseer/assistant/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: msg })
  })
  .then(r => r.json())
  .then(data => {
    thinking.remove();
    document.getElementById('asstSendBtn').disabled = false;
    if (data.type === 'email') {
      appendEmailDraft('ai', data.reply, data.to, data.subject, data.body);
    } else {
      appendMsg('ai', data.reply || 'No response.');
    }
  })
  .catch(e => {
    thinking.remove();
    document.getElementById('asstSendBtn').disabled = false;
    appendMsg('ai', 'Error: ' + e);
  });
}

function appendMsg(role, text) {
  const chat = document.getElementById('asstChat');
  const div  = document.createElement('div');
  div.className = 'msg msg-' + role;
  div.innerHTML = markdownLite(escHtml(text));
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

function appendEmailDraft(role, intro, to, subject, body) {
  const chat = document.getElementById('asstChat');
  const id   = 'draft-' + Date.now();
  const div  = document.createElement('div');
  div.className = 'msg msg-' + role;
  div.innerHTML = `
    <div>${escHtml(intro)}</div>
    <div class="email-draft" id="${id}">
      <label>To</label>
      <input type="email" class="draft-to" value="${escHtml(to)}">
      <label>Subject</label>
      <input type="text" class="draft-subject" value="${escHtml(subject)}">
      <label>Body</label>
      <textarea class="draft-body">${escHtml(body)}</textarea>
      <button class="btn-send-email" onclick="sendDraft('${id}', this)">📨 Send Email</button>
      <div class="send-status"></div>
    </div>
  `;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function sendDraft(draftId, btn) {
  const card    = document.getElementById(draftId);
  const to      = card.querySelector('.draft-to').value.trim();
  const subject = card.querySelector('.draft-subject').value.trim();
  const body    = card.querySelector('.draft-body').value.trim();
  const status  = card.querySelector('.send-status');
  btn.disabled  = true;
  btn.textContent = 'Sending...';

  fetch('/overseer/assistant/send-email', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ to, subject, body })
  })
  .then(r => r.json())
  .then(data => {
    if (data.success) {
      status.innerHTML = `<span style="color:#166534;">✅ ${escHtml(data.message)}</span>`;
      btn.textContent  = '✅ Sent';
    } else {
      status.innerHTML = `<span style="color:#991b1b;">❌ ${escHtml(data.error)}</span>`;
      btn.disabled     = false;
      btn.textContent  = '📨 Send Email';
    }
  })
  .catch(e => {
    status.innerHTML = `<span style="color:#991b1b;">❌ ${e}</span>`;
    btn.disabled     = false;
    btn.textContent  = '📨 Send Email';
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function markdownLite(s) {
  return s
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.+?)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>');
}

// ── Input auto-grow + enter to send ──────────────────────────────────────────
const asstInput = document.getElementById('asstInput');
asstInput.addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 100) + 'px';
});
asstInput.addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Modal click-outside close
document.getElementById('addModal').addEventListener('click', function(e) {
  if (e.target === this) this.classList.remove('open');
});

// Load alerts on page load
loadAlerts();
</script>
{% endblock %}
```

- [ ] **Step 2: Verify the app starts without errors**

```bash
python3 -c "import ast; ast.parse(open('app_with_ai.py').read()); print('Syntax OK')"
```
Expected: `Syntax OK`

- [ ] **Step 3: Commit**

```bash
git add templates/overseer_dashboard.html
git commit -m "feat: two-column overseer dashboard with embedded AI assistant panel"
```

---

## Task 6: Push and verify

- [ ] **Step 1: Push to GitHub**

```bash
git push
```

- [ ] **Step 2: Manual smoke test checklist**

1. Log in as admin → navigate to `/overseer`
2. Confirm two-column layout renders (assistant panel on right)
3. Confirm "Hi Jay 👋" greeting appears in chat
4. Type "how many clients do I have?" — confirm AI responds
5. Type "draft a follow-up for [a trial client email]" — confirm email draft card appears with editable fields
6. Go to `/admin/settings` — confirm SMTP section appears at bottom
7. Enter Gmail SMTP credentials, click Save — confirm flash success
8. Click Send Test Email — confirm it arrives in Jay's inbox
9. Back on `/overseer` — if any trial expires within 3 days, confirm alert card appears automatically

- [ ] **Step 3: Final commit if any fixes needed**

```bash
git add -p
git commit -m "fix: overseer assistant smoke test fixes"
git push
```

---

## Self-Review

**Spec coverage:**
- ✅ Proactive trial alerts (Task 4 + Task 5 JS `loadAlerts`)
- ✅ Chat panel (Task 5 HTML/JS)
- ✅ Email draft cards with editable fields + Send button (Task 5 `appendEmailDraft`)
- ✅ Send via SMTP (Task 1 `send_smtp_email`, Task 4 route)
- ✅ AI actions: suspend, unsuspend, reset_password (Task 3 chat route)
- ✅ Business context snapshot (Task 3 `build_assistant_context`)
- ✅ SMTP settings in admin (Task 2)
- ✅ Two-column layout, mobile stacks (Task 5 CSS)
- ✅ No auto-send — Jay always clicks Send

**Placeholder scan:** None found.

**Type consistency:** `send_smtp_email(to, subject, body)` defined in Task 1, called identically in Task 4. `build_assistant_context()` defined in Task 3, called in Task 3 chat route. `get_smtp_config()` defined in Task 1, called in Task 2 template render and Task 1 `send_smtp_email`. All consistent.
