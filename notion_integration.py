"""
Notion Integration for CEO Co-pilot
Connects to Notion API to read/write action items, projects, and pages.
Uses the official Notion REST API (v2022-06-28) directly via requests.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from config import Config

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionClient:
    """Lightweight Notion API client."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or Config.NOTION_API_KEY
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Dict = None) -> Optional[Dict]:
        try:
            resp = requests.get(
                f"{NOTION_API_BASE}/{path}",
                headers=self.headers,
                params=params,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"Notion GET {path} failed: {e}")
            return None

    def _post(self, path: str, body: Dict) -> Optional[Dict]:
        try:
            resp = requests.post(
                f"{NOTION_API_BASE}/{path}",
                headers=self.headers,
                json=body,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"Notion POST {path} failed: {e}")
            return None

    def _patch(self, path: str, body: Dict) -> Optional[Dict]:
        try:
            resp = requests.patch(
                f"{NOTION_API_BASE}/{path}",
                headers=self.headers,
                json=body,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"Notion PATCH {path} failed: {e}")
            return None

    # ─── Pages ──────────────────────────────────────────────────────────────────

    def get_page(self, page_id: str) -> Optional[Dict]:
        """Retrieve a Notion page by ID."""
        return self._get(f"pages/{page_id}")

    def get_page_url(self, page_id: str) -> Optional[str]:
        """Get the public URL for a Notion page."""
        page = self.get_page(page_id)
        return page.get("url") if page else None

    def search(self, query: str, page_size: int = 10) -> List[Dict]:
        """Search across all pages/databases in the workspace."""
        result = self._post("search", {"query": query, "page_size": page_size})
        return result.get("results", []) if result else []

    # ─── Databases ──────────────────────────────────────────────────────────────

    def query_database(self, database_id: str, filter_obj: Dict = None,
                       sorts: List = None, page_size: int = 50) -> List[Dict]:
        """Query a Notion database and return all rows."""
        body: Dict[str, Any] = {"page_size": page_size}
        if filter_obj:
            body["filter"] = filter_obj
        if sorts:
            body["sorts"] = sorts

        result = self._post(f"databases/{database_id}/query", body)
        return result.get("results", []) if result else []

    def get_database_schema(self, database_id: str) -> Optional[Dict]:
        """Retrieve a database's properties schema."""
        return self._get(f"databases/{database_id}")

    # ─── Action Items ────────────────────────────────────────────────────────────

    def get_action_items(self, database_id: str = None, only_open: bool = True) -> List[Dict]:
        """
        Fetch action items from a Notion database.
        Expects the database to have: Name/Title, Status, Priority, Due Date, Assignee, Project.
        """
        db_id = database_id or Config.NOTION_ACTION_ITEMS_DB
        if not db_id:
            return []

        filter_obj = None
        if only_open:
            filter_obj = {
                "or": [
                    {"property": "Status", "status": {"equals": "Not started"}},
                    {"property": "Status", "status": {"equals": "In progress"}},
                    {"property": "Status", "select": {"equals": "Open"}},
                    {"property": "Status", "select": {"equals": "In Progress"}},
                ]
            }

        rows = self.query_database(db_id, filter_obj=filter_obj,
                                   sorts=[{"property": "Due Date", "direction": "ascending"}])
        return [self._parse_action_item(row) for row in rows]

    def _parse_action_item(self, row: Dict) -> Dict:
        """Extract structured fields from a Notion database row."""
        props = row.get("properties", {})

        def title_text(prop):
            items = prop.get("title", [])
            return "".join(t.get("plain_text", "") for t in items) if items else ""

        def rich_text(prop):
            items = prop.get("rich_text", [])
            return "".join(t.get("plain_text", "") for t in items) if items else ""

        def select_val(prop):
            sel = prop.get("select") or {}
            return sel.get("name", "")

        def status_val(prop):
            s = prop.get("status") or {}
            return s.get("name", "")

        def date_val(prop):
            d = prop.get("date") or {}
            return d.get("start")

        def people_val(prop):
            people = prop.get("people", [])
            return ", ".join(p.get("name", "") for p in people)

        def relation_names(prop):
            # Relations only give IDs; we return IDs as-is for now
            rels = prop.get("relation", [])
            return ", ".join(r.get("id", "") for r in rels)

        # Try common property name variants
        def get_prop(*names):
            for n in names:
                if n in props:
                    return props[n]
            return {}

        title_prop = get_prop("Name", "Title", "Task", "Action Item")
        status_prop = get_prop("Status", "State")
        priority_prop = get_prop("Priority")
        due_prop = get_prop("Due Date", "Due", "Deadline")
        assignee_prop = get_prop("Assignee", "Assigned To", "Owner", "Person")
        project_prop = get_prop("Project", "Projects")

        status = status_val(status_prop) or select_val(status_prop)

        return {
            "notion_id": row.get("id"),
            "url": row.get("url"),
            "title": title_text(title_prop),
            "status": status,
            "priority": select_val(priority_prop),
            "due_date": date_val(due_prop),
            "assignee": people_val(assignee_prop),
            "project": select_val(project_prop) or rich_text(project_prop),
            "last_edited": row.get("last_edited_time"),
        }

    def create_action_item(self, database_id: str, title: str, description: str = None,
                           project: str = None, assignee_id: str = None,
                           due_date: str = None, priority: str = "Medium") -> Optional[Dict]:
        """
        Create a new action item page in a Notion database.
        Returns the created page or None.
        """
        db_id = database_id or Config.NOTION_ACTION_ITEMS_DB
        if not db_id:
            logger.warning("No Notion action items database configured")
            return None

        properties: Dict[str, Any] = {
            "Name": {"title": [{"text": {"content": title}}]},
            "Priority": {"select": {"name": priority}},
            "Status": {"select": {"name": "Open"}},
        }
        if project:
            properties["Project"] = {"select": {"name": project}}
        if due_date:
            properties["Due Date"] = {"date": {"start": due_date}}
        if assignee_id:
            properties["Assignee"] = {"people": [{"id": assignee_id}]}

        body: Dict[str, Any] = {
            "parent": {"database_id": db_id},
            "properties": properties,
        }
        if description:
            body["children"] = [{
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": description}}]
                }
            }]

        return self._post("pages", body)

    def update_action_item_status(self, page_id: str, status: str) -> Optional[Dict]:
        """Update the status of an action item in Notion."""
        return self._patch(f"pages/{page_id}", {
            "properties": {
                "Status": {"select": {"name": status}}
            }
        })

    # ─── Projects ───────────────────────────────────────────────────────────────

    def get_projects(self, database_id: str = None) -> List[Dict]:
        """Fetch projects from a Notion projects database."""
        db_id = database_id or Config.NOTION_PROJECTS_DB
        if not db_id:
            return []

        rows = self.query_database(db_id, sorts=[{"property": "Name", "direction": "ascending"}])
        return [self._parse_project(row) for row in rows]

    def _parse_project(self, row: Dict) -> Dict:
        props = row.get("properties", {})

        def title_text(prop):
            items = prop.get("title", [])
            return "".join(t.get("plain_text", "") for t in items) if items else ""

        def select_val(prop):
            return (prop.get("select") or {}).get("name", "")

        def rich_text(prop):
            items = prop.get("rich_text", [])
            return "".join(t.get("plain_text", "") for t in items) if items else ""

        def people_val(prop):
            people = prop.get("people", [])
            return ", ".join(p.get("name", "") for p in people)

        def get_prop(*names):
            for n in names:
                if n in props:
                    return props[n]
            return {}

        return {
            "notion_id": row.get("id"),
            "url": row.get("url"),
            "name": title_text(get_prop("Name", "Project", "Title")),
            "status": select_val(get_prop("Status", "State")),
            "owner": people_val(get_prop("Owner", "DRI", "Lead")),
            "description": rich_text(get_prop("Description", "Summary")),
            "last_edited": row.get("last_edited_time"),
        }

    # ─── Utility ─────────────────────────────────────────────────────────────────

    def is_connected(self) -> bool:
        """Test the Notion connection by fetching the current user."""
        result = self._get("users/me")
        return result is not None and "id" in result

    def get_workspace_name(self) -> Optional[str]:
        result = self._get("users/me")
        if result:
            return result.get("name")
        return None


# Module-level singleton
notion = NotionClient()
