"""
Server-side Meta Conversions API (CAPI) and TikTok Events API for Purchase / CompletePayment.

Uses the same event_id as the browser pixel (when configured) for deduplication.
Set META_CAPI_ACCESS_TOKEN and TIKTOK_EVENTS_API_ACCESS_TOKEN in the environment.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone as dt_timezone
from typing import Any, Mapping

import requests
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


def _sha256_email(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def send_purchase_capi_events(
    *,
    order_id: int,
    purchase_pixel: Mapping[str, Any],
    client_ip: str,
    user_agent: str,
    fbp: str,
    fbc: str,
    email: str,
) -> None:
    """
    Send Meta Purchase and TikTok CompletePayment to marketing APIs.
    Safe to call from transaction.on_commit (no DB access required).
    """
    meta_block = purchase_pixel.get("meta") or {}
    tiktok_block = purchase_pixel.get("tiktok") or {}
    event_id = purchase_pixel.get("meta_event_id") or f"purchase-{order_id}"
    tiktok_event_id = purchase_pixel.get("tiktok_event_id") or event_id

    pixel_id = (getattr(settings, "META_PIXEL_ID", "") or "").strip()
    capi_token = (getattr(settings, "META_CAPI_ACCESS_TOKEN", "") or "").strip()
    graph_ver = (
        (getattr(settings, "META_CAPI_GRAPH_VERSION", None) or "v21.0")
        .strip()
        .lstrip("/")
    )

    if capi_token and pixel_id and meta_block:
        user_data: dict[str, Any] = {}
        if email:
            user_data["em"] = [_sha256_email(email)]
        if client_ip:
            user_data["client_ip_address"] = client_ip[:45]
        if user_agent:
            user_data["client_user_agent"] = user_agent[:2048]
        if fbp:
            user_data["fbp"] = fbp[:255]
        if fbc:
            user_data["fbc"] = fbc[:255]

        custom_data: dict[str, Any] = {
            "currency": meta_block.get("currency") or "EUR",
            "value": float(meta_block.get("value") or 0),
            "content_ids": list(meta_block.get("content_ids") or []),
            "contents": list(meta_block.get("contents") or []),
            "content_type": meta_block.get("content_type") or "product",
        }
        if meta_block.get("order_id") is not None:
            custom_data["order_id"] = str(meta_block["order_id"])

        event: dict[str, Any] = {
            "event_name": "Purchase",
            "event_time": int(time.time()),
            "action_source": "website",
            "event_id": str(event_id),
            "user_data": user_data,
            "custom_data": custom_data,
        }
        pub = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
        if pub:
            event["event_source_url"] = f"{pub}/order-success/"

        body: dict[str, Any] = {
            "data": [event],
            "access_token": capi_token,
        }
        test_code = (getattr(settings, "META_CAPI_TEST_EVENT_CODE", "") or "").strip()
        if test_code:
            body["test_event_code"] = test_code

        url = f"https://graph.facebook.com/{graph_ver}/{pixel_id}/events"
        try:
            r = requests.post(url, json=body, timeout=8)
            if r.status_code != 200:
                logger.warning(
                    "Meta CAPI HTTP %s for order_id=%s: %s",
                    r.status_code,
                    order_id,
                    (r.text or "")[:800],
                )
            else:
                data = r.json()
                if isinstance(data, dict) and data.get("error"):
                    logger.warning(
                        "Meta CAPI error for order_id=%s: %s",
                        order_id,
                        data.get("error"),
                    )
        except Exception:
            logger.exception("Meta CAPI request failed order_id=%s", order_id)

    tt_token = (getattr(settings, "TIKTOK_EVENTS_API_ACCESS_TOKEN", "") or "").strip()
    tt_pixel = (getattr(settings, "TIKTOK_PIXEL_ID", "") or "").strip()
    if tt_token and tt_pixel and tiktok_block:
        ts = datetime.now(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
        pub = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
        page_url = f"{pub}/order-success/" if pub else ""

        context: dict[str, Any] = {}
        if client_ip:
            context["ip"] = client_ip
        if user_agent:
            context["user_agent"] = user_agent[:4096]
        if page_url:
            context["page"] = {"url": page_url}

        properties: dict[str, Any] = {
            "currency": tiktok_block.get("currency") or "EUR",
            "value": float(tiktok_block.get("value") or 0),
            "content_type": tiktok_block.get("content_type") or "product",
            "contents": list(tiktok_block.get("contents") or []),
        }
        if tiktok_block.get("order_id") is not None:
            properties["order_id"] = str(tiktok_block["order_id"])

        body = {
            "pixel_code": tt_pixel,
            "event": "CompletePayment",
            "event_id": str(tiktok_event_id),
            "timestamp": ts,
            "context": context,
            "properties": properties,
        }

        try:
            r = requests.post(
                "https://business-api.tiktok.com/open_api/v1.3/pixel/track/",
                headers={
                    "Access-Token": tt_token,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=8,
            )
            try:
                j = r.json()
            except Exception:
                j = {}
            tt_code = j.get("code") if isinstance(j, dict) else None
            tt_bad = r.status_code != 200 or (
                isinstance(j, dict) and "code" in j and j.get("code") != 0
            )
            if tt_bad:
                logger.warning(
                    "TikTok Events API HTTP %s code=%s for order_id=%s: %s",
                    r.status_code,
                    tt_code,
                    order_id,
                    (r.text or "")[:800],
                )
        except Exception:
            logger.exception("TikTok Events API request failed order_id=%s", order_id)


def schedule_purchase_capi(
    *,
    order_id: int,
    purchase_pixel: Mapping[str, Any],
    client_ip: str,
    user_agent: str,
    fbp: str,
    fbc: str,
    email: str,
) -> None:
    """Run CAPI sends after the DB transaction commits (same payload as browser pixel)."""

    def _run() -> None:
        try:
            send_purchase_capi_events(
                order_id=order_id,
                purchase_pixel=purchase_pixel,
                client_ip=client_ip,
                user_agent=user_agent,
                fbp=fbp,
                fbc=fbc,
                email=email,
            )
        except Exception:
            logger.exception("purchase CAPI on_commit failed order_id=%s", order_id)

    transaction.on_commit(_run)
