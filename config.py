"""
Centralized Configuration for Gmail Action Router
All settings and environment variables are managed here.
"""

import os
from dotenv import load_dotenv

load_dotenv(override=True)


class Config:
    """Central configuration class for the email agent."""

    # Gmail API Scopes
    GMAIL_SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.modify',
        'https://www.googleapis.com/auth/gmail.send',
    ]

    # Google Calendar API Scopes
    CALENDAR_SCOPES = [
        'https://www.googleapis.com/auth/calendar.readonly',
        'https://www.googleapis.com/auth/calendar.events',
    ]

    # Slack Configuration
    SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
    SLACK_SIGNING_SECRET = os.getenv('SLACK_SIGNING_SECRET')
    SLACK_CHANNEL = os.getenv('SLACK_CHANNEL', '#email-actions')
    DELEGATION_CHANNEL = os.getenv('DELEGATION_CHANNEL', '#delegated-tasks')

    # Twilio WhatsApp Configuration
    TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
    TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
    TWILIO_WHATSAPP_FROM = os.getenv('TWILIO_WHATSAPP_FROM')
    YOUR_WHATSAPP_NUMBER = os.getenv('YOUR_WHATSAPP_NUMBER')

    # AI Configuration
    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
    AI_MODEL = os.getenv('AI_MODEL', 'claude-sonnet-4-20250514')
    AI_SUMMARY_THRESHOLD = int(os.getenv('AI_SUMMARY_THRESHOLD', '500'))

    # Database Configuration
    DATABASE_PATH = os.getenv('DATABASE_PATH', 'email_agent.db')

    # Server Configuration
    FLASK_HOST = os.getenv('FLASK_HOST', '0.0.0.0')
    FLASK_PORT = int(os.getenv('FLASK_PORT', '5000'))
    FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'

    # Email Processing Configuration
    DEFAULT_SCAN_HOURS = int(os.getenv('DEFAULT_SCAN_HOURS', '168'))
    MAX_EMAILS_PER_FETCH = int(os.getenv('MAX_EMAILS_PER_FETCH', '50'))

    # Scheduling Configuration
    DAILY_DIGEST_HOUR = int(os.getenv('DAILY_DIGEST_HOUR', '7'))
    DAILY_DIGEST_MINUTE = int(os.getenv('DAILY_DIGEST_MINUTE', '0'))
    SNOOZE_CHECK_INTERVAL_MINUTES = int(os.getenv('SNOOZE_CHECK_INTERVAL_MINUTES', '15'))

    # Sender Lists (comma-separated in env)
    IMPORTANT_SENDERS = [
        s.strip() for s in os.getenv('IMPORTANT_SENDERS', '').split(',') if s.strip()
    ]
    SKIP_SENDERS = [
        s.strip() for s in os.getenv('SKIP_SENDERS', 'noreply,notifications@,newsletter').split(',') if s.strip()
    ]
    PERSONAL_SENDERS = [
        s.strip() for s in os.getenv('PERSONAL_SENDERS', '').split(',') if s.strip()
    ]

    # Priority Keywords
    PRIORITY_KEYWORDS = {
        'urgent': ['urgent', 'asap', 'critical', 'emergency', 'immediately'],
        'high': ['important', 'today', 'deadline', 'action required', 'please review'],
        'medium': ['follow up', 'update', 'reminder', 'meeting'],
        'low': ['fyi', 'for your information', 'newsletter'],
    }

    # Personal Life Keywords (for category detection)
    PERSONAL_KEYWORDS = [
        'daycare', 'school', 'doctor', 'appointment', 'pediatrician',
        'dentist', 'pharmacy', 'prescription', 'pickup', 'drop-off',
        'parent', 'family', 'kid', 'child', 'health', 'insurance claim',
    ]

    @classmethod
    def get_gmail_config(cls) -> dict:
        """Get Gmail-related configuration as a dictionary."""
        return {
            'slack_bot_token': cls.SLACK_BOT_TOKEN,
            'slack_channel': cls.SLACK_CHANNEL,
            'enable_slack': bool(cls.SLACK_BOT_TOKEN),
            'twilio_account_sid': cls.TWILIO_ACCOUNT_SID,
            'twilio_auth_token': cls.TWILIO_AUTH_TOKEN,
            'twilio_whatsapp_from': cls.TWILIO_WHATSAPP_FROM,
            'your_whatsapp_number': cls.YOUR_WHATSAPP_NUMBER,
            'enable_whatsapp': bool(cls.TWILIO_ACCOUNT_SID and cls.TWILIO_AUTH_TOKEN),
            'important_senders': cls.IMPORTANT_SENDERS,
            'skip_senders': cls.SKIP_SENDERS,
            'personal_senders': cls.PERSONAL_SENDERS,
        }

    @classmethod
    def is_ai_enabled(cls) -> bool:
        """Check if AI features are available."""
        return bool(cls.ANTHROPIC_API_KEY)

    @classmethod
    def is_slack_interactive_enabled(cls) -> bool:
        """Check if Slack interactivity is configured."""
        return bool(cls.SLACK_BOT_TOKEN and cls.SLACK_SIGNING_SECRET)
