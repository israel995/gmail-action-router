#!/usr/bin/env python3
"""
Quick Setup Helper for Gmail Action Router
Guides you through the setup process
"""

import os
import sys
import subprocess


def print_header(text):
    """Print a formatted header"""
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}\n")


def check_python_version():
    """Check if Python version is compatible"""
    if sys.version_info < (3, 7):
        print("❌ Python 3.7 or higher is required")
        print(f"   Current version: {sys.version}")
        return False
    print(f"✓ Python {sys.version_info.major}.{sys.version_info.minor} detected")
    return True


def install_dependencies():
    """Install required packages"""
    print_header("Installing Dependencies")
    
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("\n✓ All dependencies installed successfully!")
        return True
    except subprocess.CalledProcessError:
        print("\n❌ Failed to install dependencies")
        print("   Try running manually: pip install -r requirements.txt")
        return False


def setup_env_file():
    """Help create .env file"""
    print_header("Environment Configuration")
    
    if os.path.exists('.env'):
        print("⚠ .env file already exists!")
        overwrite = input("Do you want to overwrite it? (y/n): ").strip().lower()
        if overwrite != 'y':
            print("Skipping .env creation")
            return True
    
    print("Let's set up your configuration.\n")
    print("You can leave fields blank and fill them in later.\n")
    
    # Slack
    print("📱 SLACK CONFIGURATION")
    slack_token = input("Slack Bot Token (xoxb-...): ").strip()
    slack_channel = input("Slack Channel (default: #email-actions): ").strip() or "#email-actions"
    
    # WhatsApp
    print("\n💬 WHATSAPP CONFIGURATION")
    print("(You can skip this if you don't want WhatsApp notifications)")
    twilio_sid = input("Twilio Account SID: ").strip()
    twilio_token = input("Twilio Auth Token: ").strip()
    twilio_from = input("Twilio WhatsApp Number (whatsapp:+...): ").strip()
    your_number = input("Your WhatsApp Number (whatsapp:+...): ").strip()
    
    # Write .env file
    with open('.env', 'w') as f:
        f.write("# Gmail Action Router Configuration\n\n")
        f.write("# Slack\n")
        f.write(f"SLACK_BOT_TOKEN={slack_token}\n")
        f.write(f"SLACK_CHANNEL={slack_channel}\n\n")
        f.write("# Twilio WhatsApp\n")
        f.write(f"TWILIO_ACCOUNT_SID={twilio_sid}\n")
        f.write(f"TWILIO_AUTH_TOKEN={twilio_token}\n")
        f.write(f"TWILIO_WHATSAPP_FROM={twilio_from}\n")
        f.write(f"YOUR_WHATSAPP_NUMBER={your_number}\n")
    
    print("\n✓ .env file created successfully!")
    return True


def check_gmail_credentials():
    """Check if Gmail credentials exist"""
    print_header("Gmail API Setup")
    
    if os.path.exists('credentials.json'):
        print("✓ credentials.json found!")
        return True
    else:
        print("❌ credentials.json not found")
        print("\nTo set up Gmail API:")
        print("1. Go to https://console.cloud.google.com/")
        print("2. Create a new project")
        print("3. Enable Gmail API")
        print("4. Create OAuth credentials (Desktop app)")
        print("5. Download JSON and save as 'credentials.json'")
        print("\nDetailed instructions: See SETUP_GUIDE.md")
        return False


def test_imports():
    """Test if all imports work"""
    print_header("Testing Imports")
    
    modules = [
        ('google.auth', 'Google Auth'),
        ('google_auth_oauthlib.flow', 'Google OAuth'),
        ('googleapiclient.discovery', 'Google API Client'),
        ('slack_sdk', 'Slack SDK'),
        ('twilio.rest', 'Twilio'),
    ]
    
    all_ok = True
    for module_name, display_name in modules:
        try:
            __import__(module_name)
            print(f"✓ {display_name}")
        except ImportError:
            print(f"❌ {display_name} - Not installed")
            all_ok = False
    
    return all_ok


def customize_filters():
    """Help customize email filters"""
    print_header("Email Filter Customization")
    
    print("Would you like to add important senders? (emails that should be high priority)")
    print("Examples: boss@company.com, client@important.com")
    add_senders = input("Add important senders? (y/n): ").strip().lower()
    
    important_senders = []
    if add_senders == 'y':
        print("\nEnter email addresses (one per line, blank line to finish):")
        while True:
            sender = input("> ").strip()
            if not sender:
                break
            important_senders.append(sender)
    
    print("\n\nWould you like to add senders to skip? (emails to ignore)")
    print("Examples: noreply@, newsletter@, marketing@")
    add_skip = input("Add skip senders? (y/n): ").strip().lower()
    
    skip_senders = ['noreply', 'notifications@']  # Defaults
    if add_skip == 'y':
        print("\nEnter patterns to skip (one per line, blank line to finish):")
        while True:
            pattern = input("> ").strip()
            if not pattern:
                break
            skip_senders.append(pattern)
    
    # Generate config snippet
    if important_senders or len(skip_senders) > 2:
        print("\n" + "="*60)
        print("Add this to your gmail_action_router.py config:")
        print("="*60)
        print("\n'important_senders': [")
        for sender in important_senders:
            print(f"    '{sender}',")
        print("],")
        print("\n'skip_senders': [")
        for pattern in skip_senders:
            print(f"    '{pattern}',")
        print("],")
        print("="*60)


def main():
    """Main setup flow"""
    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║                                                          ║
    ║          Gmail Action Router - Setup Helper             ║
    ║                                                          ║
    ║     Route your Gmail to Slack & WhatsApp!               ║
    ║                                                          ║
    ╚══════════════════════════════════════════════════════════╝
    """)
    
    # Step 1: Check Python
    if not check_python_version():
        return
    
    # Step 2: Install dependencies
    install_deps = input("\nInstall dependencies now? (y/n): ").strip().lower()
    if install_deps == 'y':
        if not install_dependencies():
            print("\n⚠ Continue with setup anyway? Some features may not work.")
            if input("Continue? (y/n): ").strip().lower() != 'y':
                return
    
    # Step 3: Test imports
    if not test_imports():
        print("\n⚠ Some imports failed. Install dependencies first.")
        if input("Continue anyway? (y/n): ").strip().lower() != 'y':
            return
    
    # Step 4: Gmail setup
    check_gmail_credentials()
    
    # Step 5: Environment setup
    setup_env = input("\nSet up .env configuration now? (y/n): ").strip().lower()
    if setup_env == 'y':
        setup_env_file()
    
    # Step 6: Customize filters
    customize = input("\nCustomize email filters now? (y/n): ").strip().lower()
    if customize == 'y':
        customize_filters()
    
    # Final instructions
    print_header("Setup Complete!")
    print("Next steps:")
    print("1. Make sure credentials.json is in place (if not done)")
    print("2. Review and edit .env file with your API keys")
    print("3. Customize filters in gmail_action_router.py")
    print("4. Run: python gmail_action_router.py")
    print("\n📖 For detailed instructions, see: SETUP_GUIDE.md")
    print("\n✨ Happy email routing! ✨\n")


if __name__ == '__main__':
    main()
