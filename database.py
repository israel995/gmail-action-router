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

            # CEO Co-pilot: VIP contacts tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS vip_contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT,
                    slack_user_id TEXT,
                    role TEXT,
                    max_silence_hours INTEGER DEFAULT 48,
                    last_contact_at TIMESTAMP,
                    last_contact_channel TEXT,
                    last_response_at TIMESTAMP,
                    notes TEXT,
                    active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # CEO Co-pilot: Projects tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    notion_page_id TEXT,
                    notion_page_url TEXT,
                    status TEXT DEFAULT 'active',
                    keywords TEXT,
                    owner TEXT,
                    max_silence_hours INTEGER DEFAULT 72,
                    last_activity_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # CEO Co-pilot: Cross-platform communication log
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS communication_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contact_name TEXT,
                    contact_email TEXT,
                    project_name TEXT,
                    channel TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    message_id TEXT,
                    subject TEXT,
                    snippet TEXT,
                    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # CEO Co-pilot: Action items (from email, Slack, Notion)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS action_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT,
                    source TEXT DEFAULT 'manual',
                    source_id TEXT,
                    project_name TEXT,
                    assigned_to TEXT,
                    due_date TIMESTAMP,
                    status TEXT DEFAULT 'open',
                    priority TEXT DEFAULT 'medium',
                    notion_task_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create indexes for common queries
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_snoozed_remind_at ON snoozed_emails(remind_at)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_snoozed_reminded ON snoozed_emails(reminded)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_delegated_status ON delegated_emails(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_processed_message ON processed_emails(message_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_vip_email ON vip_contacts(email)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_comm_log_contact ON communication_log(contact_name)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_comm_log_project ON communication_log(project_name)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_action_items_status ON action_items(status)')

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

    # ─── CEO Co-pilot Methods ───────────────────────────────────────────────────

    # VIP Contacts
    def upsert_vip_contact(self, name: str, email: str = None, slack_user_id: str = None,
                           role: str = None, max_silence_hours: int = 48,
                           notes: str = None) -> int:
        """Add or update a VIP contact."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO vip_contacts (name, email, slack_user_id, role, max_silence_hours, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    email=excluded.email, slack_user_id=excluded.slack_user_id,
                    role=excluded.role, max_silence_hours=excluded.max_silence_hours,
                    notes=excluded.notes
            ''', (name, email, slack_user_id, role, max_silence_hours, notes))
            # Use INSERT OR IGNORE + UPDATE pattern since name isn't UNIQUE
            conn.commit()
            cursor.execute('SELECT id FROM vip_contacts WHERE name = ? AND active = 1', (name,))
            row = cursor.fetchone()
            return row['id'] if row else cursor.lastrowid

    def add_vip_contact(self, name: str, email: str = None, slack_user_id: str = None,
                        role: str = None, max_silence_hours: int = 48, notes: str = None) -> int:
        """Add a new VIP contact."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO vip_contacts
                (name, email, slack_user_id, role, max_silence_hours, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, email, slack_user_id, role, max_silence_hours, notes))
            return cursor.lastrowid

    def get_all_vip_contacts(self) -> List[Dict]:
        """Get all active VIP contacts."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM vip_contacts WHERE active = 1 ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]

    def get_vip_contact_by_email(self, email: str) -> Optional[Dict]:
        """Find a VIP contact by email address."""
        if not email:
            return None
        with self._get_connection() as conn:
            cursor = conn.cursor()
            email_lower = email.lower().strip()
            cursor.execute('''
                SELECT * FROM vip_contacts
                WHERE active = 1 AND (
                    LOWER(email) = ? OR
                    LOWER(email) LIKE ? OR
                    INSTR(LOWER(email), ?) > 0
                )
                LIMIT 1
            ''', (email_lower, f'%{email_lower}%', email_lower))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_vip_contact_by_name(self, name: str) -> Optional[Dict]:
        """Find a VIP contact by name (case-insensitive partial match)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM vip_contacts
                WHERE active = 1 AND LOWER(name) LIKE ?
                LIMIT 1
            ''', (f'%{name.lower()}%',))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_vip_last_contact(self, contact_id: int, channel: str, direction: str = 'sent'):
        """Update last contact timestamp for a VIP contact."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now()
            if direction == 'sent':
                cursor.execute('''
                    UPDATE vip_contacts
                    SET last_contact_at = ?, last_contact_channel = ?
                    WHERE id = ?
                ''', (now, channel, contact_id))
            else:
                cursor.execute('''
                    UPDATE vip_contacts
                    SET last_response_at = ?, last_contact_at = ?, last_contact_channel = ?
                    WHERE id = ?
                ''', (now, now, channel, contact_id))

    def get_overdue_vip_contacts(self) -> List[Dict]:
        """Get VIP contacts who haven't been contacted within their threshold."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT *,
                    CAST((julianday('now') - julianday(COALESCE(last_contact_at, created_at))) * 24 AS INTEGER)
                    AS hours_since_contact
                FROM vip_contacts
                WHERE active = 1 AND (
                    last_contact_at IS NULL OR
                    (julianday('now') - julianday(last_contact_at)) * 24 >= max_silence_hours
                )
                ORDER BY hours_since_contact DESC
            ''')
            return [dict(row) for row in cursor.fetchall()]

    def remove_vip_contact(self, contact_id: int):
        """Deactivate a VIP contact."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE vip_contacts SET active = 0 WHERE id = ?', (contact_id,))

    # Projects
    def add_project(self, name: str, description: str = None, notion_page_id: str = None,
                    notion_page_url: str = None, keywords: str = None,
                    owner: str = None, max_silence_hours: int = 72) -> int:
        """Add a new project to track."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO projects
                (name, description, notion_page_id, notion_page_url, keywords, owner, max_silence_hours)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (name, description, notion_page_id, notion_page_url, keywords, owner, max_silence_hours))
            return cursor.lastrowid

    def get_all_projects(self, status: str = 'active') -> List[Dict]:
        """Get all projects, optionally filtered by status."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute('SELECT * FROM projects WHERE status = ? ORDER BY name', (status,))
            else:
                cursor.execute('SELECT * FROM projects ORDER BY status, name')
            return [dict(row) for row in cursor.fetchall()]

    def get_project_by_name(self, name: str) -> Optional[Dict]:
        """Find a project by name."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM projects WHERE LOWER(name) LIKE ?', (f'%{name.lower()}%',))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_project_activity(self, project_name: str):
        """Update last activity timestamp for a project."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE projects SET last_activity_at = ? WHERE name = ?
            ''', (datetime.now(), project_name))

    def get_stalled_projects(self) -> List[Dict]:
        """Get projects with no recent activity beyond their silence threshold."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT *,
                    CAST((julianday('now') - julianday(COALESCE(last_activity_at, created_at))) * 24 AS INTEGER)
                    AS hours_since_activity
                FROM projects
                WHERE status = 'active' AND (
                    last_activity_at IS NULL OR
                    (julianday('now') - julianday(last_activity_at)) * 24 >= max_silence_hours
                )
                ORDER BY hours_since_activity DESC
            ''')
            return [dict(row) for row in cursor.fetchall()]

    def update_project_status(self, project_name: str, status: str):
        """Update project status."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE projects SET status = ? WHERE name = ?', (status, project_name))

    # Communication Log
    def log_communication(self, channel: str, direction: str, contact_name: str = None,
                          contact_email: str = None, project_name: str = None,
                          message_id: str = None, subject: str = None,
                          snippet: str = None) -> int:
        """Log a communication event."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO communication_log
                (contact_name, contact_email, project_name, channel, direction,
                 message_id, subject, snippet)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (contact_name, contact_email, project_name, channel, direction,
                  message_id, subject, snippet))
            return cursor.lastrowid

    def get_recent_communications(self, contact_name: str = None, project_name: str = None,
                                  hours: int = 72) -> List[Dict]:
        """Get recent communications, optionally filtered."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            since = datetime.now() - __import__('datetime').timedelta(hours=hours)
            if contact_name and project_name:
                cursor.execute('''
                    SELECT * FROM communication_log
                    WHERE logged_at >= ? AND (contact_name LIKE ? OR project_name LIKE ?)
                    ORDER BY logged_at DESC
                ''', (since, f'%{contact_name}%', f'%{project_name}%'))
            elif contact_name:
                cursor.execute('''
                    SELECT * FROM communication_log
                    WHERE logged_at >= ? AND contact_name LIKE ?
                    ORDER BY logged_at DESC
                ''', (since, f'%{contact_name}%'))
            elif project_name:
                cursor.execute('''
                    SELECT * FROM communication_log
                    WHERE logged_at >= ? AND project_name LIKE ?
                    ORDER BY logged_at DESC
                ''', (since, f'%{project_name}%'))
            else:
                cursor.execute('''
                    SELECT * FROM communication_log
                    WHERE logged_at >= ?
                    ORDER BY logged_at DESC
                ''', (since,))
            return [dict(row) for row in cursor.fetchall()]

    # Action Items
    def add_action_item(self, title: str, description: str = None, source: str = 'manual',
                        source_id: str = None, project_name: str = None,
                        assigned_to: str = None, due_date: datetime = None,
                        priority: str = 'medium', notion_task_id: str = None) -> int:
        """Add a new action item."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO action_items
                (title, description, source, source_id, project_name, assigned_to,
                 due_date, priority, notion_task_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (title, description, source, source_id, project_name, assigned_to,
                  due_date, priority, notion_task_id))
            return cursor.lastrowid

    def get_open_action_items(self, assigned_to: str = None, project_name: str = None) -> List[Dict]:
        """Get open action items, optionally filtered."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            base = "SELECT * FROM action_items WHERE status IN ('open', 'in_progress')"
            params = []
            if assigned_to:
                base += ' AND LOWER(assigned_to) LIKE ?'
                params.append(f'%{assigned_to.lower()}%')
            if project_name:
                base += ' AND LOWER(project_name) LIKE ?'
                params.append(f'%{project_name.lower()}%')
            base += ' ORDER BY CASE priority WHEN "urgent" THEN 1 WHEN "high" THEN 2 WHEN "medium" THEN 3 ELSE 4 END, due_date ASC'
            cursor.execute(base, params)
            return [dict(row) for row in cursor.fetchall()]

    def update_action_item_status(self, item_id: int, status: str):
        """Update an action item's status."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE action_items SET status = ?, updated_at = ? WHERE id = ?
            ''', (status, datetime.now(), item_id))

    def get_overdue_action_items(self) -> List[Dict]:
        """Get action items that are past their due date."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM action_items
                WHERE status IN ('open', 'in_progress')
                AND due_date IS NOT NULL
                AND due_date < ?
                ORDER BY due_date ASC
            ''', (datetime.now(),))
            return [dict(row) for row in cursor.fetchall()]

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
