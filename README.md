# slotsniper

Watches annoying campus portals (laundry machines, gym slots, class registration) in
headless Chrome/Edge. The moment a slot opens it pings Telegram/Discord and, optionally,
books it.

```
page load ─► session check ─► extract slots ─► diff vs last poll ─► notify / book ─► jittered sleep
                 │                 │
                 │                 ├─ DOM: role/text locator chains (survive class-name churn)
                 │                 └─ VLM: screenshot → Claude → slots + click coordinates
                 └─ expired? alert once, ask human to re-login
```

## What makes it robust

| Problem | How it's handled |
|---|---|
| SPA renders late | `first_matching` polls locator chains until something appears |
| Minified/random CSS classes | Locator specs prefer ARIA roles and visible text (`role:button:(?i)reserve`) over CSS; each field is a fallback chain |
| Portal redesign breaks selectors | If every DOM spec matches nothing, it warns once and falls back to a vision model that reads the screenshot |
| List re-renders between read and click | Booking re-finds the slot by label, not index, then checks that the button is enabled |
| SSO / Duo / 2FA | `login` opens a real window, a human logs in, and the persistent browser profile keeps the cookies. Passwords are never stored |
| Session expiry | URL/text heuristics catch it. You get one alert, not one per poll |
| Flaky network / portal 500s | Exponential backoff (up to 16×), with an alert after 3 failures in a row |
| Alert spam | Alerts fire on *transitions* (closed→open), with one-shot warnings |
| Accidental bookings | `dry_run: true` by default, plus a `max_bookings` cap |

## Quick start

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
python mock_portal/server.py            # terminal 1: a fake laundry portal
python -m slotsniper probe              # read-only: list what it sees
python -m slotsniper watch              # poll, alert, book
```

Run `python mock_portal/server.py --require-login` to test session handling.

On a real portal, run `python -m slotsniper login -t <name>` once, then `watch`.

### Notifications
Set env vars (never put them in the YAML):

- **Telegram**: create a bot with @BotFather and set `TELEGRAM_BOT_TOKEN`. Message the bot,
  then read `https://api.telegram.org/bot<token>/getUpdates` and set `TELEGRAM_CHAT_ID`.
- **Discord**: Channel → Integrations → Webhooks, then set `DISCORD_WEBHOOK_URL`.

Check them with `python -m slotsniper test-notify`. Alerts include a screenshot.

### Vision fallback
Set `ANTHROPIC_API_KEY`. The model is `claude-opus-5-5` at `effort: low`. It uses structured JSON output
and the server-side refusal fallback (`fallbacks: "default"`). Set `extractor.kind: vlm` to always use
vision, e.g. for canvas UIs or camera-feed dashboards.

## Locator specs
`css:SEL` · `text:REGEX` · `role:ROLE[:NAME_REGEX]` · `label:REGEX` · `testid:ID` ·
fields also accept `attr:NAME` and `text`. A leading `(?i)` makes a regex case-insensitive.

## Be a good citizen
- Keep `every_seconds` ≥ 30 on real portals. `min_seconds` is a hard floor. Jitter spreads load.
- This tool does **not** bypass CAPTCHAs or bot detection. If a portal shows one, it's
  telling you to slow down.
- Check your school's acceptable-use policy. Automated booking may break the rules for
  class registration even where alerts are fine. Notify-only mode is always the safe default.

## Layout
```
slotsniper/
  config.py    pydantic schema for config.yaml
  locators.py  spec strings → Playwright locators, fallback chains
  extract.py   DOM extractor + Claude vision extractor → Slot
  book.py      click + confirmation check
  state.py     per-target open/booked memory (JSON)
  notify.py    console / Telegram / Discord
  watcher.py   poll loop, session checks, backoff
  __main__.py  CLI: watch | once | probe | login | test-notify
mock_portal/server.py   a deliberately annoying fake portal for offline testing
```
