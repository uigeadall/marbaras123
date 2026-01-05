"""
SendGrid API email backend for Railway Hobby plan.
SendGrid provides HTTPS API instead of SMTP, which works on Railway Hobby plan.
"""
import logging
import requests
from django.core.mail.backends.base import BaseEmailBackend
from django.conf import settings

log = logging.getLogger(__name__)


class SendGridBackend(BaseEmailBackend):
    """
    Email backend using SendGrid API (works on Railway Hobby plan).
    SendGrid provides HTTPS API instead of SMTP.
    """
    
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = getattr(settings, "SENDGRID_API_KEY", "")
        self.api_url = "https://api.sendgrid.com/v3/mail/send"
        log.info("🚀 SendGridBackend initialized")
        
        if not self.api_key:
            log.warning("⚠️  SENDGRID_API_KEY not set - emails will not be sent")
    
    def send_messages(self, email_messages):
        """
        Send one or more EmailMessage objects and return the number of emails sent.
        """
        if not email_messages:
            return 0
        
        if not self.api_key:
            if not self.fail_silently:
                raise ValueError("SENDGRID_API_KEY is not configured")
            log.warning("⚠️  Skipping email send - SENDGRID_API_KEY not configured")
            return 0
        
        num_sent = 0
        for message in email_messages:
            try:
                if self._send_email(message):
                    num_sent += 1
            except Exception as e:
                if not self.fail_silently:
                    raise
                log.error("❌ Failed to send email via SendGrid: %s", e)
        
        return num_sent
    
    def _send_email(self, message):
        """Send a single email message via SendGrid API."""
        try:
            # Prepare email data for SendGrid API
            email_data = {
                "personalizations": [{
                    "to": [{"email": email} for email in message.to],
                }],
                "from": {"email": message.from_email},
                "subject": message.subject,
            }
            
            # Add CC and BCC if present
            if message.cc:
                email_data["personalizations"][0]["cc"] = [{"email": email} for email in message.cc]
            if message.bcc:
                email_data["personalizations"][0]["bcc"] = [{"email": email} for email in message.bcc]
            
            # Handle email content
            content = []
            if message.body:
                content.append({"type": "text/plain", "value": message.body})
            
            # Handle HTML alternative
            if hasattr(message, 'alternatives') and message.alternatives:
                for email_content, mimetype in message.alternatives:
                    if mimetype == 'text/html':
                        content.append({"type": "text/html", "value": email_content})
                        break
            
            email_data["content"] = content
            
            # Send via SendGrid API
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            
            log.info("  Sending email via SendGrid API...")
            log.info("    From: %s", email_data["from"]["email"])
            log.info("    To: %s", [email["email"] for email in email_data["personalizations"][0]["to"]])
            log.info("    Subject: %s", email_data["subject"])
            
            response = requests.post(
                self.api_url,
                json=email_data,
                headers=headers,
                timeout=30
            )
            
            if response.status_code in (200, 202):
                log.info("✅ Email sent successfully via SendGrid")
                return True
            else:
                error_msg = response.text
                log.error("❌ SendGrid API error (status %s): %s", response.status_code, error_msg)
                if not self.fail_silently:
                    raise Exception(f"SendGrid API error: {error_msg}")
                return False
                
        except requests.exceptions.RequestException as e:
            log.error("❌ Network error when sending email via SendGrid: %s", e)
            if not self.fail_silently:
                raise
            return False
        except Exception as e:
            log.error("❌ Error sending email via SendGrid: %s", e)
            log.exception("Exception details:")
            if not self.fail_silently:
                raise
            return False

