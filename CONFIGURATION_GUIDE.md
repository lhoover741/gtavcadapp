# Configuration Guide - NThaCityRP

## Setup Instructions

Your `.replit` file has been updated. Now you need to fill in the placeholder values with your actual credentials.

### 1. **SMTP Password** (For Email Notifications)
Replace in `.replit`:
```ini
SMTP_PASSWORD = "your_email_password_here"
```
With your actual email password or app-specific password for `noreply@nthatcityrp.com`.

**Why:** Applications and complaints will send email notifications to admins.
**If skipped:** Emails won't send (logged as warning, app still works).

---

### 2. **Discord Webhook URL** (For Discord Notifications)
Replace in `.replit`:
```ini
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1234567890/abcdefghijklmnop"
```

**How to get it:**
1. Go to your Discord server
2. Settings → Integrations → Webhooks
3. Create New Webhook or use existing
4. Copy the full URL

**Why:** Applications and complaints post to Discord for instant admin notification.
**If skipped:** Discord posts won't work (gracefully degraded, app still works).

---

### 3. **OpenAI API Key** (Optional - For AI Features)
Replace in `.replit`:
```ini
OPENAI_API_KEY = "sk-your-openai-api-key-here"
```

**How to get it:**
1. Go to https://platform.openai.com/api-keys
2. Create an API key
3. Paste it here

**Why:** Powers AI features:
- Police report generation with AI narrative
- 911 dispatch triage automation
- Warrant justification generation
- Suspect matching algorithm

**If skipped:** AI endpoints return 503 Service Unavailable. Core app functionality (forms, registration) still works.

---

## What's Fixed

✅ **Deployment Config** - Changed from `static` to `dynamic` to allow Python server  
✅ **Environment Variables** - Added missing SMTP_PASSWORD and OPENAI_API_KEY placeholders  
✅ **Discord Webhook** - Replaced placeholder with guidance  

---

## API Endpoints Status

### ✅ Core Features (No Setup Needed)
- `POST /api/application` - Submit applications
- `POST /api/complaint` - Submit complaints
- `GET /api/admin/session` - Check admin login
- `POST /api/admin/login` - Admin authentication
- `GET /api/server-status` - View server status

### ⚠️ Requires Configuration
- `POST /api/complaint` - Email notifications (needs SMTP_PASSWORD)
- Email notifications for applications (needs SMTP_PASSWORD)
- Discord notifications (needs DISCORD_WEBHOOK_URL)

### 🔧 Requires OpenAI Key
- `POST /api/ai/police-report` - AI report generation
- `POST /api/ai/dispatch` - Dispatch triage
- `POST /api/ai/warrant` - Warrant generation
- `POST /api/ai/suspect-match` - Suspect matching

---

## Testing Your Setup

1. **Start the app** - Click "Project" in Replit or run `python3 server.py`
2. **Test application form** - Go to `/applications.html`, submit a form
3. **Check logs** - Look for success/error messages in the terminal
4. **Test admin features** - Go to `/admin.html` (if it exists), test login

---

## Troubleshooting

**Emails not sending?**
- Check SMTP_PASSWORD is correct
- Verify NOTIFY_EMAIL exists
- Look for error logs in terminal

**Discord not posting?**
- Verify DISCORD_WEBHOOK_URL is correct (copy full URL, not just ID)
- Check webhook permissions in Discord
- Test by submitting a form and checking Discord

**AI not working?**
- Verify OPENAI_API_KEY starts with `sk-`
- Check API key has billing enabled
- Look for HTTP 401/402 errors in logs

