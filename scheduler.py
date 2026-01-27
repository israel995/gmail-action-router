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
