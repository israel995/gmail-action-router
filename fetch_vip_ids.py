#!/usr/bin/env python3
"""
One-time helper: fetches Slack user IDs and Notion 1x1 page URLs
for the Hyro management team, then prints the VIP_CONTACTS env var
ready to paste into .env
Run: python3 fetch_vip_ids.py
"""

import os
import json
import urllib.request
import urllib.parse

SLACK_TOKEN  = os.getenv("SLACK_BOT_TOKEN")
NOTION_TOKEN = os.getenv("NOTION_API_KEY")

if not SLACK_TOKEN or not NOTION_TOKEN:
    print("ERROR: Set SLACK_BOT_TOKEN and NOTION_API_KEY env vars before running.")
    print("  export SLACK_BOT_TOKEN=xoxb-...")
    print("  export NOTION_API_KEY=ntn_...")
    raise SystemExit(1)

# ── Team definition ───────────────────────────────────────────────────────────
# (name, email, role, silence_hours)
TEAM = [
    ("Rom Cohen",           "rom@hyro.ai",         "COO",              24),
    ("Nitzan Bar",          "nitzan@hyro.ai",       "CTO",              24),
    ("Aaron Bours",         "aaron@hyro.ai",        "CMO",              24),
    ("Daniel Ben Tov",      "daniel@hyro.ai",       "VP R&D",           24),
    ("Gili Lichtman Kurtz", "gili@hyro.ai",         "VP Partnerships",  48),
    ("Maya Agmon",          "maya@hyro.ai",         "Chief of Staff",   24),
    ("Brian SanLorenzo",    "brian.s@hyro.ai",      "VP Sales MidMkt",  48),
    ("Brennan Stratton",    "brennan.s@hyro.ai",    "VP Sales Ent",     48),
]

INVESTORS = [
    ("Amir Dan Rubin",    "amir@healthiercapital.com",  "Healthier Capital", 168),
    ("Gary Munitz",       "gary.munitz@macquarie.com",  "Macquarie Capital", 168),
    ("Assaf Harel",       "AHarel@nvp.com",             "Norwest",           168),
    ("Lynne Chou OKeefe", "lynne@definevc.com",         "Define Ventures",   168),
    ("Sara Eshelman",     "sara@spero.vc",              "Spero Ventures",    168),
    ("Lior Prosor",       "lior@hanaco.vc",             "Hanaco Ventures",   168),
]

# ── Slack lookup ──────────────────────────────────────────────────────────────
def slack_id_for(email):
    url = f"https://slack.com/api/users.lookupByEmail?email={urllib.parse.quote(email)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {SLACK_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        if data.get("ok"):
            return data["user"]["id"]
        return None  # users_not_found or not_authed
    except Exception as e:
        print(f"  Slack error for {email}: {e}")
        return None

# ── Notion 1x1 lookup ────────────────────────────────────────────────────────
def notion_search(query):
    payload = json.dumps({"query": query, "filter": {"value": "page", "property": "object"}, "page_size": 5}).encode()
    req = urllib.request.Request(
        "https://api.notion.com/v1/search",
        data=payload,
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.load(r).get("results", [])
    except Exception as e:
        print(f"  Notion error for '{query}': {e}")
        return []

def best_notion_page(first_name):
    """Find the most likely 1x1 page for a person by first name."""
    results = notion_search(f"1:1 {first_name}")
    if not results:
        results = notion_search(first_name)
    for page in results:
        title_parts = page.get("properties", {}).get("title", {}).get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_parts).lower()
        if "1" in title or "1:1" in title or first_name.lower() in title:
            return page.get("url")
    return results[0].get("url") if results else None

# ── Main ──────────────────────────────────────────────────────────────────────
def build_entry(name, email, role, hours, include_slack=True):
    slack_id = slack_id_for(email) if include_slack else None
    first = name.split()[0]
    notion_url = best_notion_page(first)

    sid = slack_id or ""
    nurl = notion_url or ""
    # Format: Name:email:role:hours:slack_id:notion_url
    return f"{name}:{email}:{role}:{hours}:{sid}:{nurl}"

print("Fetching Slack IDs and Notion pages...\n")

entries = []
print("── Internal Team ──")
for (name, email, role, hours) in TEAM:
    entry = build_entry(name, email, role, hours, include_slack=True)
    print(f"  {name}: {entry.split(':')[4] or 'no Slack ID'} / {entry.split(':')[5][:60] or 'no Notion page'}")
    entries.append(entry)

print("\n── Investors (no Slack) ──")
for (name, email, role, hours) in INVESTORS:
    entry = build_entry(name, email, role, hours, include_slack=False)
    print(f"  {name}: {entry.split(':')[5][:60] or 'no Notion page'}")
    entries.append(entry)

vip_value = ",".join(entries)
print("\n" + "="*70)
print("Add this to your .env file:\n")
print(f'VIP_CONTACTS="{vip_value}"')
print("="*70)

# Also write it to a file for easy copy
with open("vip_contacts_output.txt", "w") as f:
    f.write(f'VIP_CONTACTS="{vip_value}"\n')
print("\nAlso saved to: vip_contacts_output.txt")
