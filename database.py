"""
Database module for Gmail Action Router
SQLite-based state management for snoozes, delegations, and processed emails.
"""

import sqlite3
from datetime import datetime
from typing import List, Optional, Dict, Any
from contextlib import contextmanager

from config import Config


class Database:
    """SQLite database manager for the email agent."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or Config.DATABASE_PATH
        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Initialize database tables."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Processed emails tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS processed_emails (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT UNIQUE NOT NULL,
                    thread_id TEXT,
                    subject TEXT,
                    sender TEXT,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    action_taken TEXT,
                    slack_message_ts TEXT,
                    slack_channel TEXT
                )
            ''')

            # Snoozed emails
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS snoozed_emails (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT UNIQUE NOT NULL,
                    thread_id TEXT,
                    subject TEXT,
                    sender TEXT,
                    snippet TEXT,
                    priority TEXT,
                    snoozed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    remind_at TIMESTAMP NOT NULL,
                    reminded BOOLEAN DEFAULT FALSE,
                    slack_message_ts TEXT,
                    slack_channel TEXT
                )
            ''')

            # Delegated emails
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS delegated_emails (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    thread_id TEXT,
                    subject TEXT,
                    sender TEXT,
                    delegated_to TEXT NOT NULL,
                    delegated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    due_date TIMESTAMP,
                    status TEXT DEFAULT 'pending',
                    notes TEXT,
                    slack_message_ts TEXT,
                    slack_channel TEXT
                )
            ''')

            # Email summaries cache (for AI summaries)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS email_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT UNIQUE NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Calendar invites tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS calendar_invites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT UNIQUE NOT NULL,
                    thread_id TEXT,
                    subject TEXT,
                    organizer TEXT,
                    event_start TIMESTAMP,
                    event_end TIMESTAMP,
                    location TEXT,
                    status TEXT,
                    has_comments BOOLEAN DEFAULT FALSE,
                    comments TEXT,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    action_taken TEXT,
                    my_response TEXT DEFAULT 'needs_action'
                )
            ''')

            # Create indexes for common queries
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_snoozed_remind_at ON snoozed_emails(remind_at)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_snoozed_reminded ON snoozed_emails(reminded)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_delegated_status ON delegated_emails(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_processed_message ON processed_emails(message_id)')

    # Processed Emails Methods
    def mark_email_processed(self, message_id: str, thread_id: str = None,
                            subject: str = None, sender: str = None,
                            action_taken: str = None, slack_message_ts: str = None,
                            slack_channel: str = None) -> int:
        """Mark an email as processed."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO processed_emails
                (message_id, thread_id, subject, sender, action_taken, slack_message_ts, slack_channel)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (message_id, thread_id, subject, sender, action_taken, slack_message_ts, slack_channel))
            return cursor.lastrowid

    def is_email_processed(self, message_id: str) -> bool:
        """Check if an email has been processed."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM processed_emails WHERE message_id = ?', (message_id,))
            return cursor.fetchone() is not None

    def get_processed_email(self, message_id: str) -> Optional[Dict]:
        """Get processed email details."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM processed_emails WHERE message_id = ?', (message_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    # Snooze Methods
    def snooze_email(self, message_id: str, remind_at: datetime,
                    thread_id: str = None, subject: str = None,
                    sender: str = None, snippet: str = None,
                    priority: str = None, slack_message_ts: str = None,
                    slack_channel: str = None) -> int:
        """Snooze an email until a specific time."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO snoozed_emails
                (message_id, thread_id, subject, sender, snippet, priority, remind_at,
                 slack_message_ts, slack_channel, reminded)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE)
            ''', (message_id, thread_id, subject, sender, snippet, priority,
                  remind_at, slack_message_ts, slack_channel))
            return cursor.lastrowid

    def get_due_snoozes(self) -> List[Dict]:
        """Get all snoozes that are due for reminder."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM snoozed_emails
                WHERE reminded = FALSE AND remind_at <= ?
            ''', (datetime.now(),))
            return [dict(row) for row in cursor.fetchall()]

    def mark_snooze_reminded(self, message_id: str):
        """Mark a snooze as reminded."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE snoozed_emails SET reminded = TRUE WHERE message_id = ?
            ''', (message_id,))

    def cancel_snooze(self, message_id: str):
        """Cancel a snooze."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM snoozed_emails WHERE message_id = ?', (message_id,))

    def get_active_snoozes(self) -> List[Dict]:
        """Get all active (not yet reminded) snoozes."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM snoozed_emails
                WHERE reminded = FALSE
                ORDER BY remind_at ASC
            ''')
            return [dict(row) for row in cursor.fetchall()]

    # Delegation Methods
    def delegate_email(self, message_id: str, delegated_to: str,
                       thread_id: str = None, subject: str = None,
                       sender: str = None, due_date: datetime = None,
                       notes: str = None, slack_message_ts: str = None,
                       slack_channel: str = None) -> int:
        """Delegate an email to someone."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO delegated_emails
                (message_id, thread_id, subject, sender, delegated_to, due_date,
                 notes, slack_message_ts, slack_channel)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (message_id, thread_id, subject, sender, delegated_to,
                  due_date, notes, slack_message_ts, slack_channel))
            return cursor.lastrowid

    def update_delegation_status(self, delegation_id: int, status: str):
        """Update delegation status (pending, in_progress, completed, cancelled)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE delegated_emails SET status = ? WHERE id = ?
            ''', (status, delegation_id))

    def get_delegations_by_assignee(self, assignee: str, status: str = None) -> List[Dict]:
        """Get delegations for a specific assignee."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute('''
                    SELECT * FROM delegated_emails
                    WHERE delegated_to = ? AND status = ?
                    ORDER BY due_date ASC
                ''', (assignee, status))
            else:
                cursor.execute('''
                    SELECT * FROM delegated_emails
                    WHERE delegated_to = ?
                    ORDER BY due_date ASC
                ''', (assignee,))
            return [dict(row) for row in cursor.fetchall()]

    def get_pending_delegations(self) -> List[Dict]:
        """Get all pending delegations."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM delegated_emails
                WHERE status = 'pending'
                ORDER BY due_date ASC
            ''')
            return [dict(row) for row in cursor.fetchall()]

    def get_delegation_by_message(self, message_id: str) -> Optional[Dict]:
        """Get delegation by message ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM delegated_emails
                WHERE message_id = ?
                ORDER BY delegated_at DESC
                LIMIT 1
            ''', (message_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    # Email Summary Cache Methods
    def cache_summary(self, message_id: str, summary: str) -> int:
        """Cache an AI-generated email summary."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO email_summaries (message_id, summary)
                VALUES (?, ?)
            ''', (message_id, summary))
            return cursor.lastrowid

    def get_cached_summary(self, message_id: str) -> Optional[str]:
        """Get a cached summary for an email."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT summary FROM email_summaries WHERE message_id = ?', (message_id,))
            row = cursor.fetchone()
            return row['summary'] if row else None

    # Calendar Invite Methods
    def save_calendar_invite(self, message_id: str, thread_id: str = None,
                             subject: str = None, organizer: str = None,
                             event_start: datetime = None, event_end: datetime = None,
                             location: str = None, status: str = None,
                             has_comments: bool = False, comments: str = None,
                             my_response: str = 'needs_action') -> int:
        """Save or update a calendar invite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO calendar_invites
                (message_id, thread_id, subject, organizer, event_start, event_end,
                 location, status, has_comments, comments, my_response)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (message_id, thread_id, subject, organizer, event_start, event_end,
                  location, status, has_comments, comments, my_response))
            return cursor.lastrowid

    def get_calendar_invite(self, message_id: str) -> Optional[Dict]:
        """Get a calendar invite by message ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM calendar_invites WHERE message_id = ?', (message_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_pending_invites(self) -> List[Dict]:
        """Get all invites that need action."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM calendar_invites
                WHERE my_response = 'needs_action' AND action_taken IS NULL
                ORDER BY event_start ASC
            ''')
            return [dict(row) for row in cursor.fetchall()]

    def get_invites_with_comments(self) -> List[Dict]:
        """Get all invites that have comments."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM calendar_invites
                WHERE has_comments = TRUE
                ORDER BY event_start ASC
            ''')
            return [dict(row) for row in cursor.fetchall()]

    def update_invite_action(self, message_id: str, action_taken: str, my_response: str = None):
        """Update the action taken on an invite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if my_response:
                cursor.execute('''
                    UPDATE calendar_invites
                    SET action_taken = ?, my_response = ?
                    WHERE message_id = ?
                ''', (action_taken, my_response, message_id))
            else:
                cursor.execute('''
                    UPDATE calendar_invites SET action_taken = ? WHERE message_id = ?
                ''', (action_taken, message_id))

    def is_invite_processed(self, message_id: str) -> bool:
        """Check if an invite has already been processed."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM calendar_invites WHERE message_id = ?', (message_id,))
            return cursor.fetchone() is not None

    # Statistics Methods
    def get_stats(self) -> Dict[str, Any]:
        """Get overall statistics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('SELECT COUNT(*) as count FROM processed_emails')
            processed_count = cursor.fetchone()['count']

            cursor.execute('SELECT COUNT(*) as count FROM snoozed_emails WHERE reminded = FALSE')
            active_snoozes = cursor.fetchone()['count']

            cursor.execute('SELECT COUNT(*) as count FROM delegated_emails WHERE status = "pending"')
            pending_delegations = cursor.fetchone()['count']

            cursor.execute('SELECT COUNT(*) as count FROM email_summaries')
            cached_summaries = cursor.fetchone()['count']

            return {
                'processed_emails': processed_count,
                'active_snoozes': active_snoozes,
                'pending_delegations': pending_delegations,
                'cached_summaries': cached_summaries,
            }


# Singleton instance for easy import
db = Database()
