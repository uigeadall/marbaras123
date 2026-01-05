"""
Resend API email backend for Railway Hobby plan.
Resend is Railway's recommended email service for Hobby plan users.
"""
import logging
import requests
from django.core.mail.backends.base import BaseEmailBackend
from django.conf import settings

log = logging.getLogger(__name__)


class ResendBackend(BaseEmailBackend):
    """
    Email backend using Resend API (recommended for Railway Hobby plan).
    Resend provides HTTPS API instead of SMTP, which works on Railway Hobby plan.
    """
    
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = getattr(settings, "RESEND_API_KEY", "")
        self.api_url = "https://api.resend.com/emails"
        log.info("🚀 ResendBackend initialized")
        
        if not self.api_key:
            log.warning("⚠️  RESEND_API_KEY not set - emails will not be sent")
    
    def send_messages(self, email_messages):
        """
        Send one or more EmailMessage objects and return the number of emails sent.
        """
        if not email_messages:
            return 0
        
        if not self.api_key:
            if not self.fail_silently:
                raise ValueError("RESEND_API_KEY is not configured")
            log.warning("⚠️  Skipping email send - RESEND_API_KEY not configured")
            return 0
        
        num_sent = 0
        for message in email_messages:
            try:
                if self._send_email(message):
                    num_sent += 1
            except Exception as e:
                if not self.fail_silently:
                    raise
                log.error("❌ Failed to send email via Resend: %s", e)
        
        return num_sent
    
    def _send_email(self, message):
        """Send a single email message via Resend API."""
        try:
            # Prepare email data
            email_data = {
                "from": message.from_email,
                "to": message.to,
                "subject": message.subject,
            }
            
            # Add CC and BCC if present
            if message.cc:
                email_data["cc"] = message.cc
            if message.bcc:
                email_data["bcc"] = message.bcc
            
            # Handle email body
            if message.body:
                email_data["text"] = message.body
            
            # Handle HTML alternative
            if hasattr(message, 'alternatives') and message.alternatives:
                for content, mimetype in message.alternatives:
                    if mimetype == 'text/html':
                        email_data["html"] = content
                        break
            
            # If no HTML but we have text, use text as HTML too
            if "html" not in email_data and "text" in email_data:
                email_data["html"] = email_data["text"]
            
            # Send via Resend API
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            
            log.info("  Sending email via Resend API...")
            log.info("    From: %s", email_data["from"])
            log.info("    To: %s", email_data["to"])
            log.info("    Subject: %s", email_data["subject"])
            
            response = requests.post(
                self.api_url,
                json=email_data,
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                log.info("✅ Email sent successfully via Resend (ID: %s)", result.get("id", "unknown"))
                return True
            else:
                error_msg = response.text
                log.error("❌ Resend API error (status %s): %s", response.status_code, error_msg)
                if not self.fail_silently:
                    raise Exception(f"Resend API error: {error_msg}")
                return False
                
        except requests.exceptions.RequestException as e:
            log.error("❌ Network error when sending email via Resend: %s", e)
            if not self.fail_silently:
                raise
            return False
        except Exception as e:
            log.error("❌ Error sending email via Resend: %s", e)
            log.exception("Exception details:")
            if not self.fail_silently:
                raise
            return False

