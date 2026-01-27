#!/usr/bin/env python3
"""
Flask Web Server for Gmail Action Router
Handles Slack webhooks for interactivity (buttons, slash commands, modals).
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify

from config import Config
from database import db
from gmail_action_router import GmailActionRouter, Priority, Category

# Lazy imports for optional dependencies
slack_client = None
router = None


def get_slack_client():
    """Get or create Slack client."""
    global slack_client
    if slack_client is None and Config.SLACK_BOT_TOKEN:
        from slack_sdk import WebClient
        slack_client = WebClient(token=Config.SLACK_BOT_TOKEN)
    return slack_client


def get_router():
    """Get or create Gmail router (singleton)."""
    global router
    if router is None:
        router = GmailActionRouter()
        router.authenticate_gmail()
        router.setup_slack()
    return router


app = Flask(__name__)


def verify_slack_signature(f):
    """Decorator to verify Slack request signatures."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not Config.SLACK_SIGNING_SECRET:
            # Skip verification if not configured (dev mode)
            return f(*args, **kwargs)

        timestamp = request.headers.get('X-Slack-Request-Timestamp', '')
        signature = request.headers.get('X-Slack-Signature', '')

        # Check timestamp to prevent replay attacks
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return jsonify({'error': 'Request too old'}), 403

        # Verify signature
        sig_basestring = f"v0:{timestamp}:{request.get_data(as_text=True)}"
        my_signature = 'v0=' + hmac.new(
            Config.SLACK_SIGNING_SECRET.encode(),
            sig_basestring.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(my_signature, signature):
            return jsonify({'error': 'Invalid signature'}), 403

        return f(*args, **kwargs)
    return decorated_function


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'ai_enabled': Config.is_ai_enabled(),
        'slack_interactive': Config.is_slack_interactive_enabled()
    })


@app.route('/slack/events', methods=['POST'])
@verify_slack_signature
def slack_events():
    """Handle Slack Events API."""
    data = request.json

    # Handle URL verification challenge
    if data.get('type') == 'url_verification':
        return jsonify({'challenge': data.get('challenge')})

    # Handle events
    event = data.get('event', {})
    event_type = event.get('type')

    if event_type == 'message' and event.get('channel_type') == 'im':
        # Handle DM commands
        handle_dm_command(event)

    return jsonify({'ok': True})


@app.route('/slack/commands', methods=['POST'])
@verify_slack_signature
def slack_commands():
    """Handle Slack slash commands."""
    command = request.form.get('command')
    text = request.form.get('text', '')
    user_id = request.form.get('user_id')
    channel_id = request.form.get('channel_id')
    response_url = request.form.get('response_url')

    if command == '/digest':
        return handle_digest_command(text, user_id, channel_id)
    elif command == '/snooze':
        return handle_snooze_command(text, user_id, channel_id)
    elif command == '/delegate':
        return handle_delegate_command(text, user_id, channel_id)
    elif command == '/email-stats':
        return handle_stats_command(user_id, channel_id)
    elif command == '/invites':
        return handle_invites_command(text, user_id, channel_id)
    elif command == '/family':
        return handle_family_command(text, user_id, channel_id)

    return jsonify({
        'response_type': 'ephemeral',
        'text': f"Unknown command: {command}"
    })


@app.route('/slack/interactions', methods=['POST'])
@verify_slack_signature
def slack_interactions():
    """Handle Slack interactive components (buttons, modals)."""
    payload = json.loads(request.form.get('payload', '{}'))
    interaction_type = payload.get('type')

    if interaction_type == 'block_actions':
        return handle_block_actions(payload)
    elif interaction_type == 'view_submission':
        return handle_view_submission(payload)
    elif interaction_type == 'shortcut':
        return handle_shortcut(payload)

    return jsonify({'ok': True})


def handle_block_actions(payload):
    """Handle button clicks from Slack messages."""
    actions = payload.get('actions', [])
    user = payload.get('user', {})
    channel = payload.get('channel', {})
    message = payload.get('message', {})

    for action in actions:
        action_id = action.get('action_id')
        message_id = action.get('value')

        if action_id == 'archive_email':
            return handle_archive_action(message_id, user, channel, message)
        elif action_id == 'reply_email':
            return open_reply_modal(payload.get('trigger_id'), message_id)
        elif action_id == 'snooze_email':
            return open_snooze_modal(payload.get('trigger_id'), message_id, channel, message)
        elif action_id == 'delegate_email':
            return open_delegate_modal(payload.get('trigger_id'), message_id)
        # Calendar invite actions
        elif action_id == 'archive_invite':
            return handle_archive_invite(message_id, user, channel, message)
        elif action_id == 'reply_invite':
            return open_reply_modal(payload.get('trigger_id'), message_id)
        elif action_id == 'archive_all_invites':
            return handle_archive_all_invites(message_id, user, channel, message)
        elif action_id == 'accept_invite':
            return handle_accept_invite(message_id, user, channel, message)
        elif action_id == 'decline_invite':
            return handle_decline_invite(message_id, user, channel, message)

    return jsonify({'ok': True})


def handle_view_submission(payload):
    """Handle modal form submissions."""
    view = payload.get('view', {})
    callback_id = view.get('callback_id', '')
    user = payload.get('user', {})
    values = view.get('state', {}).get('values', {})

    if callback_id.startswith('reply_modal_'):
        message_id = callback_id.replace('reply_modal_', '')
        return handle_reply_submission(message_id, values, user)
    elif callback_id.startswith('snooze_modal_'):
        message_id = callback_id.replace('snooze_modal_', '')
        metadata = json.loads(view.get('private_metadata', '{}'))
        return handle_snooze_submission(message_id, values, user, metadata)
    elif callback_id.startswith('delegate_modal_'):
        message_id = callback_id.replace('delegate_modal_', '')
        return handle_delegate_submission(message_id, values, user)

    return jsonify({'ok': True})


def handle_archive_action(message_id, user, channel, message):
    """Archive an email and update Slack message."""
    try:
        r = get_router()
        success = r.archive_email(message_id)

        if success:
            # Update the Slack message to show archived status
            client = get_slack_client()
            if client and message.get('ts'):
                client.chat_update(
                    channel=channel.get('id'),
                    ts=message.get('ts'),
                    text="Email archived",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"✅ *Archived* by <@{user.get('id')}>"
                            }
                        }
                    ]
                )

            # Track in database
            db.mark_email_processed(message_id, action_taken='archived')

            return jsonify({'ok': True})
        else:
            return jsonify({
                'response_type': 'ephemeral',
                'text': "Failed to archive email. Please try again."
            })
    except Exception as e:
        return jsonify({
            'response_type': 'ephemeral',
            'text': f"Error: {str(e)}"
        })


def handle_archive_invite(message_id, user, channel, message):
    """Archive a calendar invite and update Slack message."""
    try:
        r = get_router()
        success = r.archive_email(message_id)

        if success:
            # Update the Slack message
            client = get_slack_client()
            if client and message.get('ts'):
                client.chat_update(
                    channel=channel.get('id'),
                    ts=message.get('ts'),
                    text="Invite archived",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"📁 *Invite archived* by <@{user.get('id')}>"
                            }
                        }
                    ]
                )

            # Track in database
            db.update_invite_action(message_id, 'archived')

            return jsonify({'ok': True})
        else:
            return jsonify({
                'response_type': 'ephemeral',
                'text': "Failed to archive invite. Please try again."
            })
    except Exception as e:
        return jsonify({
            'response_type': 'ephemeral',
            'text': f"Error: {str(e)}"
        })


def handle_accept_invite(message_id, user, channel, message):
    """Accept a calendar invite and archive the email."""
    try:
        r = get_router()
        client = get_slack_client()

        # Get email details for calendar matching
        email = r.get_email_by_id(message_id)
        subject = ""
        if email:
            headers = email.get('payload', {}).get('headers', [])
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')

        # Try to accept via Calendar API
        calendar_success = False
        try:
            from calendar_integration import calendar
            if calendar.authenticate():
                calendar_success = calendar.accept_event(subject)
        except Exception as e:
            print(f"Calendar accept failed: {e}")

        # Archive the email regardless
        r.archive_email(message_id)

        # Update Slack message
        if client and message.get('ts'):
            status_text = "✅ *Accepted & archived*" if calendar_success else "✅ *Archived* (calendar update may require manual confirmation)"
            client.chat_update(
                channel=channel.get('id'),
                ts=message.get('ts'),
                text="Invite accepted",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"{status_text} by <@{user.get('id')}>\n_{subject}_"
                        }
                    }
                ]
            )

        # Track in database
        db.update_invite_action(message_id, 'accepted', my_response='accepted')

        return jsonify({'ok': True})

    except Exception as e:
        return jsonify({
            'response_type': 'ephemeral',
            'text': f"Error: {str(e)}"
        })


def handle_decline_invite(message_id, user, channel, message):
    """Decline a calendar invite and archive the email."""
    try:
        r = get_router()
        client = get_slack_client()

        # Get email details for calendar matching
        email = r.get_email_by_id(message_id)
        subject = ""
        if email:
            headers = email.get('payload', {}).get('headers', [])
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')

        # Try to decline via Calendar API
        calendar_success = False
        try:
            from calendar_integration import calendar
            if calendar.authenticate():
                calendar_success = calendar.decline_event(subject)
        except Exception as e:
            print(f"Calendar decline failed: {e}")

        # Archive the email regardless
        r.archive_email(message_id)

        # Update Slack message
        if client and message.get('ts'):
            status_text = "❌ *Declined & archived*" if calendar_success else "❌ *Archived* (calendar update may require manual confirmation)"
            client.chat_update(
                channel=channel.get('id'),
                ts=message.get('ts'),
                text="Invite declined",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"{status_text} by <@{user.get('id')}>\n_{subject}_"
                        }
                    }
                ]
            )

        # Track in database
        db.update_invite_action(message_id, 'declined', my_response='declined')

        return jsonify({'ok': True})

    except Exception as e:
        return jsonify({
            'response_type': 'ephemeral',
            'text': f"Error: {str(e)}"
        })


def handle_archive_all_invites(invite_ids_str, user, channel, message):
    """Archive all calendar invites (bulk action)."""
    try:
        invite_ids = invite_ids_str.split(',') if invite_ids_str else []
        r = get_router()
        client = get_slack_client()

        archived_count = 0
        failed_count = 0

        for message_id in invite_ids:
            message_id = message_id.strip()
            if not message_id:
                continue
            try:
                if r.archive_email(message_id):
                    db.update_invite_action(message_id, 'archived')
                    archived_count += 1
                else:
                    failed_count += 1
            except Exception:
                failed_count += 1

        # Update the Slack message
        if client and message.get('ts'):
            client.chat_update(
                channel=channel.get('id'),
                ts=message.get('ts'),
                text="Invites archived",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"✅ *{archived_count} invites archived* by <@{user.get('id')}>"
                                   + (f"\n⚠️ {failed_count} failed" if failed_count > 0 else "")
                        }
                    }
                ]
            )

        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({
            'response_type': 'ephemeral',
            'text': f"Error: {str(e)}"
        })


def open_reply_modal(trigger_id, message_id):
    """Open the reply composition modal."""
    client = get_slack_client()
    if not client:
        return jsonify({'ok': False, 'error': 'Slack not configured'})

    try:
        # Get email details for context
        r = get_router()
        email = r.get_email_by_id(message_id)
        subject = ""
        sender = ""
        if email:
            headers = email.get('payload', {}).get('headers', [])
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), '')

        # Try to get AI-suggested replies if available
        suggested_replies = []
        if Config.is_ai_enabled():
            try:
                from ai_assistant import AIAssistant
                ai = AIAssistant()
                body = r.get_email_body(email) if email else ""
                suggested_replies = ai.generate_reply_suggestions(subject, sender, body)
            except Exception:
                pass

        # Build modal blocks
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Replying to:* {subject}\n*From:* {sender}"
                }
            },
            {
                "type": "input",
                "block_id": "reply_text",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "reply_input",
                    "multiline": True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Type your reply here..."
                    }
                },
                "label": {
                    "type": "plain_text",
                    "text": "Your Reply"
                }
            }
        ]

        # Add suggested replies if available
        if suggested_replies:
            options = [
                {
                    "text": {"type": "plain_text", "text": reply[:75] + "..." if len(reply) > 75 else reply},
                    "value": reply
                }
                for reply in suggested_replies[:3]
            ]
            blocks.insert(1, {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "🤖 *AI Suggested Replies:*"
                }
            })
            blocks.insert(2, {
                "type": "actions",
                "block_id": "suggested_replies",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": f"Option {i+1}"},
                        "action_id": f"use_suggestion_{i}",
                        "value": reply
                    }
                    for i, reply in enumerate(suggested_replies[:3])
                ]
            })

        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": f"reply_modal_{message_id}",
                "title": {"type": "plain_text", "text": "Reply to Email"},
                "submit": {"type": "plain_text", "text": "Send Reply"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "blocks": blocks,
                "private_metadata": json.dumps({
                    "message_id": message_id,
                    "subject": subject,
                    "sender": sender
                })
            }
        )
        return jsonify({'ok': True})

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


def open_snooze_modal(trigger_id, message_id, channel=None, message=None):
    """Open the snooze time selection modal."""
    client = get_slack_client()
    if not client:
        return jsonify({'ok': False, 'error': 'Slack not configured'})

    try:
        # Store channel and message info for updating after submission
        metadata = {
            "message_id": message_id,
            "channel_id": channel.get('id') if channel else None,
            "message_ts": message.get('ts') if message else None
        }

        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": f"snooze_modal_{message_id}",
                "title": {"type": "plain_text", "text": "Snooze Email"},
                "submit": {"type": "plain_text", "text": "Snooze"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "When would you like to be reminded about this email?"
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "snooze_duration",
                        "element": {
                            "type": "static_select",
                            "action_id": "snooze_select",
                            "placeholder": {"type": "plain_text", "text": "Select duration"},
                            "options": [
                                {"text": {"type": "plain_text", "text": "Tomorrow morning (9 AM)"}, "value": "tomorrow"},
                                {"text": {"type": "plain_text", "text": "Next Monday (9 AM)"}, "value": "monday"},
                                {"text": {"type": "plain_text", "text": "1 week"}, "value": "1w"},
                                {"text": {"type": "plain_text", "text": "2 weeks"}, "value": "2w"},
                                {"text": {"type": "plain_text", "text": "1 month"}, "value": "1m"}
                            ]
                        },
                        "label": {"type": "plain_text", "text": "Snooze until"}
                    }
                ],
                "private_metadata": json.dumps(metadata)
            }
        )
        return jsonify({'ok': True})

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


def open_delegate_modal(trigger_id, message_id):
    """Open the delegation modal."""
    client = get_slack_client()
    if not client:
        return jsonify({'ok': False, 'error': 'Slack not configured'})

    try:
        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": f"delegate_modal_{message_id}",
                "title": {"type": "plain_text", "text": "Delegate Email"},
                "submit": {"type": "plain_text", "text": "Delegate"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "delegate_to",
                        "element": {
                            "type": "users_select",
                            "action_id": "user_select",
                            "placeholder": {"type": "plain_text", "text": "Select a person"}
                        },
                        "label": {"type": "plain_text", "text": "Delegate to"}
                    },
                    {
                        "type": "input",
                        "block_id": "delegate_notes",
                        "optional": True,
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "notes_input",
                            "multiline": True,
                            "placeholder": {"type": "plain_text", "text": "Add any notes or instructions..."}
                        },
                        "label": {"type": "plain_text", "text": "Notes"}
                    },
                    {
                        "type": "input",
                        "block_id": "delegate_due",
                        "optional": True,
                        "element": {
                            "type": "datepicker",
                            "action_id": "due_date",
                            "placeholder": {"type": "plain_text", "text": "Select a date"}
                        },
                        "label": {"type": "plain_text", "text": "Due Date"}
                    }
                ],
                "private_metadata": json.dumps({"message_id": message_id})
            }
        )
        return jsonify({'ok': True})

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


def handle_reply_submission(message_id, values, user):
    """Handle reply modal submission."""
    try:
        reply_text = values.get('reply_text', {}).get('reply_input', {}).get('value', '')

        if not reply_text:
            return jsonify({
                'response_action': 'errors',
                'errors': {'reply_text': 'Please enter a reply message'}
            })

        r = get_router()
        email = r.get_email_by_id(message_id)

        if not email:
            return jsonify({
                'response_action': 'errors',
                'errors': {'reply_text': 'Could not find the original email'}
            })

        # Get email details
        headers = email.get('payload', {}).get('headers', [])
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
        sender_email = r.get_sender_email(email)
        thread_id = email.get('threadId')

        # Send the reply
        success = r.send_reply(
            message_id=message_id,
            thread_id=thread_id,
            to_email=sender_email,
            subject=subject,
            body=reply_text
        )

        if success:
            db.mark_email_processed(message_id, action_taken='replied')

            # Notify in channel
            client = get_slack_client()
            if client:
                client.chat_postMessage(
                    channel=Config.SLACK_CHANNEL,
                    text=f"✉️ <@{user.get('id')}> replied to: {subject}"
                )

            return jsonify({'response_action': 'clear'})
        else:
            return jsonify({
                'response_action': 'errors',
                'errors': {'reply_text': 'Failed to send reply. Please try again.'}
            })

    except Exception as e:
        return jsonify({
            'response_action': 'errors',
            'errors': {'reply_text': f'Error: {str(e)}'}
        })


def handle_snooze_submission(message_id, values, user, metadata=None):
    """Handle snooze modal submission."""
    try:
        duration = values.get('snooze_duration', {}).get('snooze_select', {}).get('selected_option', {}).get('value')
        metadata = metadata or {}

        if not duration:
            return jsonify({
                'response_action': 'errors',
                'errors': {'snooze_duration': 'Please select a snooze duration'}
            })

        # Calculate remind_at time
        now = datetime.now()
        if duration == 'tomorrow':
            tomorrow = now + timedelta(days=1)
            remind_at = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
        elif duration == 'monday':
            days_ahead = 7 - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            next_monday = now + timedelta(days=days_ahead)
            remind_at = next_monday.replace(hour=9, minute=0, second=0, microsecond=0)
        elif duration == '1w':
            remind_at = now + timedelta(weeks=1)
        elif duration == '2w':
            remind_at = now + timedelta(weeks=2)
        elif duration == '1m':
            remind_at = now + timedelta(days=30)
        else:
            tomorrow = now + timedelta(days=1)
            remind_at = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)

        # Get email details
        r = get_router()
        email = r.get_email_by_id(message_id)
        subject = ""
        sender = ""
        snippet = ""
        if email:
            headers = email.get('payload', {}).get('headers', [])
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), '')
            snippet = email.get('snippet', '')

        # Archive the email in Gmail (remove from inbox)
        r.archive_email(message_id)

        # Store snooze in database
        db.snooze_email(
            message_id=message_id,
            remind_at=remind_at,
            thread_id=email.get('threadId') if email else None,
            subject=subject,
            sender=sender,
            snippet=snippet
        )

        # Update the original Slack message to show snoozed status
        client = get_slack_client()
        if client:
            channel_id = metadata.get('channel_id')
            message_ts = metadata.get('message_ts')

            if channel_id and message_ts:
                client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    text="Email snoozed",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"⏰ *Snoozed* by <@{user.get('id')}> until {remind_at.strftime('%b %d at %I:%M %p')}\n_{subject}_"
                            }
                        }
                    ]
                )

        return jsonify({'response_action': 'clear'})

    except Exception as e:
        return jsonify({
            'response_action': 'errors',
            'errors': {'snooze_duration': f'Error: {str(e)}'}
        })


def handle_delegate_submission(message_id, values, user):
    """Handle delegate modal submission."""
    try:
        delegate_to = values.get('delegate_to', {}).get('user_select', {}).get('selected_user')
        notes = values.get('delegate_notes', {}).get('notes_input', {}).get('value', '')
        due_date_str = values.get('delegate_due', {}).get('due_date', {}).get('selected_date')

        if not delegate_to:
            return jsonify({
                'response_action': 'errors',
                'errors': {'delegate_to': 'Please select someone to delegate to'}
            })

        # Parse due date
        due_date = None
        if due_date_str:
            due_date = datetime.strptime(due_date_str, '%Y-%m-%d')

        # Get email details
        r = get_router()
        email = r.get_email_by_id(message_id)
        subject = ""
        sender = ""
        if email:
            headers = email.get('payload', {}).get('headers', [])
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), '')

        # Store delegation in database
        db.delegate_email(
            message_id=message_id,
            delegated_to=delegate_to,
            thread_id=email.get('threadId') if email else None,
            subject=subject,
            sender=sender,
            due_date=due_date,
            notes=notes
        )

        # Notify the assignee
        client = get_slack_client()
        if client:
            gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{message_id}"
            due_text = f"\n*Due:* {due_date.strftime('%b %d, %Y')}" if due_date else ""

            client.chat_postMessage(
                channel=delegate_to,  # DM the assignee
                text=f"📋 <@{user.get('id')}> delegated an email to you",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"📋 *New Delegated Task* from <@{user.get('id')}>"
                        }
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Subject:*\n{subject}"},
                            {"type": "mrkdwn", "text": f"*From:*\n{sender}"}
                        ]
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Notes:*\n{notes or 'No notes provided'}{due_text}"
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
                            }
                        ]
                    }
                ]
            )

            # Also post to delegation channel if configured
            if Config.DELEGATION_CHANNEL:
                client.chat_postMessage(
                    channel=Config.DELEGATION_CHANNEL,
                    text=f"📋 <@{user.get('id')}> delegated \"{subject}\" to <@{delegate_to}>{due_text}"
                )

        return jsonify({'response_action': 'clear'})

    except Exception as e:
        return jsonify({
            'response_action': 'errors',
            'errors': {'delegate_to': f'Error: {str(e)}'}
        })


def handle_digest_command(text, user_id, channel_id):
    """Handle /digest slash command."""
    try:
        # Parse optional parameters
        hours = Config.DEFAULT_SCAN_HOURS
        if text:
            try:
                hours = int(text)
            except ValueError:
                pass

        r = get_router()
        messages = r.get_unread_emails(hours_back=hours)

        if not messages:
            return jsonify({
                'response_type': 'ephemeral',
                'text': f"No unread emails in the last {hours} hours."
            })

        # Parse and categorize emails
        actions = []
        for msg in messages:
            action = r.parse_email(msg)
            if action:
                actions.append(action)

        if not actions:
            return jsonify({
                'response_type': 'ephemeral',
                'text': "No actionable emails found."
            })

        # Group by priority
        by_priority = {}
        for action in actions:
            if action.priority not in by_priority:
                by_priority[action.priority] = []
            by_priority[action.priority].append(action)

        # Build summary
        summary_lines = [f"*📧 Email Digest* ({len(actions)} emails)\n"]

        for priority in [Priority.URGENT, Priority.HIGH, Priority.MEDIUM, Priority.LOW]:
            if priority in by_priority:
                count = len(by_priority[priority])
                summary_lines.append(f"{priority.value}: {count}")

        # Add category breakdown
        by_category = {}
        for action in actions:
            cat = action.category.value
            by_category[cat] = by_category.get(cat, 0) + 1

        summary_lines.append("\n*By Category:*")
        for cat, count in by_category.items():
            summary_lines.append(f"• {cat.title()}: {count}")

        return jsonify({
            'response_type': 'in_channel',
            'text': '\n'.join(summary_lines)
        })

    except Exception as e:
        return jsonify({
            'response_type': 'ephemeral',
            'text': f"Error generating digest: {str(e)}"
        })


def handle_snooze_command(text, user_id, channel_id):
    """Handle /snooze slash command - list active snoozes."""
    try:
        snoozes = db.get_active_snoozes()

        if not snoozes:
            return jsonify({
                'response_type': 'ephemeral',
                'text': "No active snoozes."
            })

        lines = ["*⏰ Active Snoozes:*\n"]
        for snooze in snoozes:
            remind_at = datetime.fromisoformat(snooze['remind_at'])
            lines.append(f"• {snooze['subject'][:40]}... → {remind_at.strftime('%b %d, %I:%M %p')}")

        return jsonify({
            'response_type': 'ephemeral',
            'text': '\n'.join(lines)
        })

    except Exception as e:
        return jsonify({
            'response_type': 'ephemeral',
            'text': f"Error: {str(e)}"
        })


def handle_delegate_command(text, user_id, channel_id):
    """Handle /delegate slash command - list pending delegations."""
    try:
        delegations = db.get_pending_delegations()

        if not delegations:
            return jsonify({
                'response_type': 'ephemeral',
                'text': "No pending delegations."
            })

        lines = ["*📋 Pending Delegations:*\n"]
        for d in delegations:
            due_text = ""
            if d['due_date']:
                due = datetime.fromisoformat(d['due_date'])
                due_text = f" (due {due.strftime('%b %d')})"
            lines.append(f"• {d['subject'][:40]}... → <@{d['delegated_to']}>{due_text}")

        return jsonify({
            'response_type': 'ephemeral',
            'text': '\n'.join(lines)
        })

    except Exception as e:
        return jsonify({
            'response_type': 'ephemeral',
            'text': f"Error: {str(e)}"
        })


def handle_stats_command(user_id, channel_id):
    """Handle /email-stats slash command."""
    try:
        stats = db.get_stats()

        return jsonify({
            'response_type': 'ephemeral',
            'text': f"*📊 Email Agent Stats:*\n"
                   f"• Processed emails: {stats['processed_emails']}\n"
                   f"• Active snoozes: {stats['active_snoozes']}\n"
                   f"• Pending delegations: {stats['pending_delegations']}\n"
                   f"• Cached AI summaries: {stats['cached_summaries']}"
        })

    except Exception as e:
        return jsonify({
            'response_type': 'ephemeral',
            'text': f"Error: {str(e)}"
        })


def handle_invites_command(text, user_id, channel_id):
    """Handle /invites slash command - fetch and display calendar invites."""
    import threading

    # Parse optional hours parameter
    hours = 168  # Default to 7 days
    if text:
        try:
            hours = int(text)
        except ValueError:
            pass

    def process_invites_async():
        """Process invites in background thread."""
        try:
            r = get_router()
            client = get_slack_client()

            # Send scanning message
            if client:
                client.chat_postMessage(
                    channel=channel_id,
                    text=f"🔍 Scanning for calendar invites from the last {hours} hours..."
                )

            # Fetch ALL recent calendar invites (not just unprocessed)
            invites = r.get_calendar_invites(hours_back=hours)

            if not invites:
                if client:
                    client.chat_postMessage(
                        channel=channel_id,
                        text=f"No calendar invites found in the last {hours} hours."
                    )
                return

            # Send invites to Slack with full details
            r.send_calendar_invites_to_slack(invites)

            # Save new invites to database for tracking
            import json
            for invite in invites:
                if not db.is_invite_processed(invite.message_id):
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

        except Exception as e:
            client = get_slack_client()
            if client:
                client.chat_postMessage(
                    channel=channel_id,
                    text=f"Error fetching invites: {str(e)}"
                )

    # Start background thread
    thread = threading.Thread(target=process_invites_async)
    thread.start()

    # Respond immediately to Slack
    return jsonify({
        'response_type': 'ephemeral',
        'text': "Processing calendar invites..."
    })


def handle_family_command(text, user_id, channel_id):
    """Handle /family slash command - summarize family/school announcements."""
    import threading

    # Parse optional days parameter (default 7 days)
    days = 7
    if text:
        try:
            days = int(text)
        except ValueError:
            pass

    def process_family_async():
        """Process family emails in background thread."""
        try:
            r = get_router()
            client = get_slack_client()

            if client:
                client.chat_postMessage(
                    channel=channel_id,
                    text=f"👨‍👩‍👧‍👦 Scanning family announcements from the last {days} days..."
                )

            # Build query for family senders
            family_senders = Config.FAMILY_SENDERS
            if not family_senders:
                if client:
                    client.chat_postMessage(
                        channel=channel_id,
                        text="No family senders configured. Add them in config.py or FAMILY_SENDERS env var."
                    )
                return

            # Create OR query for all family senders
            sender_queries = ' OR '.join([f'from:{sender}' for sender in family_senders])
            hours_back = days * 24

            from datetime import datetime, timedelta
            after_date = datetime.now() - timedelta(hours=hours_back)
            after_timestamp = int(after_date.timestamp())

            query = f'({sender_queries}) after:{after_timestamp}'

            # Fetch emails
            results = r.gmail_service.users().messages().list(
                userId='me',
                q=query,
                maxResults=50
            ).execute()

            messages = results.get('messages', [])

            if not messages:
                if client:
                    client.chat_postMessage(
                        channel=channel_id,
                        text=f"No family announcements found in the last {days} days."
                    )
                return

            # Get full details for each email
            emails_data = []
            for msg in messages:
                msg_detail = r.gmail_service.users().messages().get(
                    userId='me',
                    id=msg['id'],
                    format='full'
                ).execute()

                headers = msg_detail.get('payload', {}).get('headers', [])
                subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
                sender = next((h['value'] for h in headers if h['name'] == 'From'), '')
                date_str = next((h['value'] for h in headers if h['name'] == 'Date'), '')

                # Get body
                body = r.get_email_body(msg_detail)[:2000]  # Limit body size

                emails_data.append({
                    'message_id': msg['id'],
                    'subject': subject,
                    'sender': sender,
                    'date': date_str,
                    'body': body,
                    'snippet': msg_detail.get('snippet', '')
                })

            # Send header
            if client:
                client.chat_postMessage(
                    channel=channel_id,
                    text="Family Announcements Summary",
                    blocks=[
                        {
                            "type": "header",
                            "text": {"type": "plain_text", "text": "👨‍👩‍👧‍👦 Family Announcements Summary"}
                        },
                        {
                            "type": "context",
                            "elements": [{"type": "mrkdwn", "text": f"_{len(emails_data)} emails from the last {days} days_"}]
                        }
                    ]
                )

            # Use AI to generate structured summary with email references
            if Config.is_ai_enabled():
                sections = _generate_family_summary_sections(emails_data)
                _send_family_sections_to_slack(client, channel_id, sections, emails_data)
            else:
                # Basic fallback without AI
                summary = _generate_basic_family_summary(emails_data)
                if client:
                    client.chat_postMessage(
                        channel=channel_id,
                        text=summary
                    )

        except Exception as e:
            client = get_slack_client()
            if client:
                client.chat_postMessage(
                    channel=channel_id,
                    text=f"Error fetching family announcements: {str(e)}"
                )

    # Start background thread
    thread = threading.Thread(target=process_family_async)
    thread.start()

    return jsonify({
        'response_type': 'ephemeral',
        'text': "Fetching family announcements..."
    })


def _generate_family_summary_sections(emails_data):
    """Use AI to generate structured summary sections with email references."""
    import json
    from anthropic import Anthropic

    client = Anthropic(api_key=Config.ANTHROPIC_API_KEY)

    # Prepare email content for AI with index references
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
  "action_required": [
    {{"text": "Fill out T-shirt order form by Friday", "email_index": 0}},
    {{"text": "Payment due for field trip", "email_index": 2}}
  ],
  "upcoming_dates": [
    {{"text": "Feb 14 - Valentine's Day party", "email_index": 1}},
    {{"text": "Feb 17 - No school (Presidents Day)", "email_index": 3}}
  ],
  "important_changes": [
    {{"text": "No hot lunch on Wednesday - pack lunch", "email_index": 4}}
  ],
  "announcements": [
    {{"text": "New after-school program starting next month", "email_index": 5}}
  ]
}}

Rules:
- Only include items that are actually mentioned in the emails
- Be concise but specific (include dates, deadlines, amounts when mentioned)
- If a section has no items, use an empty array []
- email_index must match the EMAIL_X number from the source email
- Focus on what a parent needs to know or do

Emails to analyze:
{''.join(email_texts)}

Return ONLY the JSON object, no other text."""

    response = client.messages.create(
        model=Config.AI_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    # Parse JSON response
    try:
        response_text = response.content[0].text.strip()
        # Handle potential markdown code blocks
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]
        sections = json.loads(response_text)
    except json.JSONDecodeError:
        # Fallback structure if parsing fails
        sections = {
            "action_required": [],
            "upcoming_dates": [],
            "important_changes": [],
            "announcements": [{"text": "Could not parse summary. Please check individual emails.", "email_index": 0}]
        }

    return sections


def _send_family_sections_to_slack(client, channel_id, sections, emails_data):
    """Send each section as a separate Slack message with related emails."""

    section_config = [
        ("action_required", "🚨 Action Required", "Things that need your attention"),
        ("upcoming_dates", "📅 Upcoming Dates", "Events and deadlines to remember"),
        ("important_changes", "⚠️ Important Changes", "Schedule or routine changes"),
        ("announcements", "📢 Announcements", "Good to know"),
    ]

    for section_key, section_title, section_desc in section_config:
        items = sections.get(section_key, [])

        if not items:
            continue  # Skip empty sections

        # Build bullet points
        bullet_text = ""
        email_indices = []
        for item in items:
            bullet_text += f"• {item.get('text', '')}\n"
            idx = item.get('email_index')
            if idx is not None and idx not in email_indices:
                email_indices.append(idx)

        # Send section header with bullet points
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": section_title}
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"_{section_desc}_"}]
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": bullet_text}
            },
            {"type": "divider"}
        ]

        client.chat_postMessage(
            channel=channel_id,
            text=section_title,
            blocks=blocks
        )

        # Send related emails in order
        for idx in email_indices:
            if idx < len(emails_data):
                email = emails_data[idx]
                gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{email['message_id']}"
                sender_name = email['sender'].split('<')[0].strip().strip('"') if '<' in email['sender'] else email['sender']

                client.chat_postMessage(
                    channel=channel_id,
                    text=email['subject'],
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*{email['subject'][:60]}*\n_{sender_name}_"
                            },
                            "accessory": {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Open"},
                                "url": gmail_link,
                                "action_id": "open_family_email"
                            }
                        }
                    ]
                )


def _generate_basic_family_summary(emails_data):
    """Generate a basic summary without AI."""
    lines = ["*Recent announcements:*\n"]

    for email in emails_data[:10]:
        sender_name = email['sender'].split('<')[0].strip().strip('"') if '<' in email['sender'] else email['sender']
        lines.append(f"• *{email['subject'][:50]}* - _{sender_name}_")

    lines.append("\n_Enable AI (ANTHROPIC_API_KEY) for smart summaries with action items._")

    return '\n'.join(lines)


def handle_dm_command(event):
    """Handle DM commands to the bot."""
    text = event.get('text', '').lower().strip()
    user = event.get('user')

    client = get_slack_client()
    if not client:
        return

    if text == 'digest':
        handle_digest_command('', user, event.get('channel'))
    elif text == 'snoozes':
        snoozes = db.get_active_snoozes()
        if snoozes:
            msg = "Your active snoozes:\n"
            for s in snoozes:
                remind_at = datetime.fromisoformat(s['remind_at'])
                msg += f"• {s['subject'][:40]}... → {remind_at.strftime('%b %d, %I:%M %p')}\n"
        else:
            msg = "No active snoozes."
        client.chat_postMessage(channel=event.get('channel'), text=msg)
    elif text == 'help':
        client.chat_postMessage(
            channel=event.get('channel'),
            text="*Available commands:*\n"
                 "• `digest` - Get email summary\n"
                 "• `snoozes` - List active snoozes\n"
                 "• `help` - Show this message"
        )


def handle_shortcut(payload):
    """Handle global shortcuts."""
    callback_id = payload.get('callback_id')
    # Future: implement global shortcuts like "quick_digest"
    return jsonify({'ok': True})


if __name__ == '__main__':
    print("Starting Gmail Action Router Web Server...")
    print(f"AI Features: {'Enabled' if Config.is_ai_enabled() else 'Disabled'}")
    print(f"Slack Interactive: {'Enabled' if Config.is_slack_interactive_enabled() else 'Disabled'}")
    print(f"\nServer running on http://{Config.FLASK_HOST}:{Config.FLASK_PORT}")
    print("Use ngrok to expose this server for Slack webhooks:")
    print(f"  ngrok http {Config.FLASK_PORT}")
    app.run(
        host=Config.FLASK_HOST,
        port=Config.FLASK_PORT,
        debug=Config.FLASK_DEBUG
    )
