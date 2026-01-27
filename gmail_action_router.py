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


class InviteStatus(Enum):
    NEW = "new"
    UPDATED = "updated"
    CANCELLED = "cancelled"
    RESPONSE = "response"


class ResponseStatus(Enum):
    ACCEPTED = "accepted"
    DECLINED = "declined"
    TENTATIVE = "tentative"
    NEEDS_ACTION = "needs_action"


@dataclass
class CalendarInvite:
    """Represents a calendar invite from email"""
    message_id: str
    thread_id: str
    subject: str
    organizer: str
    event_start: Optional[datetime]
    event_end: Optional[datetime]
    location: Optional[str]
    description: Optional[str]
    attendees: List[Dict]  # List of {email, name, status, comment}
    status: InviteStatus
    has_comments: bool
    comments: List[Dict]  # List of {from, text}
    received_date: datetime
    is_all_day: bool = False
    recurrence: Optional[str] = None
    my_response: ResponseStatus = ResponseStatus.NEEDS_ACTION
    has_replies: bool = False  # True if attendees have responded (accepted/declined/tentative)
    replies: List[Dict] = None  # List of {from, response} for attendee responses


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

    def move_to_inbox(self, message_id: str) -> bool:
        """Move an email back to inbox (for snooze unsnoozing)."""
        try:
            self.gmail_service.users().messages().modify(
                userId='me',
                id=message_id,
                body={'addLabelIds': ['INBOX', 'UNREAD']}
            ).execute()
            print(f"✓ Moved email {message_id} back to inbox")
            return True
        except HttpError as error:
            print(f"Error moving email to inbox: {error}")
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

    def get_calendar_invites(self, hours_back: int = 168) -> List[CalendarInvite]:
        """Fetch and parse calendar invite emails."""
        try:
            after_date = datetime.now() - timedelta(hours=hours_back)
            after_timestamp = int(after_date.timestamp())

            # Search for calendar-related emails
            query = f'(filename:ics OR subject:"invitation" OR subject:"invite" OR subject:"accepted" OR subject:"declined" OR subject:"tentative" OR from:calendar-notification) after:{after_timestamp}'

            results = self.gmail_service.users().messages().list(
                userId='me',
                q=query,
                maxResults=100
            ).execute()

            messages = results.get('messages', [])
            if not messages:
                print("No calendar invites found")
                return []

            print(f"Found {len(messages)} potential calendar invites")

            invites = []
            for msg in messages:
                msg_detail = self.gmail_service.users().messages().get(
                    userId='me',
                    id=msg['id'],
                    format='full'
                ).execute()

                invite = self._parse_calendar_invite(msg_detail)
                if invite:
                    invites.append(invite)

            print(f"Parsed {len(invites)} calendar invites")
            return invites

        except HttpError as error:
            print(f"Error fetching calendar invites: {error}")
            return []

    def _parse_calendar_invite(self, message: Dict) -> Optional[CalendarInvite]:
        """Parse a calendar invite from an email message."""
        import re
        from email.utils import parsedate_to_datetime

        headers = message['payload']['headers']
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), '')
        date_str = next((h['value'] for h in headers if h['name'] == 'Date'), '')

        # Parse received date
        try:
            received_date = parsedate_to_datetime(date_str)
        except:
            received_date = datetime.now()

        # Determine invite status from subject
        subject_lower = subject.lower()
        if 'cancelled' in subject_lower or 'canceled' in subject_lower:
            status = InviteStatus.CANCELLED
        elif 'updated' in subject_lower or 'changed' in subject_lower:
            status = InviteStatus.UPDATED
        elif 'accepted' in subject_lower or 'declined' in subject_lower or 'tentative' in subject_lower:
            status = InviteStatus.RESPONSE
        else:
            status = InviteStatus.NEW

        # Get email body and look for ICS content
        body = self.get_email_body(message)
        ics_content = self._extract_ics_from_message(message)

        # Parse event details
        event_start = None
        event_end = None
        location = None
        description = None
        organizer = sender
        attendees = []
        is_all_day = False
        recurrence = None

        if ics_content:
            parsed = self._parse_ics_content(ics_content)
            event_start = parsed.get('start')
            event_end = parsed.get('end')
            location = parsed.get('location')
            description = parsed.get('description')
            organizer = parsed.get('organizer', sender)
            attendees = parsed.get('attendees', [])
            is_all_day = parsed.get('is_all_day', False)
            recurrence = parsed.get('recurrence')

        # If ICS didn't give us a date, try to extract from email body/subject
        if not event_start:
            event_start = self._extract_date_from_text(subject + ' ' + body)

        # Extract comments from the email body
        comments = self._extract_comments_from_body(body, sender)
        has_comments = len(comments) > 0

        # Detect replies (attendee responses like accepted/declined/tentative)
        replies = []
        has_replies = False

        # Check if this is a response email (someone accepted/declined)
        if status == InviteStatus.RESPONSE:
            has_replies = True
            # Extract who responded and how from the subject
            response_type = None
            if 'accepted' in subject_lower:
                response_type = 'accepted'
            elif 'declined' in subject_lower:
                response_type = 'declined'
            elif 'tentative' in subject_lower:
                response_type = 'tentative'

            if response_type:
                replies.append({
                    'from': sender,
                    'response': response_type
                })

        # Also check attendees for responses
        for attendee in attendees:
            att_status = attendee.get('status', '').lower()
            if att_status in ['accepted', 'declined', 'tentative']:
                if att_status != 'needs_action':
                    has_replies = True
                    replies.append({
                        'from': attendee.get('email', attendee.get('name', 'Unknown')),
                        'response': att_status
                    })

        # Skip if we can't determine it's a real calendar invite
        if not event_start and status == InviteStatus.NEW:
            # Check if it really looks like an invite
            invite_indicators = ['invite', 'meeting', 'calendar', 'event', 'accepted', 'declined']
            if not any(ind in subject_lower for ind in invite_indicators):
                return None

        return CalendarInvite(
            message_id=message['id'],
            thread_id=message['threadId'],
            subject=subject,
            organizer=organizer,
            event_start=event_start,
            event_end=event_end,
            location=location,
            description=description,
            attendees=attendees,
            status=status,
            has_comments=has_comments,
            comments=comments,
            received_date=received_date,
            is_all_day=is_all_day,
            recurrence=recurrence,
            has_replies=has_replies,
            replies=replies
        )

    def _extract_ics_from_message(self, message: Dict) -> Optional[str]:
        """Extract ICS content from email attachments or body."""
        def search_parts(parts):
            for part in parts:
                mime_type = part.get('mimeType', '')
                if mime_type == 'text/calendar' or mime_type == 'application/ics':
                    data = part.get('body', {}).get('data', '')
                    if data:
                        return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                if 'parts' in part:
                    result = search_parts(part['parts'])
                    if result:
                        return result
            return None

        payload = message.get('payload', {})
        if 'parts' in payload:
            return search_parts(payload['parts'])
        return None

    def _parse_ics_content(self, ics_content: str) -> Dict:
        """Parse ICS content and extract event details."""
        import re

        result = {
            'start': None,
            'end': None,
            'location': None,
            'description': None,
            'organizer': None,
            'attendees': [],
            'is_all_day': False,
            'recurrence': None
        }

        # Extract DTSTART - handle various formats
        # Format 1: DTSTART:20240115T140000Z
        # Format 2: DTSTART;VALUE=DATE:20240115
        # Format 3: DTSTART;TZID=America/Los_Angeles:20240115T140000
        dtstart_patterns = [
            r'DTSTART[^:\n]*:(\d{8}T\d{6}Z?)',  # With time
            r'DTSTART[^:\n]*:(\d{8})',  # Date only
        ]
        for pattern in dtstart_patterns:
            dtstart_match = re.search(pattern, ics_content)
            if dtstart_match:
                dt_str = dtstart_match.group(1)
                result['start'] = self._parse_ics_datetime(dt_str)
                result['is_all_day'] = len(dt_str) == 8  # Date only, no time
                break

        # Extract DTEND
        dtend_patterns = [
            r'DTEND[^:\n]*:(\d{8}T\d{6}Z?)',
            r'DTEND[^:\n]*:(\d{8})',
        ]
        for pattern in dtend_patterns:
            dtend_match = re.search(pattern, ics_content)
            if dtend_match:
                result['end'] = self._parse_ics_datetime(dtend_match.group(1))
                break

        # Extract LOCATION
        location_match = re.search(r'LOCATION[^:]*:([^\r\n]+)', ics_content)
        if location_match:
            result['location'] = location_match.group(1).strip()

        # Extract DESCRIPTION
        desc_match = re.search(r'DESCRIPTION[^:]*:([^\r\n]+)', ics_content)
        if desc_match:
            result['description'] = desc_match.group(1).strip()[:500]  # Limit length

        # Extract ORGANIZER
        org_match = re.search(r'ORGANIZER[^:]*:mailto:([^\r\n]+)', ics_content)
        if org_match:
            result['organizer'] = org_match.group(1).strip()

        # Extract ATTENDEES
        attendee_matches = re.findall(r'ATTENDEE[^:]*(?:CN=([^;:]+))?[^:]*:mailto:([^\r\n]+)', ics_content)
        for match in attendee_matches:
            name = match[0] if match[0] else match[1]
            email = match[1]
            # Try to get PARTSTAT
            partstat_match = re.search(rf'ATTENDEE[^:]*PARTSTAT=([^;:]+)[^:]*:mailto:{re.escape(email)}', ics_content)
            status = partstat_match.group(1) if partstat_match else 'NEEDS-ACTION'
            result['attendees'].append({
                'name': name,
                'email': email,
                'status': status.lower().replace('-', '_'),
                'comment': None
            })

        # Extract RRULE (recurrence)
        rrule_match = re.search(r'RRULE[^:]*:([^\r\n]+)', ics_content)
        if rrule_match:
            result['recurrence'] = rrule_match.group(1).strip()

        return result

    def _parse_ics_datetime(self, dt_str: str) -> Optional[datetime]:
        """Parse ICS datetime format."""
        try:
            if len(dt_str) == 8:  # Date only: 20240115
                return datetime.strptime(dt_str, '%Y%m%d')
            elif 'T' in dt_str:
                if dt_str.endswith('Z'):
                    return datetime.strptime(dt_str, '%Y%m%dT%H%M%SZ')
                else:
                    return datetime.strptime(dt_str, '%Y%m%dT%H%M%S')
        except ValueError:
            pass
        return None

    def _extract_date_from_text(self, text: str) -> Optional[datetime]:
        """Try to extract a date/time from text."""
        import re

        # Try dateutil parser first on common date/time patterns
        patterns = [
            # "Monday, January 15, 2024 at 2:00 PM"
            r'(\w+day,?\s+\w+\s+\d{1,2},?\s+\d{4}\s+(?:at\s+)?\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)',
            # "January 15, 2024 at 2:00 PM" or "January 15, 2024 2:00 PM"
            r'(\w+\s+\d{1,2},?\s+\d{4}\s+(?:at\s+)?\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)',
            # "January 15, 2024"
            r'(\w+\s+\d{1,2},?\s+\d{4})',
            # "15 January 2024"
            r'(\d{1,2}\s+\w+\s+\d{4})',
            # "2024-01-15T14:00:00"
            r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})',
            # "2024-01-15"
            r'(\d{4}-\d{2}-\d{2})',
            # "01/15/2024"
            r'(\d{1,2}/\d{1,2}/\d{4})',
            # "Mon Jan 15 2024 14:00"
            r'(\w{3}\s+\w{3}\s+\d{1,2}\s+\d{4}\s+\d{1,2}:\d{2})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    from dateutil import parser
                    return parser.parse(match.group(1))
                except:
                    continue

        # Fallback: try parsing the whole text with dateutil
        try:
            from dateutil import parser
            # Look for "When:" or "Date:" prefixes
            when_match = re.search(r'(?:When|Date|Time)[:\s]+([^\n]+)', text, re.IGNORECASE)
            if when_match:
                return parser.parse(when_match.group(1), fuzzy=True)
        except:
            pass

        return None

    def _extract_comments_from_body(self, body: str, sender: str) -> List[Dict]:
        """Extract comments/notes from invite email body."""
        comments = []

        # Look for common comment patterns
        lines = body.split('\n')
        in_comment = False
        current_comment = []

        for line in lines:
            line = line.strip()
            # Skip empty lines and common boilerplate
            if not line:
                if current_comment:
                    comments.append({
                        'from': sender,
                        'text': ' '.join(current_comment)
                    })
                    current_comment = []
                continue

            # Skip boilerplate
            boilerplate = [
                'this event has been', 'view your calendar', 'more details',
                'join with google meet', 'join zoom meeting', 'dial-in',
                'meeting id:', 'passcode:', 'one tap mobile', 'when:',
                'where:', 'calendar:', 'who:', 'going?', 'yes', 'no', 'maybe',
                'organizer:', 'https://', 'http://', '---', '___', '==='
            ]
            if any(bp in line.lower() for bp in boilerplate):
                continue

            # Look for personal notes/comments (usually short, personal messages)
            if len(line) > 10 and len(line) < 500:
                # Check if it looks like a personal message
                personal_indicators = [
                    'please', 'let me know', 'looking forward', 'see you',
                    'can you', 'could you', 'would you', 'hi ', 'hey ',
                    'note:', 'fyi', 'heads up', 'reminder:', 'important:',
                    'bring', 'prepare', 'agenda', 'discuss', 'review'
                ]
                if any(ind in line.lower() for ind in personal_indicators):
                    current_comment.append(line)

        if current_comment:
            comments.append({
                'from': sender,
                'text': ' '.join(current_comment)
            })

        return comments

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

    def send_calendar_invites_to_slack(self, invites: List[CalendarInvite]):
        """Send calendar invites to Slack, organized by priority."""
        if not self.slack_client or not invites:
            return

        channel = self.config.get('slack_channel', '#email-actions')
        include_interactive = Config.is_slack_interactive_enabled()

        try:
            # Group invites by status
            new_invites = [i for i in invites if i.status == InviteStatus.NEW]
            updated_invites = [i for i in invites if i.status == InviteStatus.UPDATED]
            cancelled = [i for i in invites if i.status == InviteStatus.CANCELLED]
            # Only show invites with actual text comments/replies (not just accept/decline)
            with_text_replies = [i for i in invites if i.has_comments and i.comments]

            # Sort by event date (None dates go to end)
            def sort_by_event_date(invite):
                return invite.event_start or datetime.max

            new_invites.sort(key=sort_by_event_date)
            updated_invites.sort(key=sort_by_event_date)

            # ===== SECTION 1: Invites with Text Replies =====
            if with_text_replies:
                # Send header message
                self.slack_client.chat_postMessage(
                    channel=channel,
                    text="Invites with Replies",
                    blocks=[
                        {
                            "type": "header",
                            "text": {"type": "plain_text", "text": "💬 Invites with Text Replies"}
                        },
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": f"*{len(with_text_replies)} invites* have text replies that may need your attention."}
                        }
                    ]
                )

                # Send each invite as a SEPARATE message so archiving one doesn't affect others
                for invite in with_text_replies:
                    self.slack_client.chat_postMessage(
                        channel=channel,
                        text=f"Invite: {invite.subject}",
                        blocks=self._create_calendar_invite_block(invite, include_interactive, show_comments=True)
                    )

            # ===== SECTION 2: New Invites =====
            if new_invites:
                # Filter out ones already shown in text replies section
                new_only = [i for i in new_invites if not (i.has_comments and i.comments)]
                if new_only:
                    header_blocks = [
                        {
                            "type": "header",
                            "text": {"type": "plain_text", "text": "🆕 New Meeting Invitations"}
                        },
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": f"*{len(new_only)} new invites* received. These are meetings you've been invited to."}
                        }
                    ]

                    # Add bulk archive button
                    if include_interactive:
                        invite_ids = ','.join([i.message_id for i in new_only])
                        header_blocks.append({
                            "type": "actions",
                            "block_id": "new_invites_bulk",
                            "elements": [{
                                "type": "button",
                                "text": {"type": "plain_text", "text": "✅ Archive All New Invites"},
                                "action_id": "archive_all_invites",
                                "value": invite_ids,
                                "style": "primary"
                            }]
                        })

                    # Send header with bulk action
                    self.slack_client.chat_postMessage(
                        channel=channel,
                        text="New Invites",
                        blocks=header_blocks
                    )

                    # Send each invite as a SEPARATE message
                    for invite in new_only:
                        self.slack_client.chat_postMessage(
                            channel=channel,
                            text=f"Invite: {invite.subject}",
                            blocks=self._create_calendar_invite_block(invite, include_interactive)
                        )

            # ===== SECTION 3: Updated Invites =====
            if updated_invites:
                updated_only = [i for i in updated_invites if not (i.has_comments and i.comments)]
                if updated_only:
                    # Send header
                    self.slack_client.chat_postMessage(
                        channel=channel,
                        text="Updated Invites",
                        blocks=[
                            {
                                "type": "header",
                                "text": {"type": "plain_text", "text": "🔄 Updated Invites"}
                            },
                            {
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": f"*{len(updated_only)} invites* have been modified. Check for time, location, or attendee changes."}
                            }
                        ]
                    )

                    # Send each invite as a SEPARATE message
                    for invite in updated_only:
                        self.slack_client.chat_postMessage(
                            channel=channel,
                            text=f"Updated: {invite.subject}",
                            blocks=self._create_calendar_invite_block(invite, include_interactive)
                        )

            # ===== SECTION 4: Cancelled Invites =====
            if cancelled:
                blocks = [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": "❌ Cancelled Meetings"}
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*{len(cancelled)} meetings* have been cancelled by the organizer. Archive these emails to clean up your inbox."}
                    }
                ]

                # Add bulk archive button for cancelled
                if include_interactive:
                    invite_ids = ','.join([i.message_id for i in cancelled])
                    blocks.append({
                        "type": "actions",
                        "block_id": "cancelled_bulk",
                        "elements": [{
                            "type": "button",
                            "text": {"type": "plain_text", "text": "🗑️ Archive All Cancelled"},
                            "action_id": "archive_all_invites",
                            "value": invite_ids,
                            "style": "danger"
                        }]
                    })

                blocks.append({"type": "divider"})

                # Show cancelled invites with date and organizer
                for invite in cancelled:
                    date_str = invite.event_start.strftime('%a, %b %d') if invite.event_start else 'No date'
                    time_str = invite.event_start.strftime('%I:%M %p') if invite.event_start and not invite.is_all_day else ''
                    gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{invite.message_id}"

                    invite_block = {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"~*{invite.subject}*~\n📅 {date_str} {time_str}\n👤 {invite.organizer}"
                        }
                    }
                    if include_interactive:
                        invite_block["accessory"] = {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Archive"},
                            "action_id": "archive_invite",
                            "value": invite.message_id
                        }
                    blocks.append(invite_block)

                self.slack_client.chat_postMessage(
                    channel=channel,
                    text="Cancelled Invites",
                    blocks=blocks
                )

            # Summary is already provided by section headers, no need for extra message

            print(f"✓ Sent {len(invites)} calendar invites to Slack")

        except SlackApiError as e:
            print(f"Error sending calendar invites to Slack: {e.response['error']}")

    def _create_calendar_invite_block(self, invite: CalendarInvite, include_interactive: bool = True, show_comments: bool = False) -> List[Dict]:
        """Create Slack blocks for a single calendar invite."""
        gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{invite.message_id}"

        # Date and time display
        if invite.event_start:
            date_str = invite.event_start.strftime('%a, %b %d, %Y')
            if invite.is_all_day:
                time_str = "All day"
            else:
                time_str = invite.event_start.strftime('%I:%M %p')
                if invite.event_end:
                    time_str += f" - {invite.event_end.strftime('%I:%M %p')}"
        else:
            date_str = "Date TBD"
            time_str = ""

        # Build the invite info with clear structure
        invite_text = f"*{invite.subject}*\n"
        invite_text += f"📅 {date_str}"
        if time_str:
            invite_text += f" • ⏰ {time_str}"
        invite_text += f"\n👤 {invite.organizer}"
        if invite.location:
            invite_text += f"\n📍 {invite.location}"
        if invite.recurrence:
            invite_text += f"\n🔁 Recurring"

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": invite_text
                }
            }
        ]

        # Show text comments/replies if this is a comments section
        if show_comments and invite.has_comments and invite.comments:
            comment_text = "💬 *Reply:*\n"
            for comment in invite.comments[:3]:  # Limit to 3 comments
                text = comment.get('text', '')[:200]
                if text:
                    comment_text += f">{text}\n"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": comment_text}
            })

        # Action buttons: Accept, Reject, Archive, Open
        if include_interactive:
            blocks.append({
                "type": "actions",
                "block_id": f"invite_actions_{invite.message_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Accept"},
                        "action_id": "accept_invite",
                        "value": invite.message_id,
                        "style": "primary"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Decline"},
                        "action_id": "decline_invite",
                        "value": invite.message_id,
                        "style": "danger"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "📁 Archive"},
                        "action_id": "archive_invite",
                        "value": invite.message_id
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "📧 Open"},
                        "url": gmail_link,
                        "action_id": "open_invite"
                    }
                ]
            })

        blocks.append({"type": "divider"})

        return blocks

    def process_calendar_invites(self, hours_back: int = 168) -> List[CalendarInvite]:
        """Fetch, parse, and send calendar invites to Slack."""
        from database import db
        import json

        invites = self.get_calendar_invites(hours_back)

        # Filter to only new/unprocessed invites
        new_invites = []
        for invite in invites:
            if not db.is_invite_processed(invite.message_id):
                new_invites.append(invite)
                # Save to database
                db.save_calendar_invite(
                    message_id=invite.message_id,
                    thread_id=invite.thread_id,
                    subject=invite.subject,
                    organizer=invite.organizer,
                    event_start=invite.event_start,
                    event_end=invite.event_end,
                    location=invite.location,
                    status=invite.status.value,
                    has_comments=invite.has_comments,
                    comments=json.dumps(invite.comments) if invite.comments else None
                )

        if new_invites:
            self.send_calendar_invites_to_slack(new_invites)

        return new_invites

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
