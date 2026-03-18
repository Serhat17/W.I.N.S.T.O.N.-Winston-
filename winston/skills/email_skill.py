"""
Email Skill - Send and read emails via SMTP/IMAP.
Supports Gmail, Outlook, and other email providers.
"""

import email
import imaplib
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import decode_header
from typing import Optional

from winston.config import EmailConfig
from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.email")


class EmailSkill(BaseSkill):
    """Send and read emails."""

    name = "email"
    description = (
        "Send emails to recipients, read recent emails, and search inbox. "
        "Use this when the user asks to send, compose, write, or read emails."
    )
    parameters = {
        "action": "Action to perform: 'send', 'read', 'search'",
        "to": "(send) Recipient email address",
        "subject": "(send) Email subject line",
        "body": "(send) Email body text",
        "count": "(read) Number of recent emails to fetch (default: 5)",
        "query": "(search) Search query for email search",
    }

    def __init__(self, config: EmailConfig):
        super().__init__(config)
        self.email_config = config

    def execute(self, **kwargs) -> SkillResult:
        """Execute email action."""
        action = kwargs.get("action", "send")

        if action == "send":
            return self._send_email(
                to=kwargs.get("to", ""),
                subject=kwargs.get("subject", ""),
                body=kwargs.get("body", ""),
                cc=kwargs.get("cc"),
            )
        elif action == "read":
            return self._read_emails(count=int(kwargs.get("count", 5)))
        elif action == "search":
            return self._search_emails(query=kwargs.get("query", ""))
        else:
            return SkillResult(
                success=False,
                message=f"Unknown email action: {action}. Use 'send', 'read', or 'search'.",
            )

    def _send_email(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = None,
    ) -> SkillResult:
        """Send an email."""
        # Validate configuration
        if not self.email_config.smtp_server or not self.email_config.email:
            return SkillResult(
                success=False,
                message=(
                    "Email is not configured. Please set the following environment variables:\n"
                    "WINSTON_SMTP_SERVER, WINSTON_EMAIL, WINSTON_EMAIL_PASSWORD\n"
                    "Or configure them in config/settings.yaml"
                ),
            )

        # Validate parameters
        error = self.validate_params(["to", "subject", "body"], {"to": to, "subject": subject, "body": body})
        if error:
            return SkillResult(success=False, message=error)

        try:
            msg = MIMEMultipart()
            msg["From"] = self.email_config.email
            msg["To"] = to
            msg["Subject"] = subject
            if cc:
                msg["Cc"] = cc

            msg.attach(MIMEText(body, "plain"))

            # Connect and send
            with smtplib.SMTP(self.email_config.smtp_server, self.email_config.smtp_port) as server:
                if self.email_config.use_tls:
                    server.starttls()
                server.login(self.email_config.email, self.email_config.password)

                recipients = [to]
                if cc:
                    recipients.extend(cc.split(","))
                server.sendmail(self.email_config.email, recipients, msg.as_string())

            logger.info(f"Email sent to {to}: {subject}")
            return SkillResult(
                success=True,
                message=f"Email sent successfully to {to} with subject '{subject}'.",
            )

        except smtplib.SMTPAuthenticationError:
            return SkillResult(
                success=False,
                message="Email authentication failed. Please check your email credentials. "
                        "If using Gmail, you need an App Password.",
            )
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return SkillResult(success=False, message=f"Failed to send email: {str(e)}")

    def _read_emails(self, count: int = 5) -> SkillResult:
        """Read recent emails from inbox."""
        if not self.email_config.imap_server or not self.email_config.email:
            return SkillResult(
                success=False,
                message="Email not configured for reading. Set WINSTON_IMAP_SERVER.",
            )

        try:
            mail = imaplib.IMAP4_SSL(
                self.email_config.imap_server, self.email_config.imap_port
            )
            mail.login(self.email_config.email, self.email_config.password)
            mail.select("INBOX")

            # Get recent emails
            _, message_ids = mail.search(None, "ALL")
            ids = message_ids[0].split()
            recent_ids = ids[-count:] if len(ids) >= count else ids

            emails = []
            for eid in reversed(recent_ids):
                _, msg_data = mail.fetch(eid, "(RFC822)")
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                subject = self._decode_header(msg["Subject"])
                sender = self._decode_header(msg["From"])
                date = msg["Date"]

                # Get body
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode(errors="ignore")
                            break
                else:
                    body = msg.get_payload(decode=True).decode(errors="ignore")

                emails.append({
                    "from": sender,
                    "subject": subject,
                    "date": date,
                    "preview": body[:200].strip(),
                })

            mail.logout()

            if not emails:
                return SkillResult(success=True, message="No emails found in inbox.")

            # Format response
            response = f"Here are your {len(emails)} most recent emails:\n\n"
            for i, e in enumerate(emails, 1):
                response += (
                    f"{i}. From: {e['from']}\n"
                    f"   Subject: {e['subject']}\n"
                    f"   Date: {e['date']}\n"
                    f"   Preview: {e['preview'][:100]}...\n\n"
                )

            return SkillResult(success=True, message=response, data=emails)

        except Exception as e:
            logger.error(f"Failed to read emails: {e}")
            return SkillResult(success=False, message=f"Failed to read emails: {str(e)}")

    def _search_emails(self, query: str) -> SkillResult:
        """Search emails by subject or sender."""
        if not self.email_config.imap_server:
            return SkillResult(success=False, message="Email not configured for reading.")

        try:
            mail = imaplib.IMAP4_SSL(
                self.email_config.imap_server, self.email_config.imap_port
            )
            mail.login(self.email_config.email, self.email_config.password)
            mail.select("INBOX")

            # Search by subject and from
            _, subject_ids = mail.search(None, f'SUBJECT "{query}"')
            _, from_ids = mail.search(None, f'FROM "{query}"')

            all_ids = set(subject_ids[0].split() + from_ids[0].split())

            emails = []
            for eid in list(all_ids)[-10:]:  # Limit to 10 results
                if not eid:
                    continue
                _, msg_data = mail.fetch(eid, "(RFC822)")
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                emails.append({
                    "from": self._decode_header(msg["From"]),
                    "subject": self._decode_header(msg["Subject"]),
                    "date": msg["Date"],
                })

            mail.logout()

            if not emails:
                return SkillResult(
                    success=True,
                    message=f"No emails found matching '{query}'.",
                )

            response = f"Found {len(emails)} emails matching '{query}':\n\n"
            for i, e in enumerate(emails, 1):
                response += f"{i}. {e['subject']} - from {e['from']} ({e['date']})\n"

            return SkillResult(success=True, message=response, data=emails)

        except Exception as e:
            logger.error(f"Email search failed: {e}")
            return SkillResult(success=False, message=f"Email search failed: {str(e)}")

    def _decode_header(self, header: str) -> str:
        """Decode email header."""
        if header is None:
            return ""
        decoded_parts = decode_header(header)
        result = ""
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                result += part.decode(charset or "utf-8", errors="ignore")
            else:
                result += part
        return result
