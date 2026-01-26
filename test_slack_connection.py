#!/usr/bin/env python3
"""
Quick Slack Test - Verify your bot can post messages
"""

import os
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Load environment variables
load_dotenv()

# Get configuration
slack_token = os.getenv('SLACK_BOT_TOKEN')
slack_channel = os.getenv('SLACK_CHANNEL', '#email-actions')

print("=" * 60)
print("Slack Connection Test")
print("=" * 60)

# Check token
if not slack_token:
    print("❌ ERROR: SLACK_BOT_TOKEN not found in .env file")
    print("   Make sure your .env file has:")
    print("   SLACK_BOT_TOKEN=xoxb-your-token-here")
    exit(1)

if not slack_token.startswith('xoxb-'):
    print("⚠️  WARNING: Token doesn't start with 'xoxb-'")
    print(f"   Your token starts with: {slack_token[:10]}...")
    print("   Make sure you're using the Bot User OAuth Token")

print(f"✓ Token found: {slack_token[:20]}...")
print(f"✓ Channel: {slack_channel}")
print()

# Create Slack client
client = WebClient(token=slack_token)

# Test authentication
print("Testing Slack authentication...")
try:
    auth_response = client.auth_test()
    print(f"✓ Authenticated as: {auth_response['user']}")
    print(f"✓ Team: {auth_response['team']}")
    print(f"✓ Bot User ID: {auth_response['user_id']}")
    print()
except SlackApiError as e:
    print(f"❌ Authentication failed: {e.response['error']}")
    print()
    if e.response['error'] == 'invalid_auth':
        print("Your token is invalid. Check:")
        print("1. Token is correct in .env file")
        print("2. Token starts with 'xoxb-'")
        print("3. No extra spaces in the token")
    exit(1)

# Test posting a message
print(f"Attempting to post test message to {slack_channel}...")
try:
    response = client.chat_postMessage(
        channel=slack_channel,
        text="🎉 Test successful! Gmail Router is connected to Slack!",
        blocks=[
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "✅ Slack Connection Test"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "If you can see this message, your Gmail Router is properly connected to Slack! 🚀"
                }
            }
        ]
    )
    print("✓ Message sent successfully!")
    print(f"✓ Message timestamp: {response['ts']}")
    print()
    print("=" * 60)
    print("SUCCESS! Check your Slack channel for the test message.")
    print("=" * 60)
    
except SlackApiError as e:
    print(f"❌ Failed to post message: {e.response['error']}")
    print()
    
    # Helpful error messages
    if e.response['error'] == 'channel_not_found':
        print("Channel not found. Try:")
        print(f"1. Create a channel named {slack_channel}")
        print("2. Make sure the channel name in .env matches exactly")
        print("3. Channel names must include the # symbol")
    
    elif e.response['error'] == 'not_in_channel':
        print("Bot is not in the channel. Try:")
        print(f"1. Go to {slack_channel} in Slack")
        print("2. Type: /invite @Gmail Router")
        print("3. Or make sure you added 'chat:write.public' scope")
    
    elif e.response['error'] == 'invalid_auth':
        print("Authentication failed. Check:")
        print("1. Token in .env is correct")
        print("2. Token starts with 'xoxb-'")
        print("3. App is installed to workspace")
    
    else:
        print("Other possible issues:")
        print("1. Channel name must start with #")
        print("2. Bot needs proper permissions")
        print("3. App must be installed to workspace")
    
    exit(1)
