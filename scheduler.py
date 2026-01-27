"""
Scheduler module for Gmail Action Router
Handles automated tasks: daily digests, snooze reminders, weekly summaries.
"""

from datetime import datetime, timedelta
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import Config
from database import db
from gmail_action_router import GmailActionRouter, Priority


class EmailScheduler:
    """Scheduler for automated email tasks."""

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.router: Optional[GmailActionRouter] = None
        self.slack_client = None
        self.calendar = None

    def initialize(self, router: GmailActionRouter = None):
        """Initialize scheduler with router and services."""
        if router:
            self.router = router
        else:
            self.router = GmailActionRouter()
            self.router.authenticate_gmail()

        # Initialize Slack client
        if Config.SLACK_BOT_TOKEN:
            from slack_sdk import WebClient
            self.slack_client = WebClient(token=Config.SLACK_BOT_TOKEN)

        # Initialize calendar if available
        try:
            from calendar_integration import calendar
            if calendar.authenticate():
                self.calendar = calendar
        except Exception:
            pass

        return self

    def setup_jobs(self):
        """Configure all scheduled jobs."""

        # Daily digest - weekdays at configured time
        self.scheduler.add_job(
            self.send_daily_digest,
            CronTrigger(
                hour=Config.DAILY_DIGEST_HOUR,
                minute=Config.DAILY_DIGEST_MINUTE,
                day_of_week='mon-fri'
            ),
            id='daily_digest',
            name='Daily Email Digest',
            replace_existing=True
        )

        # Weekly summary - Monday mornings
        self.scheduler.add_job(
            self.send_weekly_summary,
            CronTrigger(
                hour=Config.DAILY_DIGEST_HOUR,
                minute=Config.DAILY_DIGEST_MINUTE,
                day_of_week='mon'
            ),
            id='weekly_summary',
            name='Weekly Email Summary',
            replace_existing=True
        )

        # Snooze checker - every 15 minutes
        self.scheduler.add_job(
            self.check_snoozes,
            IntervalTrigger(minutes=Config.SNOOZE_CHECK_INTERVAL_MINUTES),
            id='snooze_checker',
            name='Snooze Reminder Checker',
            replace_existing=True
        )

        # Delegation reminder - daily at 9 AM
        self.scheduler.add_job(
            self.check_delegation_reminders,
            CronTrigger(hour=9, minute=0, day_of_week='mon-fri'),
            id='delegation_reminder',
            name='Delegation Status Reminder',
            replace_existing=True
        )

        # Family announcements summary - daily at 8 PM
        self.scheduler.add_job(
            self.send_family_summary,
            CronTrigger(
                hour=Config.FAMILY_SUMMARY_HOUR,
                minute=Config.FAMILY_SUMMARY_MINUTE
            ),
            id='family_summary',
            name='Family Announcements Summary',
            replace_existing=True
        )

        print(f"Scheduled jobs:")
        for job in self.scheduler.get_jobs():
            print(f"  - {job.name}: {job.trigger}")

    def start(self):
        """Start the scheduler."""
        if not self.scheduler.running:
            self.scheduler.start()
            print("Scheduler started")

    def shutdown(self):
        """Shutdown the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            print("Scheduler stopped")

    def send_daily_digest(self):
        """Send the daily email digest to Slack."""
        if not self.slack_client or not self.router:
            return

        try:
            print(f"[{datetime.now()}] Generating daily digest...")

            # Fetch recent emails (last 24 hours)
            messages = self.router.get_unread_emails(hours_back=24)

            if not messages:
                self.slack_client.chat_postMessage(
                    channel=Config.SLACK_CHANNEL,
                    text="*Good morning!* No new unread emails in the last 24 hours."
                )
                return

            # Parse emails
            actions = []
            for msg in messages:
                action = self.router.parse_email(msg)
                if action:
                    actions.append(action)

            # Group by priority
            by_priority = {}
            for action in actions:
                if action.priority not in by_priority:
                    by_priority[action.priority] = []
                by_priority[action.priority].append(action)

            # Build digest message
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"Good morning! Your Daily Email Digest"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{len(actions)} emails* need your attention"
                    }
                }
            ]

            # Priority breakdown
            priority_text = []
            for priority in [Priority.URGENT, Priority.HIGH, Priority.MEDIUM, Priority.LOW]:
                if priority in by_priority:
                    count = len(by_priority[priority])
                    priority_text.append(f"{priority.value}: {count}")

            if priority_text:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": '\n'.join(priority_text)
                    }
                })

            # Add AI summary if enabled
            if Config.is_ai_enabled():
                try:
                    from ai_assistant import ai_assistant
                    summary = ai_assistant.generate_daily_digest_summary(actions)
                    if summary:
                        blocks.append({
                            "type": "context",
                            "elements": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"_AI Summary: {summary}_"
                                }
                            ]
                        })
                except Exception:
                    pass

            # Add calendar summary if available
            if self.calendar:
                calendar_summary = self.calendar.get_calendar_summary_for_digest()
                if calendar_summary:
                    blocks.append({"type": "divider"})
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": calendar_summary
                        }
                    })

            # Add active snoozes reminder
            snoozes = db.get_active_snoozes()
            if snoozes:
                blocks.append({"type": "divider"})
                snooze_text = f"*{len(snoozes)} snoozed emails* will resurface today or later"
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": snooze_text}
                })

            # Post digest
            self.slack_client.chat_postMessage(
                channel=Config.SLACK_CHANNEL,
                text="Daily Email Digest",
                blocks=blocks
            )

            # Send detailed messages for urgent items
            if Priority.URGENT in by_priority:
                for action in by_priority[Priority.URGENT]:
                    email_blocks = self.router._create_slack_blocks(
                        action,
                        include_interactive=Config.is_slack_interactive_enabled()
                    )
                    self.slack_client.chat_postMessage(
                        channel=Config.SLACK_CHANNEL,
                        text=f"URGENT: {action.subject}",
                        blocks=email_blocks
                    )

            print(f"[{datetime.now()}] Daily digest sent: {len(actions)} emails")

        except Exception as e:
            print(f"Error sending daily digest: {e}")

    def send_weekly_summary(self):
        """Send weekly email summary."""
        if not self.slack_client:
            return

        try:
            print(f"[{datetime.now()}] Generating weekly summary...")

            # Get stats
            stats = db.get_stats()

            # Get pending delegations
            delegations = db.get_pending_delegations()

            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "Weekly Email Summary"
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Emails processed:*\n{stats['processed_emails']}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Active snoozes:*\n{stats['active_snoozes']}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Pending delegations:*\n{stats['pending_delegations']}"
                        }
                    ]
                }
            ]

            # List pending delegations
            if delegations:
                blocks.append({"type": "divider"})
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Pending Delegations:*"
                    }
                })
                for d in delegations[:5]:  # Limit to 5
                    due_text = ""
                    if d['due_date']:
                        due = datetime.fromisoformat(d['due_date'])
                        due_text = f" (due {due.strftime('%b %d')})"
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"• {d['subject'][:50]}... → <@{d['delegated_to']}>{due_text}"
                        }
                    })

            self.slack_client.chat_postMessage(
                channel=Config.SLACK_CHANNEL,
                text="Weekly Email Summary",
                blocks=blocks
            )

            print(f"[{datetime.now()}] Weekly summary sent")

        except Exception as e:
            print(f"Error sending weekly summary: {e}")

    def check_snoozes(self):
        """Check for due snoozes and send reminders."""
        if not self.slack_client or not self.router:
            return

        try:
            due_snoozes = db.get_due_snoozes()

            for snooze in due_snoozes:
                try:
                    # Move email back to inbox in Gmail
                    self.router.move_to_inbox(snooze['message_id'])

                    # Build reminder message
                    gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{snooze['message_id']}"

                    blocks = [
                        {
                            "type": "header",
                            "text": {
                                "type": "plain_text",
                                "text": "Snoozed Email Reminder"
                            }
                        },
                        {
                            "type": "section",
                            "fields": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"*Subject:*\n{snooze['subject']}"
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": f"*From:*\n{snooze['sender']}"
                                }
                            ]
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Preview:*\n{snooze['snippet'][:200]}..."
                            }
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "Open Email"},
                                    "url": gmail_link,
                                    "style": "primary"
                                },
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "Snooze Again"},
                                    "action_id": "snooze_email",
                                    "value": snooze['message_id']
                                }
                            ]
                        }
                    ]

                    self.slack_client.chat_postMessage(
                        channel=Config.SLACK_CHANNEL,
                        text=f"Reminder: {snooze['subject']}",
                        blocks=blocks
                    )

                    # Mark as reminded
                    db.mark_snooze_reminded(snooze['message_id'])

                    print(f"Sent snooze reminder for: {snooze['subject'][:30]}...")

                except Exception as e:
                    print(f"Error sending snooze reminder: {e}")

        except Exception as e:
            print(f"Error checking snoozes: {e}")

    def check_delegation_reminders(self):
        """Send reminders for pending delegations that are due soon."""
        if not self.slack_client:
            return

        try:
            delegations = db.get_pending_delegations()
            today = datetime.now().date()

            for d in delegations:
                if not d['due_date']:
                    continue

                due = datetime.fromisoformat(d['due_date']).date()

                # Send reminder if due today or overdue
                if due <= today:
                    try:
                        status = "DUE TODAY" if due == today else "OVERDUE"

                        self.slack_client.chat_postMessage(
                            channel=d['delegated_to'],  # DM the assignee
                            text=f"{status}: {d['subject']}",
                            blocks=[
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": f"*{status}* - Delegated task reminder"
                                    }
                                },
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": f"*{d['subject']}*\n_{d['notes'] or 'No notes'}_"
                                    }
                                },
                                {
                                    "type": "actions",
                                    "elements": [
                                        {
                                            "type": "button",
                                            "text": {"type": "plain_text", "text": "Open Email"},
                                            "url": f"https://mail.google.com/mail/u/0/#inbox/{d['message_id']}",
                                            "style": "primary"
                                        }
                                    ]
                                }
                            ]
                        )
                    except Exception as e:
                        print(f"Error sending delegation reminder: {e}")

        except Exception as e:
            print(f"Error checking delegations: {e}")

    def send_family_summary(self):
        """Send daily family announcements summary."""
        if not self.slack_client or not self.router:
            return

        try:
            print(f"[{datetime.now()}] Generating family summary...")

            days = Config.FAMILY_SUMMARY_DAYS
            family_senders = Config.FAMILY_SENDERS

            if not family_senders:
                print("No family senders configured")
                return

            # Build query for family senders
            sender_queries = ' OR '.join([f'from:{sender}' for sender in family_senders])
            hours_back = days * 24

            after_date = datetime.now() - timedelta(hours=hours_back)
            after_timestamp = int(after_date.timestamp())
            query = f'({sender_queries}) after:{after_timestamp}'

            # Fetch emails
            results = self.router.gmail_service.users().messages().list(
                userId='me',
                q=query,
                maxResults=50
            ).execute()

            messages = results.get('messages', [])

            if not messages:
                self.slack_client.chat_postMessage(
                    channel=Config.SLACK_CHANNEL,
                    text=f"👨‍👩‍👧‍👦 No family announcements in the last {days} day(s)."
                )
                return

            # Get full details for each email
            emails_data = []
            for msg in messages:
                msg_detail = self.router.gmail_service.users().messages().get(
                    userId='me',
                    id=msg['id'],
                    format='full'
                ).execute()

                headers = msg_detail.get('payload', {}).get('headers', [])
                subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
                sender = next((h['value'] for h in headers if h['name'] == 'From'), '')
                date_str = next((h['value'] for h in headers if h['name'] == 'Date'), '')

                body = self.router.get_email_body(msg_detail)[:2000]

                emails_data.append({
                    'message_id': msg['id'],
                    'subject': subject,
                    'sender': sender,
                    'date': date_str,
                    'body': body,
                    'snippet': msg_detail.get('snippet', '')
                })

            # Send header
            self.slack_client.chat_postMessage(
                channel=Config.SLACK_CHANNEL,
                text="Family Announcements Summary",
                blocks=[
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": "👨‍👩‍👧‍👦 Family Announcements Summary"}
                    },
                    {
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": f"_{len(emails_data)} emails from today_"}]
                    }
                ]
            )

            # Generate and send AI summary if available
            if Config.is_ai_enabled():
                self._send_family_sections(emails_data)
            else:
                # Basic list without AI
                for email in emails_data[:10]:
                    gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{email['message_id']}"
                    sender_name = email['sender'].split('<')[0].strip().strip('"') if '<' in email['sender'] else email['sender']
                    self.slack_client.chat_postMessage(
                        channel=Config.SLACK_CHANNEL,
                        text=email['subject'],
                        blocks=[
                            {
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": f"*{email['subject'][:60]}*\n_{sender_name}_"},
                                "accessory": {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "Open"},
                                    "url": gmail_link
                                }
                            }
                        ]
                    )

            print(f"[{datetime.now()}] Family summary sent: {len(emails_data)} emails")

        except Exception as e:
            print(f"Error sending family summary: {e}")

    def _send_family_sections(self, emails_data):
        """Generate AI summary and send sections to Slack."""
        import json
        from anthropic import Anthropic

        try:
            client = Anthropic(api_key=Config.ANTHROPIC_API_KEY)

            # Prepare email content with index references
            email_texts = []
            for i, email in enumerate(emails_data):
                email_texts.append(f"""
[EMAIL_{i}]
From: {email['sender']}
Subject: {email['subject']}
Content: {email['body'][:1500]}
---""")

            prompt = f"""Analyze these family/school emails and categorize the important information.

Return a JSON object with these sections. Each item should reference which email it came from using the EMAIL_X index.

{{
  "action_required": [{{"text": "description", "email_index": 0}}],
  "upcoming_dates": [{{"text": "description", "email_index": 1}}],
  "important_changes": [{{"text": "description", "email_index": 2}}],
  "announcements": [{{"text": "description", "email_index": 3}}]
}}

Rules:
- Only include items actually mentioned in the emails
- Be concise but specific (include dates, deadlines when mentioned)
- If a section has no items, use an empty array []
- email_index must match the EMAIL_X number from the source email

Emails:
{''.join(email_texts)}

Return ONLY the JSON object."""

            response = client.messages.create(
                model=Config.AI_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )

            # Parse JSON
            response_text = response.content[0].text.strip()
            if response_text.startswith('```'):
                response_text = response_text.split('```')[1]
                if response_text.startswith('json'):
                    response_text = response_text[4:]
            sections = json.loads(response_text)

            # Send each section
            section_config = [
                ("action_required", "🚨 Action Required"),
                ("upcoming_dates", "📅 Upcoming Dates"),
                ("important_changes", "⚠️ Important Changes"),
                ("announcements", "📢 Announcements"),
            ]

            for section_key, section_title in section_config:
                items = sections.get(section_key, [])
                if not items:
                    continue

                bullet_text = ""
                email_indices = []
                for item in items:
                    bullet_text += f"• {item.get('text', '')}\n"
                    idx = item.get('email_index')
                    if idx is not None and idx not in email_indices:
                        email_indices.append(idx)

                self.slack_client.chat_postMessage(
                    channel=Config.SLACK_CHANNEL,
                    text=section_title,
                    blocks=[
                        {"type": "header", "text": {"type": "plain_text", "text": section_title}},
                        {"type": "section", "text": {"type": "mrkdwn", "text": bullet_text}},
                        {"type": "divider"}
                    ]
                )

                for idx in email_indices:
                    if idx < len(emails_data):
                        email = emails_data[idx]
                        gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{email['message_id']}"
                        sender_name = email['sender'].split('<')[0].strip().strip('"') if '<' in email['sender'] else email['sender']
                        self.slack_client.chat_postMessage(
                            channel=Config.SLACK_CHANNEL,
                            text=email['subject'],
                            blocks=[
                                {
                                    "type": "section",
                                    "text": {"type": "mrkdwn", "text": f"*{email['subject'][:60]}*\n_{sender_name}_"},
                                    "accessory": {"type": "button", "text": {"type": "plain_text", "text": "Open"}, "url": gmail_link}
                                }
                            ]
                        )

        except Exception as e:
            print(f"Error generating AI family summary: {e}")

    def run_now(self, job_id: str):
        """Manually trigger a scheduled job."""
        job = self.scheduler.get_job(job_id)
        if job:
            job.func()
            print(f"Manually ran job: {job_id}")
        else:
            print(f"Job not found: {job_id}")


# Singleton instance
email_scheduler = EmailScheduler()


def run_scheduler():
    """Run the scheduler as a standalone process."""
    scheduler = EmailScheduler()
    scheduler.initialize()
    scheduler.setup_jobs()
    scheduler.start()

    print("\nScheduler running. Press Ctrl+C to stop.\n")

    try:
        # Keep the main thread alive
        import time
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == '__main__':
    run_scheduler()
