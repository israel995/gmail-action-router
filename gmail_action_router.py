#!/usr/bin/env python3
"""
Gmail Action Router
Connects to Gmail, filters messages, and routes actions to Slack/WhatsApp
"""

import os
import pickle
import base64
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

from dotenv import load_dotenv
load_dotenv(override=True)  # This loads your .env file!

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


class Priority(Enum):
    URGENT = "🔴 URGENT"
    HIGH = "🟠 HIGH"
    MEDIUM = "🟡 MEDIUM"
    LOW = "🟢 LOW"


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


class GmailActionRouter:
    """Main class for Gmail filtering and action routing"""
    
    # Gmail API scopes
    SCOPES = ['https://www.googleapis.com/auth/gmail.readonly',
              'https://www.googleapis.com/auth/gmail.modify']
    
    def __init__(self, config: Dict):
        self.config = config
        self.gmail_service = None
        self.slack_client = None
        self.twilio_client = None
        
        # Filter rules
        self.priority_keywords = {
            Priority.URGENT: ['urgent', 'asap', 'critical', 'emergency', 'immediately'],
            Priority.HIGH: ['important', 'today', 'deadline', 'action required', 'please review'],
            Priority.MEDIUM: ['follow up', 'update', 'reminder', 'meeting'],
            Priority.LOW: ['fyi', 'for your information', 'newsletter']
        }
        
        # Sender importance (customize these)
        self.important_senders = config.get('important_senders', [])
        self.skip_senders = config.get('skip_senders', [])
        
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
        
        return EmailAction(
            subject=subject,
            sender=sender,
            snippet=snippet,
            priority=priority,
            labels=labels,
            message_id=message['id'],
            thread_id=message['threadId'],
            received_date=received_date,
            action_required=action
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
        elif any(word in text for word in ['meeting', 'schedule', 'calendar']):
            return "Schedule/Confirm Meeting"
        elif any(word in text for word in ['sign', 'signature', 'document']):
            return "Sign Document"
        elif 'CATEGORY_PROMOTIONS' in labels:
            return "FYI - Promotion"
        elif 'CATEGORY_SOCIAL' in labels:
            return "FYI - Social Update"
        else:
            return "Read and Decide"
    
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
            
            # Send detailed messages for urgent and high priority
            for priority in [Priority.URGENT, Priority.HIGH]:
                if priority not in by_priority:
                    continue
                
                for action in by_priority[priority]:
                    blocks = self._create_slack_blocks(action)
                    self.slack_client.chat_postMessage(
                        channel=channel,
                        blocks=blocks,
                        text=f"{priority.value}: {action.subject}"
                    )
            
            print(f"✓ Sent {len(actions)} email actions to Slack")
            
        except SlackApiError as e:
            print(f"Error sending to Slack: {e.response['error']}")
    
    def _create_slack_blocks(self, action: EmailAction) -> List[Dict]:
        """Create Slack message blocks for an email action"""
        gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{action.message_id}"
        
        return [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{action.priority.value}"
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
                    "text": f"*Preview:*\n{action.snippet[:150]}..."
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Open in Gmail"
                        },
                        "url": gmail_link,
                        "style": "primary"
                    }
                ]
            },
            {
                "type": "divider"
            }
        ]
    
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
    
    # Configuration
    config = {
        # Gmail will authenticate via OAuth
        
        # Slack configuration
        'slack_bot_token': os.getenv('SLACK_BOT_TOKEN'),
        'slack_channel': os.getenv('SLACK_CHANNEL'),
        'enable_slack': True,
        
        # Twilio WhatsApp configuration
        'twilio_account_sid': os.getenv('TWILIO_ACCOUNT_SID'),
        'twilio_auth_token': os.getenv('TWILIO_AUTH_TOKEN'),
        'twilio_whatsapp_from': os.getenv('TWILIO_WHATSAPP_FROM'),  # e.g., 'whatsapp:+14155238886'
        'your_whatsapp_number': os.getenv('YOUR_WHATSAPP_NUMBER'),  # e.g., 'whatsapp:+1234567890'
        'enable_whatsapp': True,
        
        # Filter configuration
        'important_senders': [
            'boss@company.com',
            'client@important.com',
            # Add your important senders
        ],
        'skip_senders': [
            'noreply',
            'notifications@',
            'newsletter',
            # Add senders to ignore
        ],
    }
    
    # Initialize router
    router = GmailActionRouter(config)
    
    # Authenticate services
    router.authenticate_gmail()
    router.setup_slack()
    router.setup_whatsapp()
    
    # Process emails (default: last 7 days)
    router.process_emails(hours_back=168)


if __name__ == '__main__':
    main()
