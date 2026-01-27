#!/usr/bin/env python3
"""
Gmail Action Router
Connects to Gmail, filters messages, and routes actions to Slack/WhatsApp
Enhanced with reply, archive, and AI capabilities.
"""

import os
import pickle
import base64
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from enum import Enum
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Slack SDK
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# For WhatsApp, we'll use Twilio (most reliable for automation)
from twilio.rest import Client as TwilioClient

from config import Config


class Priority(Enum):
    URGENT = "🔴 URGENT"
    HIGH = "🟠 HIGH"
    MEDIUM = "🟡 MEDIUM"
    LOW = "🟢 LOW"


class Category(Enum):
    WORK = "work"
    PERSONAL = "personal"
    PROMOTIONS = "promotions"
    SOCIAL = "social"
    OTHER = "other"


@dataclass
class EmailAction:
    """Represents an actionable email"""
    subject: str
    sender: str
    snippet: str
    priority: Priority
    labels: List[str]
    message_id: str
    thread_id: str
    received_date: datetime
    action_required: str
    deadline: Optional[datetime] = None
    category: Category = Category.OTHER
    full_body: Optional[str] = None
    ai_summary: Optional[str] = None


class GmailActionRouter:
    """Main class for Gmail filtering and action routing"""

    # Gmail API scopes (now includes send)
    SCOPES = Config.GMAIL_SCOPES

    def __init__(self, config: Dict = None):
        self.config = config or Config.get_gmail_config()
        self.gmail_service = None
        self.slack_client = None
        self.twilio_client = None
        self.user_email = None  # Will be set after authentication

        # Filter rules
        self.priority_keywords = {
            Priority.URGENT: Config.PRIORITY_KEYWORDS.get('urgent', []),
            Priority.HIGH: Config.PRIORITY_KEYWORDS.get('high', []),
            Priority.MEDIUM: Config.PRIORITY_KEYWORDS.get('medium', []),
            Priority.LOW: Config.PRIORITY_KEYWORDS.get('low', [])
        }

        # Personal life keywords
        self.personal_keywords = Config.PERSONAL_KEYWORDS

        # Sender importance (customize these)
        self.important_senders = self.config.get('important_senders', [])
        self.skip_senders = self.config.get('skip_senders', [])
        self.personal_senders = self.config.get('personal_senders', [])
        
    def authenticate_gmail(self):
        """Authenticate with Gmail API"""
        creds = None
        
        # Token file stores the user's access and refresh tokens
        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                creds = pickle.load(token)
        
        # If no valid credentials, let user log in
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', self.SCOPES)
                creds = flow.run_local_server(port=0)
            
            # Save credentials for next run
            with open('token.pickle', 'wb') as token:
                pickle.dump(creds, token)
        
        self.gmail_service = build('gmail', 'v1', credentials=creds)

        # Get the authenticated user's email address
        try:
            profile = self.gmail_service.users().getProfile(userId='me').execute()
            self.user_email = profile.get('emailAddress')
        except HttpError:
            self.user_email = None

        print("✓ Gmail authenticated successfully")
    
    def setup_slack(self):
        """Setup Slack client"""
        slack_token = self.config.get('slack_bot_token')
        if slack_token:
            self.slack_client = WebClient(token=slack_token)
            print("✓ Slack client initialized")
        else:
            print("⚠ Slack token not provided - Slack notifications disabled")
    
    def setup_whatsapp(self):
        """Setup WhatsApp via Twilio"""
        account_sid = self.config.get('twilio_account_sid')
        auth_token = self.config.get('twilio_auth_token')
        
        if account_sid and auth_token:
            self.twilio_client = TwilioClient(account_sid, auth_token)
            print("✓ WhatsApp (Twilio) client initialized")
        else:
            print("⚠ Twilio credentials not provided - WhatsApp notifications disabled")
    
    def get_unread_emails(self, hours_back: int = 24) -> List[Dict]:
        """Fetch unread emails from the last N hours"""
        try:
            # Calculate the date for filtering
            after_date = datetime.now() - timedelta(hours=hours_back)
            after_timestamp = int(after_date.timestamp())
            
            # Query for unread emails
            query = f'is:unread after:{after_timestamp}'
            
            results = self.gmail_service.users().messages().list(
                userId='me',
                q=query,
                maxResults=50
            ).execute()
            
            messages = results.get('messages', [])
            
            if not messages:
                print(f"No unread emails in the last {hours_back} hours")
                return []
            
            print(f"Found {len(messages)} unread emails")
            
            # Get full message details
            detailed_messages = []
            for msg in messages:
                msg_detail = self.gmail_service.users().messages().get(
                    userId='me',
                    id=msg['id'],
                    format='full'
                ).execute()
                detailed_messages.append(msg_detail)
            
            return detailed_messages
            
        except HttpError as error:
            print(f"An error occurred: {error}")
            return []
    
    def parse_email(self, message: Dict) -> Optional[EmailAction]:
        """Parse email and determine if action is required"""
        headers = message['payload']['headers']
        
        # Extract key information
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
        date_str = next((h['value'] for h in headers if h['name'] == 'Date'), '')
        
        # Get snippet and labels
        snippet = message.get('snippet', '')
        labels = message.get('labelIds', [])
        
        # Skip if from ignored senders
        if any(skip in sender.lower() for skip in self.skip_senders):
            return None
        
        # Determine priority
        priority = self._determine_priority(subject, snippet, sender)
        
        # Determine action required
        action = self._determine_action(subject, snippet, labels)
        
        # Parse date
        try:
            from email.utils import parsedate_to_datetime
            received_date = parsedate_to_datetime(date_str)
        except:
            received_date = datetime.now()
        
        # Determine category
        category = self._determine_category(subject, snippet, sender, labels)

        # Boost priority for personal time-sensitive items
        if category == Category.PERSONAL and priority in [Priority.MEDIUM, Priority.LOW]:
            text = (subject + ' ' + snippet).lower()
            time_sensitive = ['today', 'tomorrow', 'pickup', 'drop-off', 'appointment', 'deadline']
            if any(word in text for word in time_sensitive):
                priority = Priority.HIGH

        return EmailAction(
            subject=subject,
            sender=sender,
            snippet=snippet,
            priority=priority,
            labels=labels,
            message_id=message['id'],
            thread_id=message['threadId'],
            received_date=received_date,
            action_required=action,
            category=category
        )
    
    def _determine_priority(self, subject: str, snippet: str, sender: str) -> Priority:
        """Determine email priority based on content and sender"""
        text = (subject + ' ' + snippet).lower()
        
        # Check for important senders first
        if any(important in sender.lower() for important in self.important_senders):
            return Priority.HIGH
        
        # Check keywords
        for priority, keywords in self.priority_keywords.items():
            if any(keyword in text for keyword in keywords):
                return priority
        
        return Priority.MEDIUM
    
    def _determine_action(self, subject: str, snippet: str, labels: List[str]) -> str:
        """Determine what action is required"""
        text = (subject + ' ' + snippet).lower()

        if any(word in text for word in ['reply', 'respond', 'answer', 'feedback']):
            return "Reply Required"
        elif any(word in text for word in ['review', 'approve', 'check']):
            return "Review Required"
        elif any(word in text for word in ['meeting', 'schedule', 'calendar', 'invite']):
            return "Schedule/Confirm Meeting"
        elif any(word in text for word in ['sign', 'signature', 'document']):
            return "Sign Document"
        elif 'CATEGORY_PROMOTIONS' in labels:
            return "FYI - Promotion"
        elif 'CATEGORY_SOCIAL' in labels:
            return "FYI - Social Update"
        else:
            return "Read and Decide"

    def _determine_category(self, subject: str, snippet: str, sender: str, labels: List[str]) -> Category:
        """Determine email category (work, personal, etc.)"""
        text = (subject + ' ' + snippet).lower()

        # Check for personal senders first
        if any(personal in sender.lower() for personal in self.personal_senders):
            return Category.PERSONAL

        # Check for personal keywords
        if any(keyword in text for keyword in self.personal_keywords):
            return Category.PERSONAL

        # Check Gmail labels
        if 'CATEGORY_PROMOTIONS' in labels:
            return Category.PROMOTIONS
        if 'CATEGORY_SOCIAL' in labels:
            return Category.SOCIAL

        return Category.WORK

    def get_email_body(self, message: Dict) -> str:
        """Extract the full body text from an email message."""
        def get_body_from_parts(parts):
            body = ""
            for part in parts:
                mime_type = part.get('mimeType', '')
                if mime_type == 'text/plain':
                    data = part.get('body', {}).get('data', '')
                    if data:
                        body += base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                elif mime_type == 'text/html' and not body:
                    data = part.get('body', {}).get('data', '')
                    if data:
                        # Basic HTML to text - just strip tags
                        import re
                        html = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                        body = re.sub(r'<[^>]+>', '', html)
                elif 'parts' in part:
                    body += get_body_from_parts(part['parts'])
            return body

        payload = message.get('payload', {})

        # Single part message
        if 'body' in payload and payload['body'].get('data'):
            return base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')

        # Multipart message
        if 'parts' in payload:
            return get_body_from_parts(payload['parts'])

        return message.get('snippet', '')

    def archive_email(self, message_id: str) -> bool:
        """Archive an email by removing INBOX label."""
        try:
            self.gmail_service.users().messages().modify(
                userId='me',
                id=message_id,
                body={'removeLabelIds': ['INBOX', 'UNREAD']}
            ).execute()
            print(f"✓ Archived email {message_id}")
            return True
        except HttpError as error:
            print(f"Error archiving email: {error}")
            return False

    def mark_as_read(self, message_id: str) -> bool:
        """Mark an email as read."""
        try:
            self.gmail_service.users().messages().modify(
                userId='me',
                id=message_id,
                body={'removeLabelIds': ['UNREAD']}
            ).execute()
            return True
        except HttpError as error:
            print(f"Error marking email as read: {error}")
            return False

    def trash_email(self, message_id: str) -> bool:
        """Move an email to trash."""
        try:
            self.gmail_service.users().messages().trash(
                userId='me',
                id=message_id
            ).execute()
            print(f"✓ Trashed email {message_id}")
            return True
        except HttpError as error:
            print(f"Error trashing email: {error}")
            return False

    def send_reply(self, message_id: str, thread_id: str, to_email: str,
                   subject: str, body: str) -> bool:
        """Send a reply to an email."""
        try:
            # Create the reply message
            message = MIMEMultipart()
            message['to'] = to_email
            message['from'] = self.user_email or 'me'

            # Ensure subject has Re: prefix
            if not subject.lower().startswith('re:'):
                subject = f"Re: {subject}"
            message['subject'] = subject

            # Add In-Reply-To and References headers for proper threading
            message['In-Reply-To'] = message_id
            message['References'] = message_id

            # Add body
            message.attach(MIMEText(body, 'plain'))

            # Encode the message
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')

            # Send the message
            sent = self.gmail_service.users().messages().send(
                userId='me',
                body={
                    'raw': raw_message,
                    'threadId': thread_id
                }
            ).execute()

            print(f"✓ Sent reply to {to_email}")
            return True

        except HttpError as error:
            print(f"Error sending reply: {error}")
            return False

    def get_email_by_id(self, message_id: str) -> Optional[Dict]:
        """Fetch a single email by ID."""
        try:
            return self.gmail_service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()
        except HttpError as error:
            print(f"Error fetching email: {error}")
            return None

    def get_sender_email(self, message: Dict) -> str:
        """Extract just the email address from the From header."""
        headers = message['payload']['headers']
        from_header = next((h['value'] for h in headers if h['name'] == 'From'), '')

        # Extract email from "Name <email@domain.com>" format
        import re
        match = re.search(r'<([^>]+)>', from_header)
        if match:
            return match.group(1)
        return from_header
    
    def send_to_slack(self, actions: List[EmailAction]):
        """Send actionable emails to Slack"""
        if not self.slack_client:
            return
        
        channel = self.config.get('slack_channel', '#email-actions')

        try:
            # Group by priority
            by_priority = {}
            for action in actions:
                if action.priority not in by_priority:
                    by_priority[action.priority] = []
                by_priority[action.priority].append(action)
            
            # Send summary
            summary_text = f"*📧 Email Actions Summary* ({len(actions)} total)\n\n"
            
            for priority in [Priority.URGENT, Priority.HIGH, Priority.MEDIUM, Priority.LOW]:
                if priority in by_priority:
                    summary_text += f"{priority.value}: {len(by_priority[priority])}\n"
            
            self.slack_client.chat_postMessage(
                channel=channel,
                text=summary_text
            )
            
            # Check if interactive features are enabled
            include_interactive = Config.is_slack_interactive_enabled()

            # Send detailed messages for urgent and high priority
            for priority in [Priority.URGENT, Priority.HIGH]:
                if priority not in by_priority:
                    continue

                for action in by_priority[priority]:
                    blocks = self._create_slack_blocks(action, include_interactive)
                    self.slack_client.chat_postMessage(
                        channel=channel,
                        blocks=blocks,
                        text=f"{priority.value}: {action.subject}"
                    )
            
            print(f"✓ Sent {len(actions)} email actions to Slack")
            
        except SlackApiError as e:
            print(f"Error sending to Slack: {e.response['error']}")
    
    def _create_slack_blocks(self, action: EmailAction, include_interactive: bool = True) -> List[Dict]:
        """Create Slack message blocks for an email action"""
        gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{action.message_id}"

        # Category badge
        category_emoji = {
            Category.WORK: "💼",
            Category.PERSONAL: "🏠",
            Category.PROMOTIONS: "🏷️",
            Category.SOCIAL: "👥",
            Category.OTHER: "📧"
        }
        cat_badge = category_emoji.get(action.category, "📧")

        # Build preview text
        preview_text = action.ai_summary if action.ai_summary else action.snippet[:200]
        if len(preview_text) > 200:
            preview_text = preview_text[:200] + "..."

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{action.priority.value} {cat_badge}"
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Subject:*\n{action.subject}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*From:*\n{action.sender}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Action:*\n{action.action_required}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Received:*\n{action.received_date.strftime('%b %d, %I:%M %p')}"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Preview:*\n{preview_text}"
                }
            }
        ]

        # Add AI summary section if available
        if action.ai_summary and action.ai_summary != action.snippet[:200]:
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "🤖 _AI Summary_"
                    }
                ]
            })

        # Action buttons
        if include_interactive:
            blocks.append({
                "type": "actions",
                "block_id": f"email_actions_{action.message_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "📧 Open"},
                        "url": gmail_link,
                        "action_id": "open_gmail"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Archive"},
                        "action_id": "archive_email",
                        "value": action.message_id,
                        "style": "primary"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "↩️ Reply"},
                        "action_id": "reply_email",
                        "value": action.message_id
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "⏰ Snooze"},
                        "action_id": "snooze_email",
                        "value": action.message_id
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "👤 Delegate"},
                        "action_id": "delegate_email",
                        "value": action.message_id
                    }
                ]
            })
        else:
            # Simple open button when interactivity is not configured
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open in Gmail"},
                        "url": gmail_link,
                        "style": "primary"
                    }
                ]
            })

        blocks.append({"type": "divider"})

        return blocks
    
    def send_to_whatsapp(self, urgent_actions: List[EmailAction]):
        """Send urgent emails to WhatsApp"""
        if not self.twilio_client or not urgent_actions:
            return
        
        from_whatsapp = self.config.get('twilio_whatsapp_from')
        to_whatsapp = self.config.get('your_whatsapp_number')
        
        if not from_whatsapp or not to_whatsapp:
            print("⚠ WhatsApp numbers not configured")
            return
        
        try:
            # Send only urgent items to WhatsApp
            for action in urgent_actions:
                if action.priority == Priority.URGENT:
                    message = (
                        f"🔴 URGENT EMAIL\n\n"
                        f"From: {action.sender}\n"
                        f"Subject: {action.subject}\n"
                        f"Action: {action.action_required}\n\n"
                        f"Preview: {action.snippet[:100]}..."
                    )
                    
                    self.twilio_client.messages.create(
                        from_=from_whatsapp,
                        to=to_whatsapp,
                        body=message
                    )
            
            print(f"✓ Sent {len([a for a in urgent_actions if a.priority == Priority.URGENT])} urgent emails to WhatsApp")
            
        except Exception as e:
            print(f"Error sending to WhatsApp: {e}")
    
    def process_emails(self, hours_back: int = 24):
        """Main processing function"""
        print(f"\n{'='*60}")
        print(f"Processing emails from the last {hours_back} hours...")
        print(f"{'='*60}\n")
        
        # Get emails
        messages = self.get_unread_emails(hours_back)
        
        # Parse and filter
        actions = []
        for msg in messages:
            action = self.parse_email(msg)
            if action:
                actions.append(action)
        
        if not actions:
            print("No actionable emails found")
            return
        
        # Sort by priority
        priority_order = {
            Priority.URGENT: 0,
            Priority.HIGH: 1,
            Priority.MEDIUM: 2,
            Priority.LOW: 3
        }
        actions.sort(key=lambda x: priority_order[x.priority])
        
        # Print summary
        print(f"\n📊 Found {len(actions)} actionable emails:\n")
        for action in actions:
            print(f"{action.priority.value} | {action.subject[:50]}... | {action.action_required}")
        
        # Send to Slack
        if self.config.get('enable_slack', True):
            self.send_to_slack(actions)
        
        # Send urgent to WhatsApp
        if self.config.get('enable_whatsapp', True):
            urgent = [a for a in actions if a.priority == Priority.URGENT]
            if urgent:
                self.send_to_whatsapp(urgent)
        
        print(f"\n{'='*60}")
        print("✓ Processing complete!")
        print(f"{'='*60}\n")


def main():
    """Main execution function"""

    # Initialize router with centralized config
    router = GmailActionRouter()

    # Authenticate services
    router.authenticate_gmail()
    router.setup_slack()
    router.setup_whatsapp()

    # Process emails (default: last 7 days)
    router.process_emails(hours_back=Config.DEFAULT_SCAN_HOURS)


if __name__ == '__main__':
    main()
