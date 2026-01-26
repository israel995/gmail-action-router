# Gmail Action Router - Setup Guide

## Overview
This script connects to your Gmail, intelligently filters emails, determines what actions are needed, and routes them to Slack and WhatsApp so you can work from one system.

## Features
- ✅ Connects to Gmail via OAuth (secure, no password needed)
- ✅ Intelligently categorizes emails by priority (Urgent, High, Medium, Low)
- ✅ Determines what action is required (Reply, Review, Schedule, etc.)
- ✅ Sends actionable emails to Slack with rich formatting
- ✅ Sends urgent emails to WhatsApp for immediate attention
- ✅ Customizable filters for important/ignored senders
- ✅ Keyword-based priority detection

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Up Gmail API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Enable the Gmail API:
   - Go to "APIs & Services" > "Library"
   - Search for "Gmail API"
   - Click "Enable"
4. Create OAuth credentials:
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - Choose "Desktop app"
   - Download the JSON file
   - Rename it to `credentials.json` and place it in the same directory as the script

### 3. Set Up Slack (Optional but Recommended)

1. Go to [Slack API](https://api.slack.com/apps)
2. Click "Create New App" > "From scratch"
3. Name your app (e.g., "Gmail Action Router") and select your workspace
4. Go to "OAuth & Permissions"
5. Add the following Bot Token Scopes:
   - `chat:write`
   - `chat:write.public`
6. Install the app to your workspace
7. Copy the "Bot User OAuth Token" (starts with `xoxb-`)
8. Create a channel in Slack (e.g., `#email-actions`) and invite the bot

### 4. Set Up WhatsApp via Twilio (Optional)

1. Sign up for [Twilio](https://www.twilio.com/)
2. Get a Twilio phone number with WhatsApp capability
3. Set up the WhatsApp Sandbox for testing:
   - Go to Messaging > Try it out > Send a WhatsApp message
   - Follow instructions to connect your WhatsApp
4. Get your credentials:
   - Account SID (from dashboard)
   - Auth Token (from dashboard)
   - WhatsApp-enabled number (format: `whatsapp:+14155238886`)

### 5. Configure Environment Variables

Create a `.env` file in the same directory:

```bash
# Slack Configuration
SLACK_BOT_TOKEN=xoxb-your-token-here
SLACK_CHANNEL=#email-actions

# Twilio WhatsApp Configuration
TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
YOUR_WHATSAPP_NUMBER=whatsapp:+1234567890
```

### 6. Customize Filters (Important!)

Edit the `config` dictionary in `gmail_action_router.py`:

```python
config = {
    # ... other config ...
    
    'important_senders': [
        'boss@company.com',
        'client@important.com',
        'partner@',  # Partial match works too
    ],
    'skip_senders': [
        'noreply',
        'notifications@',
        'newsletter',
        'marketing@',
    ],
}
```

### 7. Run the Script

First run (will open browser for Gmail authentication):
```bash
python gmail_action_router.py
```

Subsequent runs (uses saved token):
```bash
python gmail_action_router.py
```

## How It Works

### Priority Detection
The script assigns priority based on:
- **Urgent**: Keywords like "urgent", "asap", "critical", "emergency"
- **High**: Keywords like "important", "today", "deadline" + emails from important senders
- **Medium**: Keywords like "follow up", "update", "meeting"
- **Low**: Keywords like "fyi", newsletters, promotions

### Action Detection
The script determines actions needed:
- **Reply Required**: Contains "reply", "respond", "answer"
- **Review Required**: Contains "review", "approve", "check"
- **Schedule Meeting**: Contains "meeting", "schedule", "calendar"
- **Sign Document**: Contains "sign", "signature", "document"
- **FYI**: Promotional or social emails

### Routing Logic
- **Slack**: All actionable emails (with rich formatting for Urgent/High priority)
- **WhatsApp**: Only URGENT emails (to avoid spam)

## Advanced Usage

### Run on a Schedule

**Using cron (Linux/Mac):**
```bash
# Edit crontab
crontab -e

# Run every hour
0 * * * * cd /path/to/script && /usr/bin/python3 gmail_action_router.py >> /path/to/log.txt 2>&1

# Run every 30 minutes
*/30 * * * * cd /path/to/script && /usr/bin/python3 gmail_action_router.py >> /path/to/log.txt 2>&1
```

**Using Windows Task Scheduler:**
1. Open Task Scheduler
2. Create Basic Task
3. Set trigger (e.g., hourly)
4. Set action: Start a program
5. Program: `python`
6. Arguments: `C:\path\to\gmail_action_router.py`

### Customize Time Range

Modify the hours_back parameter:
```python
# Process last 6 hours
router.process_emails(hours_back=6)

# Process last 48 hours
router.process_emails(hours_back=48)
```

### Add Custom Filters

Add custom logic in the `_determine_priority` or `_determine_action` methods:

```python
def _determine_action(self, subject: str, snippet: str, labels: List[str]) -> str:
    text = (subject + ' ' + snippet).lower()
    
    # Your custom rules
    if 'invoice' in text:
        return "Process Invoice"
    elif 'pr review' in text or 'pull request' in text:
        return "Review Code"
    
    # ... existing logic
```

## Troubleshooting

### Gmail Authentication Issues
- Make sure `credentials.json` is in the same directory
- Delete `token.pickle` and re-authenticate if you get auth errors
- Check that Gmail API is enabled in Google Cloud Console

### Slack Messages Not Sending
- Verify bot token is correct
- Make sure bot is invited to the channel
- Check bot has `chat:write` permission

### WhatsApp Not Working
- Make sure you've completed the Twilio WhatsApp sandbox setup
- Verify phone numbers are in correct format: `whatsapp:+1234567890`
- Check Twilio account has sufficient credits

### No Emails Found
- Check the time range (default is 24 hours)
- Verify you have unread emails
- Check if emails are being filtered out by skip_senders

## Security Best Practices

1. **Never commit credentials**: Add to `.gitignore`:
   ```
   credentials.json
   token.pickle
   .env
   ```

2. **Use environment variables** for all sensitive data

3. **Limit API scopes** to only what's needed

4. **Regularly rotate** Slack and Twilio tokens

5. **Monitor usage** of Twilio (costs money per message)

## Customization Ideas

1. **Add more integrations**: Discord, Telegram, MS Teams
2. **Create custom labels** in Gmail based on priority
3. **Auto-reply** to certain emails
4. **Archive/delete** promotional emails automatically
5. **Create tasks** in project management tools (Asana, Trello)
6. **Send daily summaries** instead of real-time notifications
7. **Machine learning**: Train a model on your email patterns

## Cost Considerations

- **Gmail API**: Free (with quotas)
- **Slack API**: Free for basic usage
- **Twilio WhatsApp**: ~$0.005 per message (keep urgent-only!)

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review API documentation:
   - [Gmail API](https://developers.google.com/gmail/api)
   - [Slack API](https://api.slack.com/)
   - [Twilio WhatsApp](https://www.twilio.com/docs/whatsapp)

## License
Use freely for personal or commercial projects!
