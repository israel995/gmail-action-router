"""
AI Assistant module for Gmail Action Router
Uses Claude API for email summarization and smart reply suggestions.
"""

from typing import List, Optional

from config import Config
from database import db


class AIAssistant:
    """AI-powered email assistant using Claude API."""

    def __init__(self):
        self.enabled = Config.is_ai_enabled()
        self.client = None
        self.model = Config.AI_MODEL
        self.summary_threshold = Config.AI_SUMMARY_THRESHOLD

        if self.enabled:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
            except ImportError:
                print("Warning: anthropic package not installed. AI features disabled.")
                self.enabled = False
            except Exception as e:
                print(f"Warning: Failed to initialize Anthropic client: {e}")
                self.enabled = False

    def summarize_email(self, message_id: str, subject: str, sender: str,
                        body: str, force: bool = False) -> Optional[str]:
        """
        Generate a concise summary of an email.

        Args:
            message_id: Gmail message ID (for caching)
            subject: Email subject line
            sender: Sender name/email
            body: Full email body text
            force: Force regeneration even if cached

        Returns:
            2-3 sentence summary or None if AI is disabled/fails
        """
        if not self.enabled:
            return None

        # Check cache first (unless forced)
        if not force:
            cached = db.get_cached_summary(message_id)
            if cached:
                return cached

        # Only summarize if body exceeds threshold
        if len(body) < self.summary_threshold:
            return None

        try:
            prompt = f"""Summarize this email in 2-3 concise sentences. Focus on:
1. The main purpose/request
2. Any action items or deadlines
3. Key information the recipient needs to know

Email:
From: {sender}
Subject: {subject}

{body[:3000]}  # Limit body to avoid token limits

Provide a brief, actionable summary:"""

            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )

            summary = response.content[0].text.strip()

            # Cache the summary
            db.cache_summary(message_id, summary)

            return summary

        except Exception as e:
            print(f"AI summarization error: {e}")
            return None

    def generate_reply_suggestions(self, subject: str, sender: str,
                                   body: str, num_options: int = 3) -> List[str]:
        """
        Generate smart reply suggestions for an email.

        Args:
            subject: Email subject line
            sender: Sender name/email
            body: Full email body text
            num_options: Number of reply options to generate

        Returns:
            List of reply suggestions (brief, detailed, decline)
        """
        if not self.enabled:
            return []

        try:
            prompt = f"""Generate {num_options} different reply options for this email.
The options should be:
1. A brief, positive acknowledgment (1-2 sentences)
2. A more detailed, helpful response (2-3 sentences)
3. A polite decline or deferral (1-2 sentences)

Each reply should be professional, friendly, and ready to send.

Email:
From: {sender}
Subject: {subject}

{body[:2000]}

Provide exactly {num_options} reply options, separated by "---":"""

            response = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )

            text = response.content[0].text.strip()

            # Parse the replies
            replies = [r.strip() for r in text.split('---') if r.strip()]

            # Clean up any numbering or labels
            cleaned_replies = []
            for reply in replies[:num_options]:
                # Remove common prefixes like "1.", "Option 1:", etc.
                lines = reply.split('\n')
                cleaned_lines = []
                for line in lines:
                    line = line.strip()
                    # Skip lines that are just labels
                    if line.lower() in ['brief:', 'detailed:', 'decline:', 'option 1:', 'option 2:', 'option 3:']:
                        continue
                    # Remove leading numbers/bullets
                    if line and line[0].isdigit() and len(line) > 2 and line[1] in '.):':
                        line = line[2:].strip()
                    cleaned_lines.append(line)
                cleaned_reply = ' '.join(cleaned_lines).strip()
                if cleaned_reply:
                    cleaned_replies.append(cleaned_reply)

            return cleaned_replies

        except Exception as e:
            print(f"AI reply suggestion error: {e}")
            return []

    def classify_email_priority(self, subject: str, sender: str, body: str) -> dict:
        """
        Use AI to classify email priority and suggested action.

        Returns:
            Dict with 'priority', 'action', 'reasoning' keys
        """
        if not self.enabled:
            return {}

        try:
            prompt = f"""Analyze this email and classify it:

From: {sender}
Subject: {subject}

{body[:1500]}

Respond in this exact format:
PRIORITY: [urgent/high/medium/low]
ACTION: [reply/review/schedule/sign/read/archive]
REASONING: [1 sentence explaining why]"""

            response = self.client.messages.create(
                model=self.model,
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}]
            )

            text = response.content[0].text.strip()

            # Parse response
            result = {}
            for line in text.split('\n'):
                if line.startswith('PRIORITY:'):
                    result['priority'] = line.replace('PRIORITY:', '').strip().lower()
                elif line.startswith('ACTION:'):
                    result['action'] = line.replace('ACTION:', '').strip().lower()
                elif line.startswith('REASONING:'):
                    result['reasoning'] = line.replace('REASONING:', '').strip()

            return result

        except Exception as e:
            print(f"AI classification error: {e}")
            return {}

    def extract_action_items(self, subject: str, body: str) -> List[str]:
        """
        Extract specific action items from an email.

        Returns:
            List of action items mentioned in the email
        """
        if not self.enabled:
            return []

        try:
            prompt = f"""Extract any specific action items, tasks, or requests from this email.
List only clear, actionable items. If there are no clear action items, respond with "None".

Subject: {subject}

{body[:2000]}

Action items (one per line, or "None"):"""

            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )

            text = response.content[0].text.strip()

            if text.lower() == 'none' or not text:
                return []

            # Parse action items
            items = []
            for line in text.split('\n'):
                line = line.strip()
                # Remove bullets/numbers
                if line and line[0] in '-•*':
                    line = line[1:].strip()
                elif line and line[0].isdigit() and len(line) > 2 and line[1] in '.):':
                    line = line[2:].strip()
                if line and line.lower() != 'none':
                    items.append(line)

            return items

        except Exception as e:
            print(f"AI action extraction error: {e}")
            return []

    def generate_daily_digest_summary(self, emails: list) -> str:
        """
        Generate a natural language summary of today's emails.

        Args:
            emails: List of EmailAction objects

        Returns:
            A paragraph summarizing the day's emails
        """
        if not self.enabled or not emails:
            return ""

        try:
            # Build email list for prompt
            email_list = []
            for e in emails[:20]:  # Limit to 20 emails
                email_list.append(f"- From: {e.sender}, Subject: {e.subject}, Priority: {e.priority.name}")

            prompt = f"""You are a personal email assistant. Provide a brief, natural summary of today's emails.
Highlight urgent items, important senders, and any patterns you notice.
Keep it conversational and helpful, like a briefing from an assistant.

Today's emails ({len(emails)} total):
{chr(10).join(email_list)}

Brief summary (2-3 sentences):"""

            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )

            return response.content[0].text.strip()

        except Exception as e:
            print(f"AI digest summary error: {e}")
            return ""


# Singleton instance
ai_assistant = AIAssistant()
