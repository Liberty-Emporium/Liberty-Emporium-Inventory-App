# AI-Powered Ad Copy Generation — Design Spec
**Date:** 2026-04-05  
**Status:** Approved

## Overview

Replace the manual Store Name + CTA text inputs in the Picture Ad Studio with fully automatic AI-generated copy. When the user clicks Generate, Claude writes a headline, selling line, price callout, and tagline for each selected product. Pillow renders the ad image using that copy. Results stream in as each ad finishes — no waiting for all ads before seeing the first one.

## What Changes

### Removed
- "Store Name" text input
- "Call-to-Action line" text input

### Added
- "AI-Powered Copy — ON" info badge in the config panel (no interaction needed, just communicates the feature is active)
- Generate button label updated to "✨ Generate Ads with AI"
- Streaming results — ads appear one at a time as they finish rather than all at once

### Unchanged
- Style chips (Elegant, Vivid, Forest, Ocean, Warm) — user still picks style
- Format chips (Square, Portrait, Story)
- Product selection grid
- Pillow rendering pipeline (colors, photo crop, layout)
- `/generate-ads` endpoint URL

## Ad Copy Fields

Claude generates 4 text fields per product, replacing the existing raw fields:

| Field | Replaces | Example | Max length |
|---|---|---|---|
| `headline` | Product title on the ad | "Holiday Magic Awaits" | 6 words |
| `selling_line` | Description line | "A charming vintage collectible" | 10 words |
| `price_callout` | Price string | "Just $24!" | 5 words |
| `tagline` | Footer/CTA line | "Find it at Liberty Emporium" | 8 words |

## Backend Architecture

### Endpoint: `/generate-ads` (POST)
- Accepts same JSON payload as today (products, style, format)
- Switches response type to **Server-Sent Events (SSE)** — `text/event-stream`
- Spawns one thread per product (max 10, same as current selection limit)
- Each thread: calls Claude → renders image → emits SSE event with result
- Final SSE event signals completion

### Claude API Call (per product)
- Model: `claude-haiku-4-5-20251001` (same model used elsewhere in the app)
- One call per product, all calls fire in parallel via threads
- Prompt sends: title, price, category, condition, description
- Response format: JSON with keys `headline`, `selling_line`, `price_callout`, `tagline`
- Fallback: if Claude call fails or returns malformed JSON, use raw product fields (title, description, `$price`, store address) so the ad still renders

### Prompt
```
You write ad copy for a thrift and antique store called Liberty Emporium.
Given this product, return ONLY a JSON object with these exact keys:
- headline: punchy ad headline, max 6 words
- selling_line: one benefit or descriptor, max 10 words  
- price_callout: price with excitement, max 5 words (e.g. "Just $24!")
- tagline: short footer line, max 8 words

Product:
Title: {title}
Category: {category}
Condition: {condition}
Price: ${price}
Description: {description}
```

## Frontend Changes

### Config Panel
- Remove both `<input>` fields (store name, CTA)
- Add AI copy badge:
  ```html
  <div class="ai-copy-badge">
    ✨ AI-Powered Copy — Claude will write headline, selling line, price callout, and tagline for each product.
  </div>
  ```

### Generate Flow
- Button label: "✨ Generate Ads with AI"
- On click: open an `EventSource` to `/generate-ads` (SSE) instead of a plain `fetch`
- As each `data:` event arrives, call `appendResult(data)` to add the ad card to the grid immediately
- Loading overlay still shows but detail text updates per-product: "Writing copy for Santa Figure…"
- On final event, hide overlay

### JavaScript changes
- Replace `fetch('/generate-ads', ...)` with `EventSource` approach
- `showResults()` replaced by `appendResult()` — adds one card at a time
- Results section becomes visible on first result, not after all complete

## Error Handling
- If Claude API key is not set: return a clear JSON error before starting any threads — "AI copy requires an Anthropic API key. Set ANTHROPIC_API_KEY in your environment."
- If Claude returns invalid JSON for one product: log the error, fall back to raw product fields, continue rendering. The ad still generates.
- If Claude call times out (>15s): same fallback as invalid JSON.
- If all products fail Claude: all ads still render with raw fields, no error shown to user.

## Testing
- Manually generate ads for 1, 3, and 10 products and verify:
  - Copy is appropriate for the product
  - Ads stream in one at a time
  - Download works on each streamed result
- Test with no API key set — verify clear error message
- Test fallback: temporarily break the Claude prompt and verify ads still render with raw fields
