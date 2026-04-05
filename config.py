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
    FAMILY_SUMMARY_HOUR = int(os.getenv('FAMILY_SUMMARY_HOUR', '20'))  # 8 PM
    FAMILY_SUMMARY_MINUTE = int(os.getenv('FAMILY_SUMMARY_MINUTE', '0'))
    FAMILY_SUMMARY_DAYS = int(os.getenv('FAMILY_SUMMARY_DAYS', '1'))  # Look back 1 day by default

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

    # Family Senders - schools, daycares, activities (domains/partial matches)
    FAMILY_SENDERS = [
        s.strip() for s in os.getenv('FAMILY_SENDERS', '').split(',') if s.strip()
    ] or [
        # Daycares/Preschools
        'kaymbu.com',
        'preschoolsmiles.com',
        # Schools
        'nvnet.org',
        'demarestpublicschools',
        'membershiptoolkit.com',
        # Nature Centers / Community
        'dmsnews-nvnet.org',
    ]

    # ===========================================
    # WORK EMAIL CONFIGURATION
    # ===========================================

    # Internal company domain
    WORK_INTERNAL_DOMAINS = [
        s.strip() for s in os.getenv('WORK_INTERNAL_DOMAINS', '').split(',') if s.strip()
    ] or [
        'hyro.ai',
    ]

    # Investor domains (VCs, investors)
    WORK_INVESTORS = [
        s.strip() for s in os.getenv('WORK_INVESTORS', '').split(',') if s.strip()
    ] or [
        'healthiercapital.com',
        'definevc.com',
        'nvp.com',
        'macquarie.com',
        'spero.vc',
    ]

    # Customer domains (US health systems)
    WORK_CUSTOMERS = [
        s.strip() for s in os.getenv('WORK_CUSTOMERS', '').split(',') if s.strip()
    ] or [
        # Known customers
        'christushealth.org',
        'sutterhealth.org',
        'ehmchealth.org',
        'htahealth.com',
        # Add more health systems as needed
    ]

    # Health system domain patterns (for pattern matching)
    HEALTH_SYSTEM_PATTERNS = [
        'health.org', 'health.com', 'hospital.org', 'hospital.com',
        'medical.org', 'medical.com', 'healthcare.org', 'healthcare.com',
        'med.org', 'clinic.org', 'health.edu', 'mayo.edu',
    ]

    # Contract/signature platforms (always important)
    WORK_CONTRACT_PLATFORMS = [
        'docusign.net',
        'adobesign.com',
        'rightsignature.com',
        'hellosign.com',
        'pandadoc.com',
    ]

    # Marketing/newsletter domains to skip
    WORK_SKIP_DOMAINS = [
        s.strip() for s in os.getenv('WORK_SKIP_DOMAINS', '').split(',') if s.strip()
    ] or [
        'beehiiv.com', 'substack.com', 'mailchimp.com',
        'stitchfix.com', 'bloomingdales.com', 'canyonranch.com',
        'zillow.com', 'americandream.com',
    ]

    # Work summary scheduling
    WORK_SUMMARY_HOUR = int(os.getenv('WORK_SUMMARY_HOUR', '7'))  # 7 AM
    WORK_SUMMARY_MINUTE = int(os.getenv('WORK_SUMMARY_MINUTE', '30'))
    WORK_SUMMARY_DAYS = int(os.getenv('WORK_SUMMARY_DAYS', '1'))  # Look back 1 day

    # ===========================================
    # CEO CO-PILOT CONFIGURATION
    # ===========================================

    # Notion integration
    NOTION_API_KEY = os.getenv('NOTION_API_KEY')
    NOTION_ACTION_ITEMS_DB = os.getenv('NOTION_ACTION_ITEMS_DB')  # Database ID for action items
    NOTION_PROJECTS_DB = os.getenv('NOTION_PROJECTS_DB')          # Database ID for projects

    # VIP contacts: "Name:email:role,Name2:email2:role2"
    # e.g. "Alice Smith:alice@company.com:CTO,Bob Lee:bob@company.com:VP Sales"
    VIP_CONTACTS_RAW = os.getenv('VIP_CONTACTS', '')

    # Default silence threshold (hours) before alerting about a VIP contact
    VIP_DEFAULT_SILENCE_HOURS = int(os.getenv('VIP_DEFAULT_SILENCE_HOURS', '48'))

    # Tracked projects: "ProjectName:keyword1+keyword2,Project2:kw1+kw2"
    TRACKED_PROJECTS_RAW = os.getenv('TRACKED_PROJECTS', '')

    # Co-pilot Slack channel (defaults to main channel)
    COPILOT_CHANNEL = os.getenv('COPILOT_CHANNEL', os.getenv('SLACK_CHANNEL', '#email-actions'))

    # Co-pilot morning briefing schedule (default: 8 AM weekdays)
    COPILOT_BRIEFING_HOUR = int(os.getenv('COPILOT_BRIEFING_HOUR', '8'))
    COPILOT_BRIEFING_MINUTE = int(os.getenv('COPILOT_BRIEFING_MINUTE', '0'))

    # How often to check for overdue VIPs and stalled projects (minutes)
    COPILOT_CHECK_INTERVAL_MINUTES = int(os.getenv('COPILOT_CHECK_INTERVAL_MINUTES', '480'))

    # Notion sync interval (minutes)
    NOTION_SYNC_INTERVAL_MINUTES = int(os.getenv('NOTION_SYNC_INTERVAL_MINUTES', '480'))

    @classmethod
    def parse_vip_contacts(cls) -> list:
        """Parse VIP_CONTACTS env var into a list of dicts."""
        contacts = []
        if not cls.VIP_CONTACTS_RAW:
            return contacts
        for entry in cls.VIP_CONTACTS_RAW.split(','):
            parts = [p.strip() for p in entry.split(':')]
            if len(parts) >= 2:
                contacts.append({
                    'name': parts[0],
                    'email': parts[1],
                    'role': parts[2] if len(parts) > 2 else '',
                    'max_silence_hours': int(parts[3]) if len(parts) > 3 else cls.VIP_DEFAULT_SILENCE_HOURS,
                })
        return contacts

    @classmethod
    def parse_tracked_projects(cls) -> list:
        """Parse TRACKED_PROJECTS env var into a list of dicts."""
        projects = []
        if not cls.TRACKED_PROJECTS_RAW:
            return projects
        for entry in cls.TRACKED_PROJECTS_RAW.split(','):
            parts = [p.strip() for p in entry.split(':')]
            if parts[0]:
                projects.append({
                    'name': parts[0],
                    'keywords': parts[1].replace('+', ',') if len(parts) > 1 else parts[0],
                })
        return projects

    @classmethod
    def is_notion_enabled(cls) -> bool:
        """Check if Notion integration is configured."""
        return bool(cls.NOTION_API_KEY)

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
