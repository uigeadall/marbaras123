"""
Probe DHL API endpoints to discover which API product the configured
Global Mail credentials actually belong to.

Usage (Railway):
    python manage.py probe_dhl_api

The command does not send any shipment requests. It only calls
authentication / health endpoints with Basic Auth or OAuth 2.0 client
credentials, and prints a concise report of which DHL API accepts the
credentials.
"""
from __future__ import annotations

import base64
import json
import sys
from typing import Optional

import requests
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Probe multiple DHL API products to find which one accepts the configured Global Mail credentials."

    def add_arguments(self, parser):
        parser.add_argument(
            "--key",
            help="Override GLOBAL_MAIL_API_KEY (consumerKey/clientId) for this probe only.",
        )
        parser.add_argument(
            "--secret",
            help="Override GLOBAL_MAIL_API_SECRET (consumerSecret/clientSecret) for this probe only.",
        )
        parser.add_argument(
            "--account",
            help="Override GLOBAL_MAIL_ACCOUNT_NUMBER (userId or account no) for this probe only.",
        )

    def handle(self, *args, **opts):
        key = opts.get("key") or getattr(settings, "GLOBAL_MAIL_API_KEY", "") or ""
        secret = opts.get("secret") or getattr(settings, "GLOBAL_MAIL_API_SECRET", "") or ""
        account = opts.get("account") or getattr(settings, "GLOBAL_MAIL_ACCOUNT_NUMBER", "") or ""

        if not key or not secret:
            self.stderr.write(self.style.ERROR(
                "GLOBAL_MAIL_API_KEY and/or GLOBAL_MAIL_API_SECRET not configured."
            ))
            sys.exit(2)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "Probing DHL API endpoints for these credentials:"
        ))
        self.stdout.write(f"  consumerKey    : {key[:10]}…  (len={len(key)})")
        self.stdout.write(f"  consumerSecret : {secret[:4]}…    (len={len(secret)})")
        self.stdout.write(f"  userId/account : {account or '(not set)'}")
        self.stdout.write("")

        results = []

        # 1. MyDHL API (DHL Express) — Basic Auth on shipments endpoint.
        #    Just GET against a harmless endpoint to check auth.
        results.append(self._probe_mydhl(key, secret))

        # 2. DHL eCommerce Solutions — OAuth 2.0 token endpoint.
        results.append(self._probe_ecommerce_solutions(key, secret))

        # 3. DHL Parcel DE Shipping Post Parcel Germany — OAuth 2.0.
        results.append(self._probe_parcel_de_shipping(key, secret, account))

        # 4. DHL Parcel DE Customer Account — Basic Auth.
        results.append(self._probe_parcel_de_account(key, secret))

        # 5. DHL Deutsche Post International (Mail) — Basic Auth.
        results.append(self._probe_deutsche_post_international(key, secret))

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Summary"))
        any_ok = False
        for r in results:
            label = f"  {r['api']:<48}"
            if r["ok"]:
                any_ok = True
                self.stdout.write(self.style.SUCCESS(f"{label} ✅ {r['status']}  {r['note']}"))
            else:
                self.stdout.write(self.style.WARNING(f"{label} ❌ {r['status']}  {r['note']}"))

        self.stdout.write("")
        if any_ok:
            self.stdout.write(self.style.SUCCESS(
                "At least one DHL API accepted the credentials. "
                "Tell the assistant which API was marked ✅ — it will implement the carrier class for that one."
            ))
        else:
            self.stdout.write(self.style.ERROR(
                "No tested DHL API accepted the credentials. "
                "The app in DHL Developer Portal may not be subscribed to a shipping product yet, "
                "or the credentials might be for a different DHL product we did not probe. "
                "Check DHL Developer Portal -> My Apps -> Subscribed APIs."
            ))

    # ------------------------------------------------------------------
    # Probe helpers
    # ------------------------------------------------------------------

    def _result(self, api: str, ok: bool, status: str, note: str = "") -> dict:
        return {"api": api, "ok": ok, "status": status, "note": note}

    def _probe_mydhl(self, key: str, secret: str) -> dict:
        """MyDHL API (DHL Express) uses Basic Auth directly on the resource URL."""
        api = "MyDHL API (DHL Express)"
        url = "https://express.api.dhl.com/mydhlapi/test/address-validate"
        try:
            r = requests.get(
                url,
                auth=(key, secret),
                params={"type": "delivery", "countryCode": "DE", "postalCode": "53113"},
                timeout=15,
            )
            if r.status_code == 200:
                return self._result(api, True, "200 OK", f"{url}")
            if r.status_code in (401, 403):
                return self._result(api, False, f"{r.status_code}", "credentials rejected")
            # Some DHL endpoints return 400 with "Invalid Credentials" body
            if r.status_code == 400 and "Invalid Credentials" in r.text:
                return self._result(api, False, "400", "Invalid Credentials (per body)")
            return self._result(api, False, f"{r.status_code}", (r.text or "")[:120].replace("\n", " "))
        except Exception as e:
            return self._result(api, False, "network", str(e)[:120])

    def _probe_ecommerce_solutions(self, key: str, secret: str) -> dict:
        """
        DHL eCommerce Solutions — OAuth 2.0 on /auth/v4/accesstoken.
        Sandbox host: api-sandbox.dhlecs.com, prod host: api.dhlecs.com.
        """
        api = "DHL eCommerce Solutions (sandbox)"
        url = "https://api-sandbox.dhlecs.com/auth/v4/accesstoken"
        try:
            r = requests.get(
                url,
                auth=(key, secret),
                params={"grant_type": "client_credentials"},
                timeout=15,
            )
            if r.status_code == 200 and "access_token" in (r.text or ""):
                return self._result(api, True, "200 OK", "access_token received")
            if r.status_code in (401, 403):
                return self._result(api, False, f"{r.status_code}", "credentials rejected")
            return self._result(api, False, f"{r.status_code}", (r.text or "")[:120].replace("\n", " "))
        except Exception as e:
            return self._result(api, False, "network", str(e)[:120])

    def _probe_parcel_de_shipping(self, key: str, secret: str, account: str) -> dict:
        """
        DHL Parcel DE Shipping (Post & Parcel Germany) — API key goes in the
        DPDHL-API-Key header, plus Basic Auth for user/password (billing
        number setup). We check the openapi endpoint which requires just the
        API key for a 200 / 401 split.
        """
        api = "DHL Parcel DE Shipping (Post & Parcel DE) [sandbox]"
        url = "https://api-sandbox.dhl.com/parcel/de/shipping/v2/orders"
        try:
            r = requests.get(
                url,
                headers={
                    "dpdhl-api-key": key,
                    "Accept": "application/json",
                },
                auth=(account, secret) if account else None,
                params={"profile": "STANDARD_GRUPPENPROFIL"},
                timeout=15,
            )
            # 200 = works, 400 with "UNAUTHORIZED_USER" hint = wrong username,
            # 401/403 = wrong API key entirely.
            if r.status_code == 200:
                return self._result(api, True, "200 OK", "API key accepted")
            if r.status_code == 400:
                body = (r.text or "")
                if "api key" in body.lower() or "dpdhl-api-key" in body.lower():
                    return self._result(api, False, "400", "DPDHL-API-Key missing / invalid")
                return self._result(api, True, "400", "API key accepted; user/password wrong or payload invalid")
            if r.status_code in (401, 403):
                return self._result(api, False, f"{r.status_code}", "credentials rejected")
            return self._result(api, False, f"{r.status_code}", (r.text or "")[:120].replace("\n", " "))
        except Exception as e:
            return self._result(api, False, "network", str(e)[:120])

    def _probe_parcel_de_account(self, key: str, secret: str) -> dict:
        """DHL Parcel DE Customer Account API — Basic Auth."""
        api = "DHL Parcel DE Customer Account"
        url = "https://api-sandbox.dhl.com/parcel/de/account/auth/v1/accesstoken"
        try:
            r = requests.post(
                url,
                auth=(key, secret),
                timeout=15,
            )
            if r.status_code == 200 and "accessToken" in (r.text or ""):
                return self._result(api, True, "200 OK", "accessToken received")
            if r.status_code in (401, 403):
                return self._result(api, False, f"{r.status_code}", "credentials rejected")
            return self._result(api, False, f"{r.status_code}", (r.text or "")[:120].replace("\n", " "))
        except Exception as e:
            return self._result(api, False, "network", str(e)[:120])

    def _probe_deutsche_post_international(self, key: str, secret: str) -> dict:
        """DHL Deutsche Post International (mail products) — Basic Auth."""
        api = "DHL Deutsche Post International (mail)"
        url = "https://api-eu.dhl.com/mail/intl/tracking/v1/packages"
        try:
            r = requests.get(
                url,
                headers={
                    "DHL-API-Key": key,
                },
                auth=(key, secret),
                timeout=15,
            )
            if r.status_code in (200, 400):
                # 400 usually means "shipment ID missing" — credentials OK
                body = (r.text or "").lower()
                if "unauthorized" in body or "invalid credentials" in body:
                    return self._result(api, False, f"{r.status_code}", "rejected (per body)")
                return self._result(api, True, f"{r.status_code}", "credentials accepted")
            if r.status_code in (401, 403):
                return self._result(api, False, f"{r.status_code}", "credentials rejected")
            return self._result(api, False, f"{r.status_code}", (r.text or "")[:120].replace("\n", " "))
        except Exception as e:
            return self._result(api, False, "network", str(e)[:120])
