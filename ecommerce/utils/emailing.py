import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, mail_admins
from django.template.loader import render_to_string
from decimal import Decimal

log = logging.getLogger(__name__)

def _log_email_config():
    """Log current email configuration for debugging."""
    email_backend = getattr(settings, "EMAIL_BACKEND", "not set")
    # Check which backend is being used
    if "resend_backend" in email_backend or "ResendBackend" in email_backend:
        log.info("  ✅ Using Resend API backend (works on Railway Hobby plan)")
    elif "sendgrid_backend" in email_backend or "SendGridBackend" in email_backend:
        log.info("  ✅ Using SendGrid API backend (works on Railway Hobby plan)")
    elif "smtp_backend" in email_backend or "SMTPSBackend" in email_backend:
        log.info("  ✅ Using custom SMTPS backend (requires Railway Pro plan)")
    else:
        log.warning("  ⚠️  Using standard Django SMTP backend")
    email_host = getattr(settings, "EMAIL_HOST", "not set")
    email_port = getattr(settings, "EMAIL_PORT", "not set")
    email_user = getattr(settings, "EMAIL_HOST_USER", "not set")
    email_use_tls = getattr(settings, "EMAIL_USE_TLS", False)
    email_use_ssl = getattr(settings, "EMAIL_USE_SSL", False)
    default_from = getattr(settings, "DEFAULT_FROM_EMAIL", "not set")
    
    # Determine protocol
    if email_use_ssl and email_port == 465:
        protocol = "SMTPS (SMTP over SSL)"
    elif email_use_tls and email_port == 587:
        protocol = "SMTP with STARTTLS"
    else:
        protocol = "SMTP (plain)"
    
    log.info("=" * 60)
    log.info("EMAIL CONFIGURATION:")
    log.info("  EMAIL_BACKEND: %s", email_backend)
    log.info("  Protocol: %s", protocol)
    log.info("  EMAIL_HOST: %s", email_host)
    log.info("  EMAIL_PORT: %s", email_port)
    log.info("  EMAIL_HOST_USER: %s", email_user)
    log.info("  EMAIL_USE_TLS: %s", email_use_tls)
    log.info("  EMAIL_USE_SSL: %s", email_use_ssl)
    log.info("  DEFAULT_FROM_EMAIL: %s", default_from)
    log.info("=" * 60)

def _base_url(request=None, fallback=""):
    if request:
        scheme = "https" if request.is_secure() else "http"
        return f"{scheme}://{request.get_host()}"
    return fallback

def send_welcome_email(user, base_url) -> bool:
    """Send welcome email to user. Returns True on success, False on failure.
    Never raises exceptions - all errors are logged and swallowed."""
    try:
        if not getattr(user, "email", None):
            log.warning("Welcome email skipped, user has no email: %s", user)
            return False

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "no-reply@example.com"
        ctx = {"user": user, "base_url": base_url}

        # Log email configuration and sending attempt
        _log_email_config()
        log.info("📧 ATTEMPTING TO SEND WELCOME EMAIL")
        log.info("  To: %s", user.email)
        log.info("  From: %s", from_email)
        log.info("  Subject: Welcome to Marbaras ✨")
        log.info("  Base URL: %s", base_url)

        try:
            subject = "Welcome to Marbaras ✨"
            text = render_to_string("emails/welcome.txt", ctx)
            html = render_to_string("emails/welcome.html", ctx)
            msg = EmailMultiAlternatives(subject, text, from_email, [user.email])
            msg.attach_alternative(html, "text/html")
            
            email_port = getattr(settings, "EMAIL_PORT", "not set")
            email_use_ssl = getattr(settings, "EMAIL_USE_SSL", False)
            if email_use_ssl and email_port == 465:
                protocol = "SMTPS (smtps://)"
            elif getattr(settings, "EMAIL_USE_TLS", False) and email_port == 587:
                protocol = "SMTP with STARTTLS"
            else:
                protocol = "SMTP"
            
            log.info("  Message created, attempting to send via %s...", protocol)
            log.info("  Connection details:")
            log.info("    Protocol: %s", protocol)
            log.info("    Host: %s:%s", getattr(settings, "EMAIL_HOST", "not set"), email_port)
            log.info("    User: %s", getattr(settings, "EMAIL_HOST_USER", "not set"))
            log.info("    TLS: %s, SSL: %s", getattr(settings, "EMAIL_USE_TLS", False), email_use_ssl)
            
            # Try sending with fail_silently=False first to see the actual error
            try:
                result = msg.send(fail_silently=False)
                log.info("✅ Welcome email sent successfully to %s (result: %s)", user.email, result)
                return True
            except Exception as smtp_error:
                log.error("❌ SMTP Error when sending welcome email to %s:", user.email)
                log.error("  Error type: %s", type(smtp_error).__name__)
                log.error("  Error message: %s", str(smtp_error))
                log.exception("  Full exception traceback:")
                
                # Try again with fail_silently=True to prevent blocking
                try:
                    log.info("  Retrying with fail_silently=True...")
                    result = msg.send(fail_silently=True)
                    log.info("  Retry result: %s (0 = failed, 1 = success)", result)
                except Exception as retry_error:
                    log.error("  Retry also failed: %s", retry_error)
                
                return False
        except Exception as e:
            log.error("❌ Failed to send welcome email to %s: %s", getattr(user, "email", None), e)
            log.exception("Exception details:")
            return False
    except Exception as e:
        # Catch any unexpected errors (e.g., template rendering, settings access)
        log.error("❌ Unexpected error in send_welcome_email for user %s: %s", getattr(user, "email", "unknown"), e)
        log.exception("Exception details:")
        return False

def send_welcome_email_with_promo(user, base_url, promo_code) -> bool:
    """Send welcome email with promo code to email subscriber. Returns True on success, False on failure."""
    try:
        email = getattr(user, "email", None)
        if not email:
            log.warning("Welcome email with promo skipped, no email: %s", user)
            return False

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "no-reply@example.com"
        ctx = {
            "user": user, 
            "base_url": base_url,
            "promo_code": promo_code
        }

        log.info("📧 ATTEMPTING TO SEND WELCOME EMAIL WITH PROMO CODE")
        log.info("  To: %s", email)
        log.info("  Promo Code: %s", promo_code)

        try:
            subject = "🎁 Welcome to Marbaras - Your 5% Discount Code!"
            text = render_to_string("emails/welcome_promo.txt", ctx)
            html = render_to_string("emails/welcome_promo.html", ctx)
            msg = EmailMultiAlternatives(subject, text, from_email, [email])
            msg.attach_alternative(html, "text/html")
            
            result = msg.send(fail_silently=False)
            log.info("  ✅ Welcome email with promo sent successfully")
            return True
        except Exception as smtp_error:
            log.error("❌ SMTP Error when sending welcome email with promo to %s:", email)
            log.error("  Error: %s", str(smtp_error))
            log.exception("  Full exception traceback:")
            return False
    except Exception as e:
        log.error("❌ Unexpected error in send_welcome_email_with_promo for %s: %s", getattr(user, "email", "unknown"), e)
        log.exception("Exception details:")
        return False


def send_order_confirmation_email(order, base_url, notify_admin=False) -> bool:
    """Send order confirmation email to customer."""
    # Prefer an explicit order email (e.g., shipping email), else fallback to user's email.
    recipient = getattr(order, "email", None) or getattr(getattr(order, "user", None), "email", None)
    if not recipient:
        log.warning("Order email skipped, no recipient for order #%s", order.id)
        return False

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "no-reply@example.com"

    # Avoid N+1 when templates access item.product and variant
    items = order.items.select_related("product", "variant").all()

    # Calculate totals
    subtotal = sum(
        (item.product.get_discounted_price() * Decimal(item.quantity))
        for item in items
    )
    shipping_cost = order.shipping_option.price if order.shipping_option else Decimal("0.00")
    discount_amount = Decimal("0.00")
    if order.coupon:
        discount_amount = subtotal - order.coupon.apply(subtotal)
    total = order.total_price

    ctx = {
        "order": order,
        "items": items,
        "base_url": base_url,
        "subtotal": subtotal,
        "shipping_cost": shipping_cost,
        "discount_amount": discount_amount,
        "total": total,
    }
    
    # Log email configuration and sending attempt
    _log_email_config()
    log.info("📧 ATTEMPTING TO SEND ORDER CONFIRMATION EMAIL")
    log.info("  Order ID: #%s", order.id)
    log.info("  To: %s", recipient)
    log.info("  From: %s", from_email)
    log.info("  Subject: Order #%s - Marbaras ✨", order.id)
    log.info("  Total: $%s", total)
    log.info("  Base URL: %s", base_url)
    
    try:
        subject = f"Order #{order.id} - Marbaras ✨"
        text = render_to_string("emails/order_confirmation.txt", ctx)
        html = render_to_string("emails/order_confirmation.html", ctx)

        msg = EmailMultiAlternatives(subject, text, from_email, [recipient])
        msg.attach_alternative(html, "text/html")
        
        log.info("  Message created, attempting to send via SMTP...")
        result = msg.send(fail_silently=True)  # Changed to True to prevent blocking if email fails
        log.info("✅ Order confirmation email sent successfully to %s (result: %s)", recipient, result)

        if notify_admin:
            log.info("  Sending admin notification email...")
            send_admin_order_notification(order, base_url, items, subtotal, shipping_cost, discount_amount, total)
        return True
    except Exception as e:
        log.error("❌ Failed to send order email for #%s: %s", order.id, e)
        log.exception("Exception details:")
        return False


def send_order_shipped_email(order, base_url, tracking_number=None) -> bool:
    """Send email when order is shipped."""
    recipient = getattr(order, "email", None) or getattr(getattr(order, "user", None), "email", None)
    if not recipient:
        log.warning("Order shipped email skipped, no recipient for order #%s", getattr(order, 'id', 'unknown'))
        return False

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "no-reply@example.com"
    items = order.items.select_related("product", "variant").all()

    # Use tracking_number from parameter if provided, otherwise use order.tracking_number
    final_tracking_number = tracking_number or order.tracking_number
    
    ctx = {
        "order": order,
        "items": items,
        "base_url": base_url,
        "tracking_number": final_tracking_number,
        "shipping_option": order.shipping_option,
    }
    
    # Log email configuration and sending attempt
    _log_email_config()
    log.info("📧 ATTEMPTING TO SEND ORDER SHIPPED EMAIL")
    log.info("  Order ID: #%s", order.id)
    log.info("  To: %s", recipient)
    log.info("  From: %s", from_email)
    log.info("  Subject: Your order #%s has been shipped - Marbaras", order.id)
    log.info("  Tracking Number: %s", tracking_number or "N/A")
    log.info("  Base URL: %s", base_url)
    
    try:
        subject = f"Your order #{order.id} has been shipped - Marbaras"
        text = render_to_string("emails/order_shipped.txt", ctx)
        html = render_to_string("emails/order_shipped.html", ctx)

        msg = EmailMultiAlternatives(subject, text, from_email, [recipient])
        msg.attach_alternative(html, "text/html")
        
        log.info("  Message created, attempting to send via SMTP...")
        result = msg.send(fail_silently=True)  # Changed to True to prevent blocking if email fails
        log.info("✅ Order shipped email sent successfully to %s (result: %s)", recipient, result)
        return True
    except Exception as e:
        log.error("❌ Failed to send order shipped email for #%s: %s", order.id, e)
        log.exception("Exception details:")
        return False


def send_admin_order_notification(order, base_url, items, subtotal, shipping_cost, discount_amount, total) -> bool:
    """Send detailed order notification email to admin."""
    try:
        from django.conf import settings
        
        # Get admin email from settings
        admin_email = getattr(settings, 'ADMIN_EMAIL', None)
        if not admin_email:
            # Try to get from ADMINS setting
            admins = getattr(settings, 'ADMINS', [])
            if admins and len(admins) > 0:
                admin_email = admins[0][1] if isinstance(admins[0], tuple) else admins[0]
        
        if not admin_email:
            log.warning("⚠️  No admin email configured. Set ADMIN_EMAIL in settings.")
            return False
        
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "no-reply@example.com"
        recipient = getattr(order, "email", None) or getattr(getattr(order, "user", None), "email", None)
        customer_name = getattr(order, "full_name", None) or (getattr(order.user, "username", None) if order.user else "Guest")
        
        ctx = {
            "order": order,
            "items": items,
            "base_url": base_url,
            "subtotal": subtotal,
            "shipping_cost": shipping_cost,
            "discount_amount": discount_amount,
            "total": total,
            "customer_name": customer_name,
            "customer_email": recipient,
            "admin_url": f"{base_url}/admin/ecommerce/order/{order.id}/",
        }
        
        log.info("📧 ATTEMPTING TO SEND ADMIN ORDER NOTIFICATION")
        log.info("  Order ID: #%s", order.id)
        log.info("  To: %s", admin_email)
        log.info("  From: %s", from_email)
        log.info("  Subject: 🛒 New Order #%s - %s", order.id, customer_name)
        
        try:
            subject = f"🛒 New Order #%s - %s" % (order.id, customer_name)
            
            # Try to render HTML template, fallback to text if not available
            try:
                html = render_to_string("emails/admin_order_notification.html", ctx)
                text = render_to_string("emails/admin_order_notification.txt", ctx)
            except Exception as template_error:
                log.warning("  Template not found, using simple text email: %s", template_error)
                # Fallback to simple text email
                text = f"""New Order #{order.id}

Customer: {customer_name}
Email: {recipient}
Phone: {order.phone}

Shipping Address:
{order.address}
{order.city}, {order.postal_code}
{order.country or ''}

Order Items:
"""
                for item in items:
                    product_name = item.product.name
                    if item.variant:
                        product_name += f" (Size: {item.variant.size})"
                    text += f"- {item.quantity}x {product_name} - ${item.product.get_discounted_price() * Decimal(item.quantity)}\n"
                
                text += f"""
Subtotal: ${subtotal}
Shipping: ${shipping_cost}
"""
                if discount_amount > 0:
                    text += f"Discount: -${discount_amount}\n"
                text += f"""
Total: ${total}

View order: {base_url}/admin/ecommerce/order/{order.id}/
"""
                html = None
            
            msg = EmailMultiAlternatives(subject, text, from_email, [admin_email])
            if html:
                msg.attach_alternative(html, "text/html")
            
            result = msg.send(fail_silently=True)
            log.info("✅ Admin order notification email sent successfully to %s (result: %s)", admin_email, result)
            return True
            
        except Exception as e:
            log.error("❌ Failed to send admin notification email: %s", e)
            log.exception("Exception details:")
            return False
            
    except Exception as e:
        log.error("❌ Error in send_admin_order_notification: %s", e)
        log.exception("Exception details:")
        return False


def send_password_reset_email(user, reset_url, base_url) -> bool:
    """Send password reset email."""
    if not getattr(user, "email", None):
        log.warning("Password reset email skipped, user has no email: %s", user)
        return False

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "no-reply@example.com"
    ctx = {"user": user, "reset_url": reset_url, "base_url": base_url}

    # Log email configuration and sending attempt
    _log_email_config()
    log.info("📧 ATTEMPTING TO SEND PASSWORD RESET EMAIL")
    log.info("  To: %s", user.email)
    log.info("  From: %s", from_email)
    log.info("  Subject: Password Reset - Marbaras")
    log.info("  Reset URL: %s", reset_url)
    log.info("  Base URL: %s", base_url)

    try:
        subject = "Password Reset - Marbaras"
        text = render_to_string("emails/password_reset.txt", ctx)
        html = render_to_string("emails/password_reset.html", ctx)

        msg = EmailMultiAlternatives(subject, text, from_email, [user.email])
        msg.attach_alternative(html, "text/html")
        
        log.info("  Message created, attempting to send via SMTP...")
        result = msg.send(fail_silently=True)  # Changed to True to prevent blocking if email fails
        log.info("✅ Password reset email sent successfully to %s (result: %s)", user.email, result)
        return True
    except Exception as e:
        log.error("❌ Failed to send password reset email to %s: %s", getattr(user, "email", None), e)
        log.exception("Exception details:")
        return False
