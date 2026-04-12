# Security Hardening Report - Akces Hub

**Date:** 2026-04-12
**Scope:** Comprehensive security hardening of the Flask application

---

## Summary of Vulnerabilities Found & Fixed

### 1. Rate Limiting (HIGH) - FIXED
**Issue:** Login endpoint and API write endpoints had no per-route rate limiting. While a global 200/min limit existed via flask_limiter, sensitive endpoints like login were not individually protected against brute-force attacks.

**Fix:**
- Login endpoint: 5 requests/minute per IP
- First setup endpoint: 3 requests/minute per IP
- API write endpoints (backup create/restore, ngrok control, notify): 30 requests/minute (shared)
- Webhook endpoints (Telegram, Allegro): 60 requests/minute (shared)
- Global rate limit (200/min) verified working via flask_limiter

**Files:** `app.py` (rate limit decorators applied after blueprint registration)

---

### 2. Session Key Inconsistency (HIGH) - FIXED
**Issue:** Critical authentication bypass on external (ngrok) connections. The `block_unauthenticated_external` middleware in `app.py` checked `session.get('user')`, but the auth module (`modules/auth.py`) sets `session['user_id']` and `session['username']` on login -- never `session['user']`. This meant:
- The external access blocker NEVER recognized logged-in users (session['user'] was always None)
- The CSRF protection was NEVER enforced (same check at line 309)
- Template `current_user` was always None in 20+ files across 12 modules

**Fix:**
- `app.py` line 278: `session.get('user')` -> `session.get('user_id')` (auth check)
- `app.py` line 309: `session.get('user')` -> `session.get('user_id')` (CSRF enforcement)
- 21 occurrences of `current_user=session.get('user')` -> `session.get('username')` across 12 module files (analityka, allegro_api, paletomat, palety, analytics, serwisant, magazynier, sprzedaze, telegram_bot, ustawienia, wysylki, warehouse)

**Files:** `app.py`, `modules/analityka.py`, `modules/allegro_api.py`, `modules/paletomat.py`, `modules/palety.py`, `modules/analytics.py`, `modules/serwisant.py`, `modules/magazynier.py`, `modules/sprzedaze.py`, `modules/telegram_bot.py`, `modules/ustawienia.py`, `modules/wysylki.py`, `modules/warehouse.py`

---

### 3. Webhook Signature Validation (MEDIUM) - FIXED
**Issue:** Telegram and Allegro webhook endpoints accepted POST requests without any signature verification. Any attacker knowing the webhook URL could inject fake webhook payloads.

**Fix:**
- Added `validate_webhook_signatures()` before_request handler
- Telegram: Validates `X-Telegram-Bot-Api-Secret-Token` header using HMAC-SHA256 of the bot token
- Allegro: Validates `X-Allegro-Webhook-Secret` header against stored shared secret
- Uses `hmac.compare_digest()` for constant-time comparison (prevents timing attacks)
- Graceful degradation: if validation config is missing, webhooks still work (backwards compatible)

**Files:** `app.py` (new before_request handler)

---

### 4. Encrypted Backups (MEDIUM) - FIXED
**Issue:** Database backups were stored as plaintext .db files. If the backup directory or cloud sync was compromised, all data would be exposed.

**Fix:**
- After creating and verifying a backup, it is now encrypted using Fernet (AES-128-CBC)
- Encryption key reuses the existing `_get_fernet()` from `modules/database.py`
- Encrypted backups saved as `.db.enc` files; unencrypted originals are deleted
- Restore function auto-detects `.enc` files and decrypts to a temp file before restoring
- Temp decrypted files are cleaned up in a `finally` block
- Backup listing and rotation updated to handle both `.db` and `.db.enc` files
- Graceful fallback: if cryptography library unavailable, saves unencrypted (with warning)

**Files:** `modules/backup_manager.py`

---

### 5. Error Handlers Enhanced (MEDIUM) - FIXED
**Issue:** Existing 500/404 error handlers returned bare HTML strings without proper styling. The 500 handler correctly logged server-side details but the client response was minimal.

**Fix:**
- 500 handler: Styled error page matching app theme (dark mode), uses `safe_error_message()`, separate JSON response for AJAX
- 404 handler: Styled error page with navigation back to dashboard, JSON response for AJAX
- 403 handler: New handler added for forbidden access attempts (logged with IP)
- `debug=False` verified in `app.run()` (already correct)
- All handlers log full details server-side, return only generic messages to clients

**Files:** `app.py`

---

### 6. Security Headers (LOW) - VERIFIED & ENHANCED
**Issue:** Headers were already comprehensive. Minor enhancement opportunity.

**Current headers (all present):**
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: SAMEORIGIN`
- `X-XSS-Protection: 1; mode=block`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy` (full policy with frame-ancestors, base-uri, form-action)
- `Permissions-Policy` (enhanced)

**Enhancement:**
- Added `payment=()` and `usb=()` to Permissions-Policy

**Files:** `app.py`

---

## Previously Fixed (Not in This Pass)

These were already addressed in a prior commit:
- CORS restricted to exact localhost:5000 origin
- SESSION_COOKIE_SECURE auto-enabled on production
- `sanitize_csv_cell()` and `sanitize_html()` in modules/utils.py
- Legacy SHA-256 password hashes rejected (require reset)
- `.env.key` file permissions auto-fixed
- `safe_error_message()` added to modules/utils.py
- Server version header hidden (werkzeug.serving)
- Secret key generated and stored in `.secret_key` file (not hardcoded)
- CSRFProtect enabled with Flask-WTF
- Session cookie: HttpOnly, SameSite=Lax, 12h lifetime

---

## Remaining Risks

### Low Priority
1. **CSP uses unsafe-inline for scripts** - Required by Chart.js and inline templates. Migrating to nonce-only CSP requires refactoring all inline `<script>` tags across 14K+ lines of templates.
2. **Auto-login on LAN** - Configurable feature (`auto_login_lan`), disabled by default. When enabled, any device on the local network gets admin access. Document this risk for users.
3. **SQLite concurrency** - WAL mode helps, but heavy concurrent writes can still cause SQLITE_BUSY. Connection pooling and busy_timeout=30000 mitigate this.
4. **No HTTPS enforcement at app level** - HSTS header is set, but the app itself runs HTTP. Relies on nginx/reverse proxy for TLS termination.

### Informational
5. **Rate limiting in-memory** - Uses `memory://` storage, resets on app restart. For persistent rate limiting across restarts, consider Redis backend.
6. **Webhook secrets must be configured** - Telegram/Allegro webhook validation only works if the corresponding secrets are stored in config. Without them, validation is skipped (backwards compatible).

---

## Post-Deploy Verification Checklist

- [ ] **Rate limiting test:** Try logging in 6 times in 1 minute with wrong password. 6th attempt should return 429 Too Many Requests.
- [ ] **Session test:** Log in via ngrok (external), verify dashboard loads correctly. Previously would fail due to session['user'] bug.
- [ ] **CSRF test:** Submit a form via external connection, verify CSRF token is now validated for logged-in users.
- [ ] **Backup encryption test:** Trigger a manual backup via `/api/backup/create`. Verify the file in `backups/` has `.db.enc` extension. Verify restore works from the encrypted backup.
- [ ] **Error pages test:** Navigate to a non-existent URL, verify styled 404 page appears. Force a 500 error, verify no internal details leak.
- [ ] **Security headers test:** Use browser DevTools Network tab to inspect response headers on any page. Verify all 7 headers are present.
- [ ] **Webhook validation test:** If Telegram bot is configured, set the webhook secret and verify messages still arrive. Send a forged POST to the webhook URL without the header and verify 403.
- [ ] **Template current_user test:** Verify the username displays correctly in the navigation bar on all pages (was previously showing blank/None).
- [ ] **debug=False check:** Verify `app.run(debug=False)` in production. Intentionally cause an error and confirm no debugger/traceback is shown to the client.
