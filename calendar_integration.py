"""
Google Calendar Integration for Gmail Action Router
Provides calendar awareness for email processing and digest generation.
"""

import os
import pickle
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import Config


class CalendarIntegration:
    """Google Calendar API integration for the email agent."""

    SCOPES = Config.CALENDAR_SCOPES
    TOKEN_FILE = 'calendar_token.pickle'

    def __init__(self):
        self.service = None
        self.enabled = False

    def authenticate(self) -> bool:
        """Authenticate with Google Calendar API."""
        creds = None

        # Load existing token
        if os.path.exists(self.TOKEN_FILE):
            with open(self.TOKEN_FILE, 'rb') as token:
                creds = pickle.load(token)

        # Refresh or get new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    creds = None

            if not creds:
                if not os.path.exists('credentials.json'):
                    print("Warning: credentials.json not found. Calendar features disabled.")
                    return False

                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', self.SCOPES)
                creds = flow.run_local_server(port=0)

            # Save credentials
            with open(self.TOKEN_FILE, 'wb') as token:
                pickle.dump(creds, token)

        try:
            self.service = build('calendar', 'v3', credentials=creds)
            self.enabled = True
            print("Calendar authenticated successfully")
            return True
        except Exception as e:
            print(f"Calendar authentication failed: {e}")
            return False

    def get_todays_events(self) -> List[Dict]:
        """Get all events for today."""
        if not self.enabled:
            return []

        try:
            now = datetime.now()
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + timedelta(days=1)

            events_result = self.service.events().list(
                calendarId='primary',
                timeMin=start_of_day.isoformat() + 'Z',
                timeMax=end_of_day.isoformat() + 'Z',
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            return events_result.get('items', [])

        except HttpError as e:
            print(f"Error fetching today's events: {e}")
            return []

    def get_upcoming_events(self, days: int = 7, max_results: int = 20) -> List[Dict]:
        """Get upcoming events for the next N days."""
        if not self.enabled:
            return []

        try:
            now = datetime.now()
            end_date = now + timedelta(days=days)

            events_result = self.service.events().list(
                calendarId='primary',
                timeMin=now.isoformat() + 'Z',
                timeMax=end_date.isoformat() + 'Z',
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            return events_result.get('items', [])

        except HttpError as e:
            print(f"Error fetching upcoming events: {e}")
            return []

    def get_free_busy(self, start: datetime, end: datetime) -> List[Dict]:
        """Get free/busy information for a time range."""
        if not self.enabled:
            return []

        try:
            body = {
                "timeMin": start.isoformat() + 'Z',
                "timeMax": end.isoformat() + 'Z',
                "items": [{"id": "primary"}]
            }

            result = self.service.freebusy().query(body=body).execute()
            return result.get('calendars', {}).get('primary', {}).get('busy', [])

        except HttpError as e:
            print(f"Error fetching free/busy: {e}")
            return []

    def create_event(self, summary: str, start: datetime, end: datetime,
                     description: str = None, attendees: List[str] = None,
                     location: str = None) -> Optional[Dict]:
        """Create a new calendar event."""
        if not self.enabled:
            return None

        try:
            event = {
                'summary': summary,
                'start': {
                    'dateTime': start.isoformat(),
                    'timeZone': 'America/Los_Angeles',  # TODO: Make configurable
                },
                'end': {
                    'dateTime': end.isoformat(),
                    'timeZone': 'America/Los_Angeles',
                },
            }

            if description:
                event['description'] = description
            if location:
                event['location'] = location
            if attendees:
                event['attendees'] = [{'email': email} for email in attendees]

            created_event = self.service.events().insert(
                calendarId='primary',
                body=event
            ).execute()

            print(f"Created calendar event: {created_event.get('htmlLink')}")
            return created_event

        except HttpError as e:
            print(f"Error creating event: {e}")
            return None

    def format_event_for_slack(self, event: Dict) -> str:
        """Format a calendar event for Slack display."""
        summary = event.get('summary', 'No title')
        start = event.get('start', {})
        location = event.get('location', '')

        # Handle all-day events vs timed events
        if 'dateTime' in start:
            start_dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
            time_str = start_dt.strftime('%I:%M %p')
        else:
            time_str = 'All day'

        location_str = f" @ {location}" if location else ""
        return f"• {time_str}: {summary}{location_str}"

    def get_calendar_summary_for_digest(self) -> str:
        """Generate a calendar summary for the daily digest."""
        if not self.enabled:
            return ""

        events = self.get_todays_events()

        if not events:
            return "*Today's Calendar:* No events scheduled"

        lines = ["*Today's Calendar:*"]
        for event in events:
            lines.append(self.format_event_for_slack(event))

        return '\n'.join(lines)

    def detect_calendar_email(self, subject: str, body: str) -> Dict:
        """
        Detect if an email is calendar-related and extract details.

        Returns dict with:
            - is_calendar: bool
            - event_type: 'invite', 'update', 'cancel', 'reminder', None
            - suggested_action: str
        """
        text = (subject + ' ' + body).lower()

        result = {
            'is_calendar': False,
            'event_type': None,
            'suggested_action': None
        }

        # Calendar invite indicators
        invite_keywords = ['you have been invited', 'invitation:', 'meeting request',
                          'calendar invite', 'join meeting', 'rsvp']
        update_keywords = ['meeting updated', 'event changed', 'rescheduled',
                          'new time:', 'time changed']
        cancel_keywords = ['meeting cancelled', 'event canceled', 'no longer happening',
                          'meeting has been cancelled']
        reminder_keywords = ['reminder:', 'starting soon', 'upcoming meeting',
                            'don\'t forget', 'event reminder']

        if any(kw in text for kw in cancel_keywords):
            result['is_calendar'] = True
            result['event_type'] = 'cancel'
            result['suggested_action'] = 'Update your calendar - event cancelled'
        elif any(kw in text for kw in update_keywords):
            result['is_calendar'] = True
            result['event_type'] = 'update'
            result['suggested_action'] = 'Review calendar changes'
        elif any(kw in text for kw in invite_keywords):
            result['is_calendar'] = True
            result['event_type'] = 'invite'
            result['suggested_action'] = 'Accept or decline invitation'
        elif any(kw in text for kw in reminder_keywords):
            result['is_calendar'] = True
            result['event_type'] = 'reminder'
            result['suggested_action'] = 'Prepare for upcoming event'

        return result

    def get_next_available_slot(self, duration_minutes: int = 30,
                                within_days: int = 7) -> Optional[Dict]:
        """
        Find the next available time slot for a meeting.

        Returns dict with 'start' and 'end' datetime objects, or None.
        """
        if not self.enabled:
            return None

        try:
            now = datetime.now()
            end_search = now + timedelta(days=within_days)

            # Get busy times
            busy_times = self.get_free_busy(now, end_search)

            # Find gaps - simple implementation for business hours (9 AM - 5 PM)
            current = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

            while current < end_search:
                # Skip weekends
                if current.weekday() >= 5:
                    current += timedelta(days=1)
                    current = current.replace(hour=9, minute=0)
                    continue

                # Skip outside business hours
                if current.hour < 9:
                    current = current.replace(hour=9, minute=0)
                    continue
                if current.hour >= 17:
                    current += timedelta(days=1)
                    current = current.replace(hour=9, minute=0)
                    continue

                slot_end = current + timedelta(minutes=duration_minutes)

                # Check if slot conflicts with busy times
                is_free = True
                for busy in busy_times:
                    busy_start = datetime.fromisoformat(busy['start'].replace('Z', '+00:00'))
                    busy_end = datetime.fromisoformat(busy['end'].replace('Z', '+00:00'))

                    # Remove timezone info for comparison
                    busy_start = busy_start.replace(tzinfo=None)
                    busy_end = busy_end.replace(tzinfo=None)

                    if not (slot_end <= busy_start or current >= busy_end):
                        is_free = False
                        break

                if is_free:
                    return {'start': current, 'end': slot_end}

                current += timedelta(minutes=30)

            return None

        except Exception as e:
            print(f"Error finding available slot: {e}")
            return None


    def respond_to_event(self, event_id: str, response: str) -> bool:
        """
        Respond to a calendar event (accept, decline, tentative).

        Args:
            event_id: The calendar event ID
            response: 'accepted', 'declined', or 'tentative'

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False

        try:
            # Get the event first
            event = self.service.events().get(
                calendarId='primary',
                eventId=event_id
            ).execute()

            # Find current user in attendees and update response
            # If user is the organizer, this won't work the same way
            attendees = event.get('attendees', [])
            for attendee in attendees:
                if attendee.get('self', False):
                    attendee['responseStatus'] = response
                    break

            # Update the event
            self.service.events().update(
                calendarId='primary',
                eventId=event_id,
                body=event
            ).execute()

            print(f"Responded '{response}' to event: {event.get('summary')}")
            return True

        except HttpError as e:
            print(f"Error responding to event: {e}")
            return False

    def find_event_by_subject_and_time(self, subject: str, event_start: datetime = None,
                                        time_window_hours: int = 24) -> Optional[str]:
        """
        Find a calendar event ID by matching subject and approximate time.

        Returns the event ID if found, None otherwise.
        """
        if not self.enabled:
            return None

        try:
            # Search in a time window around the event start
            if event_start:
                time_min = event_start - timedelta(hours=1)
                time_max = event_start + timedelta(hours=time_window_hours)
            else:
                # If no start time, search next 30 days
                time_min = datetime.now()
                time_max = time_min + timedelta(days=30)

            events_result = self.service.events().list(
                calendarId='primary',
                timeMin=time_min.isoformat() + 'Z',
                timeMax=time_max.isoformat() + 'Z',
                singleEvents=True,
                orderBy='startTime',
                maxResults=50
            ).execute()

            events = events_result.get('items', [])

            # Try to match by subject (fuzzy match)
            subject_lower = subject.lower()
            # Remove common prefixes like "Invitation:", "Updated:", etc.
            for prefix in ['invitation:', 'updated invitation:', 'canceled:', 'accepted:', 'declined:']:
                if subject_lower.startswith(prefix):
                    subject_lower = subject_lower[len(prefix):].strip()
                    break

            for event in events:
                event_summary = event.get('summary', '').lower()
                # Check if the subjects match (account for slight variations)
                if subject_lower in event_summary or event_summary in subject_lower:
                    return event.get('id')

            return None

        except HttpError as e:
            print(f"Error finding event: {e}")
            return None

    def accept_event(self, subject: str, event_start: datetime = None) -> bool:
        """Accept a calendar invite by subject."""
        event_id = self.find_event_by_subject_and_time(subject, event_start)
        if event_id:
            return self.respond_to_event(event_id, 'accepted')
        return False

    def decline_event(self, subject: str, event_start: datetime = None) -> bool:
        """Decline a calendar invite by subject."""
        event_id = self.find_event_by_subject_and_time(subject, event_start)
        if event_id:
            return self.respond_to_event(event_id, 'declined')
        return False


# Singleton instance
calendar = CalendarIntegration()
