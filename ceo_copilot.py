"""
CEO Co-pilot Core
Tracks VIP contacts and projects across Gmail, Slack, and Notion.
Provides intelligent briefings and proactive alerts.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from config import Config
from database import db

logger = logging.getLogger(__name__)

# Emoji indicators
URGENCY_EMOJI = {0: ":white_circle:", 1: ":yellow_circle:", 2: ":orange_circle:", 3: ":red_circle:"}
STATUS_EMOJI = {"active": ":green_circle:", "paused": ":yellow_circle:", "completed": ":white_check_mark:"}
CHANNEL_EMOJI = {"email": ":email:", "slack": ":slack:", "notion": ":notebook:", "manual": ":pencil:"}


def _hours_ago(ts) -> Optional[int]:
    """Return how many hours ago a timestamp was, or None if None."""
    if not ts:
        return None
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return None
    delta = datetime.now() - ts
    return int(delta.total_seconds() / 3600)


def _format_hours(hours: Optional[int]) -> str:
    """Human-readable duration from hours."""
    if hours is None:
        return "never"
    if hours < 1:
        return "< 1 hour ago"
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    remaining = hours % 24
    if remaining:
        return f"{days}d {remaining}h ago"
    return f"{days}d ago"


def _urgency_level(hours: Optional[int], threshold: int) -> int:
    """0=ok, 1=watch, 2=warn, 3=urgent"""
    if hours is None:
        return 3
    ratio = hours / max(threshold, 1)
    if ratio >= 2.0:
        return 3
    if ratio >= 1.0:
        return 2
    if ratio >= 0.75:
        return 1
    return 0


class CEOCopilot:
    """
    Core CEO co-pilot engine.
    Maintains state about VIP contacts and projects,
    scans communications, and builds briefings.
    """

    def __init__(self):
        self._notion = None
        self._seed_from_config()

    def _seed_from_config(self):
        """Seed VIP contacts and projects from env vars, updating existing records."""
        existing = {c["email"]: c for c in db.get_all_vip_contacts() if c.get("email")}
        for contact in Config.parse_vip_contacts():
            email = contact["email"]
            if email not in existing:
                db.add_vip_contact(
                    name=contact["name"],
                    email=email,
                    role=contact.get("role", ""),
                    max_silence_hours=contact.get("max_silence_hours", Config.VIP_DEFAULT_SILENCE_HOURS),
                    slack_user_id=contact.get("slack_user_id"),
                    notion_page_url=contact.get("notion_page_url"),
                )
                logger.info(f"Seeded VIP contact: {contact['name']}")
            else:
                # Update Slack/Notion fields if they were empty and now have values
                row = existing[email]
                updates = {}
                if contact.get("slack_user_id") and not row.get("slack_user_id"):
                    updates["slack_user_id"] = contact["slack_user_id"]
                if contact.get("notion_page_url") and not row.get("notion_page_url"):
                    updates["notion_page_url"] = contact["notion_page_url"]
                if updates:
                    db.update_vip_contact(row["id"], **updates)
                    logger.info(f"Updated VIP contact: {contact['name']} {list(updates.keys())}")

        existing_projects = {p["name"] for p in db.get_all_projects(status=None)}
        for proj in Config.parse_tracked_projects():
            if proj["name"] not in existing_projects:
                db.add_project(name=proj["name"], keywords=proj.get("keywords", ""))
                logger.info(f"Seeded project: {proj['name']}")

    @property
    def notion(self):
        if self._notion is None and Config.is_notion_enabled():
            from notion_integration import NotionClient
            self._notion = NotionClient()
        return self._notion

    # ─── Contact Matching ────────────────────────────────────────────────────────

    def find_vip_in_email(self, sender: str, recipients: str = "") -> Optional[Dict]:
        """Check if an email involves a tracked VIP contact."""
        vips = db.get_all_vip_contacts()
        sender_lower = (sender or "").lower()
        for vip in vips:
            emails = [e.strip().lower() for e in (vip.get("email") or "").split(",") if e.strip()]
            for email in emails:
                if email and (email in sender_lower or email in recipients.lower()):
                    return vip
        return None

    def find_projects_in_text(self, text: str) -> List[Dict]:
        """Find projects whose keywords match a text string."""
        if not text:
            return []
        text_lower = text.lower()
        matched = []
        for project in db.get_all_projects():
            keywords_raw = project.get("keywords") or project["name"]
            keywords = [k.strip().lower() for k in keywords_raw.split(",") if k.strip()]
            if any(kw and kw in text_lower for kw in keywords):
                matched.append(project)
        return matched

    # ─── Email Scanning ──────────────────────────────────────────────────────────

    def process_email(self, email_data: Dict):
        """
        Scan an email object (as produced by GmailActionRouter) for VIP contacts
        and project matches. Log communication events.

        email_data keys: sender, subject, snippet, message_id, thread_id,
                         received_date, full_body
        """
        sender = email_data.get("sender", "")
        subject = email_data.get("subject", "")
        snippet = email_data.get("snippet", "")
        message_id = email_data.get("message_id", "")
        received_date = email_data.get("received_date")

        full_text = f"{subject} {snippet}"

        # Check for VIP contact
        vip = self.find_vip_in_email(sender)
        if vip:
            direction = "received"
            db.update_vip_last_contact(vip["id"], channel="email", direction=direction)
            db.log_communication(
                channel="email",
                direction=direction,
                contact_name=vip["name"],
                contact_email=sender,
                message_id=message_id,
                subject=subject,
                snippet=snippet[:200],
            )
            logger.info(f"Logged email from VIP {vip['name']}: {subject}")

        # Check for project matches
        projects = self.find_projects_in_text(full_text)
        for project in projects:
            db.update_project_activity(project["name"])
            db.log_communication(
                channel="email",
                direction="received",
                contact_name=vip["name"] if vip else None,
                contact_email=sender,
                project_name=project["name"],
                message_id=message_id,
                subject=subject,
                snippet=snippet[:200],
            )

    # ─── Notion Sync ─────────────────────────────────────────────────────────────

    def sync_notion_action_items(self) -> int:
        """
        Pull open action items from Notion and upsert into local DB.
        Returns the number of items synced.
        """
        if not self.notion:
            return 0
        try:
            items = self.notion.get_action_items()
            synced = 0
            for item in items:
                notion_id = item.get("notion_id")
                if not notion_id:
                    continue
                # Check if already tracked
                existing = db.get_open_action_items()
                existing_ids = {a.get("notion_task_id") for a in existing}
                if notion_id not in existing_ids:
                    due = item.get("due_date")
                    due_dt = None
                    if due:
                        try:
                            due_dt = datetime.fromisoformat(due)
                        except ValueError:
                            pass
                    db.add_action_item(
                        title=item["title"] or "(untitled)",
                        description=None,
                        source="notion",
                        source_id=notion_id,
                        project_name=item.get("project"),
                        assigned_to=item.get("assignee"),
                        due_date=due_dt,
                        priority=item.get("priority", "medium").lower(),
                        notion_task_id=notion_id,
                    )
                    synced += 1
            return synced
        except Exception as e:
            logger.error(f"Notion sync failed: {e}")
            return 0

    # ─── Dashboard Data ───────────────────────────────────────────────────────────

    def get_dashboard_data(self) -> Dict[str, Any]:
        """Compile all data needed for the co-pilot dashboard."""
        overdue_vips = db.get_overdue_vip_contacts()
        stalled_projects = db.get_stalled_projects()
        open_actions = db.get_open_action_items()
        overdue_actions = db.get_overdue_action_items()
        all_vips = db.get_all_vip_contacts()

        # Annotate VIPs with urgency
        annotated_vips = []
        for vip in all_vips:
            last = vip.get("last_contact_at")
            hours = _hours_ago(last)
            urgency = _urgency_level(hours, vip.get("max_silence_hours", 48))
            annotated_vips.append({**vip, "hours_since_contact": hours, "urgency": urgency})
        annotated_vips.sort(key=lambda v: v["urgency"], reverse=True)

        return {
            "overdue_vips": overdue_vips,
            "stalled_projects": stalled_projects,
            "open_actions": open_actions,
            "overdue_actions": overdue_actions,
            "all_vips": annotated_vips,
            "all_projects": db.get_all_projects(),
            "generated_at": datetime.now(),
        }

    # ─── Slack Block Builders ─────────────────────────────────────────────────────

    def build_dashboard_blocks(self) -> List[Dict]:
        """Build full co-pilot dashboard as Slack blocks."""
        data = self.get_dashboard_data()
        blocks = []
        now_str = data["generated_at"].strftime("%A, %b %d %H:%M")

        blocks.append({"type": "header", "text": {"type": "plain_text", "text": f"CEO Co-pilot Briefing  {now_str}"}})
        blocks.append({"type": "divider"})

        # ── Overdue VIP contacts
        overdue = data["overdue_vips"]
        if overdue:
            blocks.append(_section(f":red_circle: *{len(overdue)} VIP Contact(s) Need Attention*"))
            for vip in overdue[:5]:
                hours = vip.get("hours_since_contact")
                threshold = vip.get("max_silence_hours", 48)
                role_str = f" _{vip.get('role', '')}_" if vip.get("role") else ""
                last_ch = vip.get("last_contact_channel") or "?"
                blocks.append(_section(
                    f"*{vip['name']}*{role_str}  •  {CHANNEL_EMOJI.get(last_ch, ':speech_balloon:')} last: {_format_hours(hours)}  (threshold: {threshold}h)"
                ))
            if len(overdue) > 5:
                blocks.append(_section(f"_...and {len(overdue) - 5} more. Run `/team` for full list._"))
        else:
            blocks.append(_section(":white_check_mark: *All VIP contacts are up to date*"))

        blocks.append({"type": "divider"})

        # ── Stalled projects
        stalled = data["stalled_projects"]
        if stalled:
            blocks.append(_section(f":orange_circle: *{len(stalled)} Stalled Project(s)*"))
            for proj in stalled[:5]:
                hours = proj.get("hours_since_activity")
                owner = f"  Owner: _{proj.get('owner', 'unassigned')}_" if proj.get("owner") else ""
                blocks.append(_section(f"*{proj['name']}*{owner}  •  Silent for {_format_hours(hours)}"))
            if len(stalled) > 5:
                blocks.append(_section(f"_...and {len(stalled) - 5} more. Run `/projects` for full list._"))
        else:
            blocks.append(_section(":white_check_mark: *All projects have recent activity*"))

        blocks.append({"type": "divider"})

        # ── Overdue action items
        overdue_actions = data["overdue_actions"]
        if overdue_actions:
            blocks.append(_section(f":warning: *{len(overdue_actions)} Overdue Action Item(s)*"))
            for item in overdue_actions[:5]:
                due = item.get("due_date", "?")
                assignee = f"  → _{item.get('assigned_to', 'me')}_" if item.get("assigned_to") else ""
                proj = f"  [{item.get('project_name')}]" if item.get("project_name") else ""
                blocks.append(_section(f"• *{item['title']}*{proj}{assignee}  _Due: {due}_"))
        else:
            open_count = len(data["open_actions"])
            if open_count:
                blocks.append(_section(f":clipboard: *{open_count} open action item(s)* — all on track"))
            else:
                blocks.append(_section(":white_check_mark: *No open action items*"))

        blocks.append({"type": "divider"})

        # ── Quick actions
        blocks.append({
            "type": "actions",
            "elements": [
                _button("View Team", "copilot_team", "view_team"),
                _button("View Projects", "copilot_projects", "view_projects"),
                _button("Action Items", "copilot_actions", "view_actions"),
            ],
        })

        return blocks

    def build_team_blocks(self) -> List[Dict]:
        """Build full management team status as Slack blocks."""
        vips = db.get_all_vip_contacts()
        blocks = [{"type": "header", "text": {"type": "plain_text", "text": "Management Team Status"}}]
        blocks.append({"type": "divider"})

        if not vips:
            blocks.append(_section(
                ":information_source: No VIP contacts configured yet.\n"
                "Add contacts with `/add-vip Name email@domain.com Role` or set `VIP_CONTACTS` in your .env"
            ))
            return blocks

        for vip in vips:
            last = vip.get("last_contact_at")
            resp = vip.get("last_response_at")
            hours = _hours_ago(last)
            threshold = vip.get("max_silence_hours", 48)
            urgency = _urgency_level(hours, threshold)
            emoji = URGENCY_EMOJI[urgency]

            role_str = f" • _{vip.get('role', '')}_" if vip.get("role") else ""
            ch = vip.get("last_contact_channel") or ""
            ch_emoji = CHANNEL_EMOJI.get(ch, "")
            slack_mention = f"  <@{vip['slack_user_id']}>" if vip.get("slack_user_id") else ""
            notion_link = f"  <{vip['notion_page_url']}|1:1 Notes>" if vip.get("notion_page_url") else ""

            text = (
                f"{emoji} *{vip['name']}*{role_str}{slack_mention}{notion_link}\n"
                f"Last contact: {_format_hours(hours)} {ch_emoji}  |  "
                f"Last response: {_format_hours(_hours_ago(resp))}"
            )
            blocks.append(_section(text))

            # Recent communications
            recent = db.get_recent_communications(contact_name=vip["name"], hours=72)
            if recent:
                comms_text = "  ".join(
                    f"{CHANNEL_EMOJI.get(c['channel'], '')} {c['subject'][:40] if c.get('subject') else c.get('snippet', '')[:40]}"
                    for c in recent[:3]
                )
                blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"Recent: {comms_text}"}]})

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "actions",
            "elements": [_button("Full Briefing", "copilot_briefing", "view_briefing")],
        })
        return blocks

    def build_projects_blocks(self) -> List[Dict]:
        """Build project status view as Slack blocks."""
        projects = db.get_all_projects(status=None)
        blocks = [{"type": "header", "text": {"type": "plain_text", "text": "Project Tracker"}}]
        blocks.append({"type": "divider"})

        if not projects:
            blocks.append(_section(
                ":information_source: No projects configured yet.\n"
                "Add one with `/add-project ProjectName keyword1 keyword2`"
            ))
            return blocks

        active = [p for p in projects if p["status"] == "active"]
        other = [p for p in projects if p["status"] != "active"]

        for proj in active:
            hours = _hours_ago(proj.get("last_activity_at"))
            threshold = proj.get("max_silence_hours", 72)
            urgency = _urgency_level(hours, threshold)
            emoji = URGENCY_EMOJI[urgency]

            owner_str = f"  Owner: _{proj.get('owner', 'TBD')}_" if proj.get("owner") else ""
            notion_link = f"  <{proj['notion_page_url']}|Notion>" if proj.get("notion_page_url") else ""
            blocks.append(_section(
                f"{emoji} *{proj['name']}*{owner_str}{notion_link}\n"
                f"Last activity: {_format_hours(hours)}  (threshold: {threshold}h)"
            ))

        if other:
            blocks.append({"type": "divider"})
            blocks.append(_section(f"*Inactive/Completed ({len(other)})*"))
            for proj in other[:5]:
                blocks.append(_section(f"• {STATUS_EMOJI.get(proj['status'], ':white_circle:')} {proj['name']} — _{proj['status']}_"))

        return blocks

    def build_action_items_blocks(self, project_name: str = None, assigned_to: str = None) -> List[Dict]:
        """Build action items list as Slack blocks."""
        items = db.get_open_action_items(project_name=project_name, assigned_to=assigned_to)
        overdue = db.get_overdue_action_items()
        overdue_ids = {i["id"] for i in overdue}

        blocks = [{"type": "header", "text": {"type": "plain_text", "text": "Action Items"}}]
        if project_name:
            blocks[0]["text"]["text"] = f"Action Items: {project_name}"
        blocks.append({"type": "divider"})

        if not items:
            blocks.append(_section(":white_check_mark: *No open action items*"))
            return blocks

        for item in items[:15]:
            is_overdue = item["id"] in overdue_ids
            emoji = ":red_circle:" if is_overdue else ":white_circle:"
            proj = f" [{item.get('project_name')}]" if item.get("project_name") else ""
            assignee = f"  → _{item.get('assigned_to', 'me')}_" if item.get("assigned_to") else ""
            due = f"  _Due {item['due_date']}_" if item.get("due_date") else ""
            src = CHANNEL_EMOJI.get(item.get("source", "manual"), "")
            blocks.append(_section(f"{emoji} {src} *{item['title']}*{proj}{assignee}{due}"))

        if len(items) > 15:
            blocks.append(_section(f"_...{len(items) - 15} more items not shown_"))

        return blocks

    def build_followup_blocks(self, query: str) -> List[Dict]:
        """Build follow-up view for a person or project."""
        blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"Follow-up: {query}"}}]
        blocks.append({"type": "divider"})

        # Check if it's a person
        vip = db.get_vip_contact_by_name(query)
        if vip:
            hours = _hours_ago(vip.get("last_contact_at"))
            resp_hours = _hours_ago(vip.get("last_response_at"))
            blocks.append(_section(
                f":bust_in_silhouette: *{vip['name']}*  _{vip.get('role', '')}_\n"
                f":email: `{vip.get('email', 'N/A')}`\n"
                f"Last contacted: {_format_hours(hours)}  |  Last response: {_format_hours(resp_hours)}"
            ))
            blocks.append({"type": "divider"})

            # Recent comms
            recent = db.get_recent_communications(contact_name=vip["name"], hours=168)
            if recent:
                blocks.append(_section("*Recent communications (7d):*"))
                for comm in recent[:8]:
                    ch = CHANNEL_EMOJI.get(comm["channel"], "")
                    direction = ":arrow_right:" if comm["direction"] == "sent" else ":arrow_left:"
                    subj = comm.get("subject") or comm.get("snippet", "")[:50]
                    ts = comm.get("logged_at", "")[:10]
                    blocks.append({"type": "context", "elements": [
                        {"type": "mrkdwn", "text": f"{direction} {ch} {ts}  {subj}"}
                    ]})
            else:
                blocks.append(_section("_No recent communications logged_"))

            # Open action items assigned to this person
            actions = db.get_open_action_items(assigned_to=vip["name"])
            if actions:
                blocks.append({"type": "divider"})
                blocks.append(_section(f"*Open action items ({len(actions)}):*"))
                for item in actions[:5]:
                    blocks.append({"type": "context", "elements": [
                        {"type": "mrkdwn", "text": f":white_circle: {item['title']}"}
                    ]})

        # Check if it's a project
        proj = db.get_project_by_name(query)
        if proj:
            hours = _hours_ago(proj.get("last_activity_at"))
            notion_link = f"  <{proj['notion_page_url']}|Open in Notion>" if proj.get("notion_page_url") else ""
            blocks.append(_section(
                f":file_folder: *{proj['name']}*  _{proj.get('status', 'active')}_\n"
                f"Owner: _{proj.get('owner', 'TBD')}_  |  Last activity: {_format_hours(hours)}{notion_link}"
            ))

            if proj.get("description"):
                blocks.append({"type": "context", "elements": [
                    {"type": "mrkdwn", "text": proj["description"]}
                ]})

            recent = db.get_recent_communications(project_name=proj["name"], hours=168)
            if recent:
                blocks.append({"type": "divider"})
                blocks.append(_section("*Recent activity (7d):*"))
                for comm in recent[:8]:
                    ch = CHANNEL_EMOJI.get(comm["channel"], "")
                    direction = ":arrow_right:" if comm["direction"] == "sent" else ":arrow_left:"
                    subj = comm.get("subject") or comm.get("snippet", "")[:50]
                    ts = comm.get("logged_at", "")[:10]
                    blocks.append({"type": "context", "elements": [
                        {"type": "mrkdwn", "text": f"{direction} {ch} {ts}  {subj}"}
                    ]})

            actions = db.get_open_action_items(project_name=proj["name"])
            if actions:
                blocks.append({"type": "divider"})
                blocks.append(_section(f"*Action items ({len(actions)}):*"))
                for item in actions[:5]:
                    assignee = f"  → _{item.get('assigned_to', 'me')}_" if item.get("assigned_to") else ""
                    blocks.append({"type": "context", "elements": [
                        {"type": "mrkdwn", "text": f":white_circle: {item['title']}{assignee}"}
                    ]})

        if not vip and not proj:
            blocks.append(_section(f":x: No VIP contact or project found matching `{query}`"))

        return blocks

    # ─── Proactive Alerts ────────────────────────────────────────────────────────

    def get_alert_blocks(self) -> Optional[List[Dict]]:
        """
        Build an alert message if there are urgent items.
        Returns None if nothing needs attention.
        """
        overdue_vips = db.get_overdue_vip_contacts()
        stalled_projects = db.get_stalled_projects()
        overdue_actions = db.get_overdue_action_items()

        # Only high-urgency items (ratio >= 2x threshold)
        urgent_vips = [
            v for v in overdue_vips
            if _urgency_level(v.get("hours_since_contact"), v.get("max_silence_hours", 48)) >= 3
        ]
        critical_projects = [
            p for p in stalled_projects
            if _urgency_level(p.get("hours_since_activity"), p.get("max_silence_hours", 72)) >= 3
        ]

        if not urgent_vips and not critical_projects and not overdue_actions:
            return None

        blocks = [{"type": "header", "text": {"type": "plain_text", "text": ":rotating_light: CEO Co-pilot Alert"}}]

        if urgent_vips:
            items_text = "\n".join(f"• *{v['name']}* — {_format_hours(v.get('hours_since_contact'))} since contact" for v in urgent_vips[:3])
            blocks.append(_section(f":red_circle: *{len(urgent_vips)} VIP contact(s) significantly overdue:*\n{items_text}"))

        if critical_projects:
            items_text = "\n".join(f"• *{p['name']}* — silent for {_format_hours(p.get('hours_since_activity'))}" for p in critical_projects[:3])
            blocks.append(_section(f":orange_circle: *{len(critical_projects)} project(s) critically stalled:*\n{items_text}"))

        if overdue_actions:
            items_text = "\n".join(f"• {a['title']}" for a in overdue_actions[:3])
            blocks.append(_section(f":warning: *{len(overdue_actions)} overdue action item(s):*\n{items_text}"))

        blocks.append({
            "type": "actions",
            "elements": [_button("View Full Briefing", "copilot_full_briefing", "view_briefing")],
        })
        return blocks


# ─── Slack Block Helpers ─────────────────────────────────────────────────────────

def _section(text: str) -> Dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _button(label: str, action_id: str, value: str) -> Dict:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label},
        "action_id": action_id,
        "value": value,
    }


# Module-level singleton
copilot = CEOCopilot()
