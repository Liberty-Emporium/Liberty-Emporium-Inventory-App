# Overseer Assistant — Design Spec

**Date:** 2026-04-06
**Author:** Jay Alexander
**Status:** Approved

---

## Goal

Build an AI-powered business assistant embedded in the Overseer Dashboard. The assistant handles the business side of running RetailTrack: it monitors 14-day trials, drafts payment follow-up emails for Jay to review and send, answers questions about clients and revenue, and can execute management actions (suspend, unsuspend, reset password) on Jay's behalf via chat.

---

## Architecture Overview

The Overseer Assistant has two modes that work together inside a chat panel on the right side of `/overseer`:

**Proactive mode** — on dashboard load, the backend scans all client stores for trials expiring within 3 days. For each, it pre-generates a draft payment follow-up email using Claude and surfaces it as an alert card at the top of the chat panel. Jay sees these immediately without having to ask.

**Reactive mode** — Jay types any message into the chat input. The message is POSTed to `/overseer/assistant/chat`. The backend builds a system prompt containing a full live snapshot of all client data, sends it to Claude Haiku (same `urllib` API pattern already in the app), and streams the response back into the chat panel.

**Email sending** — when the AI drafts an email, it returns a structured block with `to`, `subject`, and `body`. The UI renders this as an editable draft card with a **Send** button. Clicking it POSTs to `/overseer/assistant/send-email`, which delivers via `smtplib` using Jay's SMTP credentials.

**Actions** — the AI can trigger backend actions by returning JSON: `{"action": "suspend", "slug": "store-slug"}`. Supported actions: `suspend`, `unsuspend`, `reset_password`. Deletion is intentionally excluded — it requires manual confirmation in the UI.

---

## UI Layout

The overseer dashboard becomes a two-column layout:

**Left column** — existing client grid (stats bar, + Add New Client, client cards). Narrowed to fit.

**Right column (~380px)** — Overseer Assistant panel:
- **Header**: "🤖 Overseer Assistant" with green status dot
- **Alerts section**: auto-generated expiring trial cards at top, each showing store name, days remaining, and a pre-written draft email with **Review & Send** button
- **Chat history**: scrollable thread. Jay's messages right-aligned (dark). AI responses left-aligned (light). Email draft responses appear as a distinct card inside the AI message with editable `To`, `Subject`, `Body` fields and a **Send** button
- **Input bar**: text input + Send button. Placeholder: *"Ask anything — who's trial ends soon? Draft a follow-up for Mike..."*

On mobile the assistant panel stacks below the client grid.

---

## AI Context (System Prompt)

Every chat request includes a live business snapshot built from the client store files:

```
You are the Overseer Assistant for RetailTrack, a SaaS inventory management app run by Jay Alexander.

Today's date: {date}

CLIENTS:
{for each store: name, slug, plan, status, created_at, trial_end, days_remaining}

BUSINESS SUMMARY:
- Total clients: {n}
- Active: {n}  Suspended: {n}  In trial: {n}
- Monthly recurring revenue: ${active_count * 20}
- All-time setup fees: ${total_clients * 99}
- Trials expiring within 7 days: {list}
- Trials past end date (unpaid): {list}

You can help Jay by:
- Answering questions about clients and revenue
- Drafting follow-up emails (return as JSON: {"type":"email","to":"...","subject":"...","body":"..."})
- Triggering actions (return as JSON: {"action":"suspend"|"unsuspend"|"reset_password","slug":"..."})

Always be concise. When drafting emails, be warm and professional — Jay is a real person, not a corporation.
```

---

## Capabilities

| Jay says | AI does |
|---|---|
| "Who's trial ends this week?" | Lists clients with expiry dates |
| "Draft a follow-up for Sarah's Boutique" | Returns editable email draft card |
| "How much am I making this month?" | Calculates from active client count |
| "Suspend Mike's store" | Returns action JSON → backend suspends → confirms in chat |
| "Reset password for client X" | Returns action JSON → backend resets → shows temp password in chat |
| "Which clients haven't paid yet?" | Lists trial clients past their end date |
| "How many clients do I have?" | Answers from snapshot |

---

## Routes

| Method | Path | Description |
|---|---|---|
| GET | `/overseer` | Updated — loads assistant panel + proactive trial alerts |
| POST | `/overseer/assistant/chat` | Accepts `{message}`, returns `{reply, type, action?, email?}` |
| POST | `/overseer/assistant/send-email` | Accepts `{to, subject, body}`, sends via SMTP, returns `{success}` |
| POST | `/overseer/assistant/alerts` | Returns pre-generated alert cards for expiring trials |

---

## SMTP Email Setup

New "Email (SMTP)" section in `/admin/settings`:

**Fields stored in `app_config.json`:**
- `smtp_host` — e.g. `smtp.gmail.com`
- `smtp_port` — 465 (SSL) or 587 (TLS)
- `smtp_user` — sender email address
- `smtp_password` — password or app password

**Send Test Email** button on settings page confirms credentials work.

**Send flow:**
1. Jay clicks **Send** on a draft card
2. POST to `/overseer/assistant/send-email`
3. Backend sends via `smtplib.SMTP_SSL` (port 465) or `smtplib.SMTP` + `starttls` (port 587)
4. Chat panel shows ✅ "Sent to client@email.com" or ❌ error inline

---

## Proactive Trial Alert Format

The AI drafts alerts for trials expiring within 3 days. Default email template:

> **Subject:** Your RetailTrack trial ends in {N} days — next steps
>
> Hi {contact_name or "there"},
>
> Just a quick note — your 14-day free trial for **{store_name}** ends on **{trial_end}**.
>
> To keep your store running, the one-time setup fee is **$99** and then just **$20/month** after that. I'll send you a payment link when you're ready.
>
> Feel free to reply to this email with any questions.
>
> — Jay
> Liberty Emporium Programs

Jay can edit any field before sending.

---

## New Files

- `templates/overseer_dashboard.html` — updated with two-column layout + assistant panel
- `templates/admin_settings.html` — updated with SMTP section

**No new files needed** — all assistant logic lives in `app_with_ai.py` as new routes.

---

## What Is NOT in Scope

- Automatic sending without Jay's review — Jay always clicks Send
- Scheduling emails (send at 9am tomorrow) — manual only
- AI initiating actions without Jay asking — proactive mode only surfaces drafts, never auto-sends
- Client-facing AI — this is overseer-only
- SMS or any channel other than email
