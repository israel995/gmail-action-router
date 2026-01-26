# Gmail Action Router 📧 → 💬

**Stop checking Gmail. Let your emails come to you in Slack and WhatsApp.**

## What It Does

This script automatically:
- 🔍 Scans your Gmail for unread messages
- 🎯 Filters out noise and prioritizes what matters
- 🤖 Determines what action each email needs
- 📱 Sends actionable items to Slack (with pretty formatting)
- 🚨 Sends urgent items to WhatsApp

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run setup helper
python setup_helper.py

# 3. Get Gmail credentials
# - Go to https://console.cloud.google.com/
# - Enable Gmail API
# - Download credentials.json

# 4. Run the script
python gmail_action_router.py
```

## Features

✅ **Smart Prioritization**
- Urgent: "asap", "critical", "emergency"
- High: "important", "today", important senders
- Medium: "follow up", "meeting"
- Low: FYI, newsletters

✅ **Action Detection**
- Reply Required
- Review Required  
- Schedule Meeting
- Sign Document
- Read and Decide

✅ **Multi-Platform**
- Rich Slack messages with buttons
- WhatsApp alerts for urgent items
- Customizable routing rules

## File Structure

```
gmail-action-router/
├── gmail_action_router.py  # Main script
├── setup_helper.py         # Interactive setup
├── requirements.txt        # Dependencies
├── SETUP_GUIDE.md         # Detailed instructions
├── .env.example           # Configuration template
├── .gitignore             # Protect credentials
└── credentials.json       # Gmail OAuth (you provide)
```

## Configuration

### Required
- Gmail credentials (`credentials.json`)

### Optional  
- Slack bot token (for Slack integration)
- Twilio credentials (for WhatsApp integration)

### Customize
Edit `gmail_action_router.py` to add:
- Important senders (auto high-priority)
- Skip senders (auto ignore)
- Custom keywords for priority/action detection

## Usage

**One-time run:**
```bash
python gmail_action_router.py
```

**Scheduled (every hour):**
```bash
# Linux/Mac (crontab)
0 * * * * cd /path/to/script && python3 gmail_action_router.py

# Or use Task Scheduler on Windows
```

## Examples

**Slack Output:**
```
🔴 URGENT
Subject: Client deadline moved to tomorrow!
From: client@company.com
Action: Reply Required
[Open in Gmail] button
```

**WhatsApp Output:**
```
🔴 URGENT EMAIL

From: boss@company.com
Subject: Need approval ASAP
Action: Review Required

Preview: Hi, we need your sign-off on...
```

## Cost

- Gmail API: **Free**
- Slack: **Free**  
- Twilio WhatsApp: **~$0.005/message** (keep urgent-only!)

## Security

✅ OAuth authentication (no passwords stored)  
✅ Environment variables for secrets  
✅ Included .gitignore for credentials  
✅ Read-only Gmail access (can be modified if needed)

## Troubleshooting

**"No credentials.json found"**
→ Download from Google Cloud Console

**"Slack messages not sending"**  
→ Check bot token and invite bot to channel

**"WhatsApp not working"**
→ Complete Twilio sandbox setup first

**"No emails found"**
→ Check time range (default: 24 hours)

## Advanced

- Schedule daily summaries instead of real-time
- Add more platforms (Discord, Teams, Telegram)
- Auto-label emails in Gmail
- Create tasks in project management tools
- Machine learning for better filtering

## Documentation

📖 **Full setup guide**: `SETUP_GUIDE.md`  
🔧 **Interactive setup**: `python setup_helper.py`  
💡 **Configuration**: `.env.example`

## Support

- Gmail API: https://developers.google.com/gmail/api
- Slack API: https://api.slack.com/
- Twilio: https://www.twilio.com/docs/whatsapp

## License

Free to use for personal or commercial projects!

---

**Made with ❤️ for people drowning in email**
