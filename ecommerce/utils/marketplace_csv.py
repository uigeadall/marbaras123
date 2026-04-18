"""
CSV / TSV parsers for marketplace order exports.

Supports both Amazon Seller Central order reports (tab-separated) and
Etsy Shop Manager order CSVs (comma-separated). Each parser returns a
list of normalized dictionaries that can be persisted directly onto the
:class:`ecommerce.models.MarketplaceOrder` model.

Design goals
------------
* **Lenient** — Amazon and Etsy both change column names from time to
  time, so the parsers try a pool of candidate column names for each
  logical field and fall back gracefully when something is missing.
* **Auto-detect** — callers can use :func:`parse_csv_auto` and let the
  module figure out which marketplace produced the file.
* **Zero Django imports at module top level** — keeps the unit tests
  fast and makes the parsers safe to call from management commands.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _pick(row: Dict[str, Any], *candidates: str, default: str = "") -> str:
    """Return the first non-empty value among a list of candidate column
    names (case-insensitive, trimmed)."""
    lowered = {(k or "").strip().lower(): v for k, v in row.items()}
    for name in candidates:
        val = lowered.get(name.strip().lower())
        if val is None:
            continue
        cleaned = _clean(val)
        if cleaned:
            return cleaned
    return default


def _to_decimal(value: str, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, "", "-"):
        return default
    v = str(value).strip()
    v = re.sub(r"[^0-9.,-]", "", v)
    if "," in v and "." in v:
        v = v.replace(",", "")
    elif "," in v and v.count(",") == 1 and re.search(r",\d{1,2}$", v):
        v = v.replace(",", ".")
    else:
        v = v.replace(",", "")
    try:
        return Decimal(v)
    except (InvalidOperation, ValueError):
        return default


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value).strip().split(".")[0] or default)
    except (ValueError, AttributeError):
        return default


_COUNTRY_NAME_TO_ISO2 = {
    "united kingdom": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "germany": "DE",
    "deutschland": "DE",
    "france": "FR",
    "italy": "IT",
    "italia": "IT",
    "spain": "ES",
    "españa": "ES",
    "netherlands": "NL",
    "belgium": "BE",
    "austria": "AT",
    "denmark": "DK",
    "sweden": "SE",
    "norway": "NO",
    "finland": "FI",
    "poland": "PL",
    "portugal": "PT",
    "greece": "GR",
    "ireland": "IE",
    "czech republic": "CZ",
    "czechia": "CZ",
    "slovakia": "SK",
    "slovenia": "SI",
    "romania": "RO",
    "bulgaria": "BG",
    "hungary": "HU",
    "croatia": "HR",
    "switzerland": "CH",
    "canada": "CA",
    "australia": "AU",
    "new zealand": "NZ",
    "japan": "JP",
    "china": "CN",
}


def _normalize_country(raw: str) -> str:
    v = (raw or "").strip()
    if not v:
        return ""
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return _COUNTRY_NAME_TO_ISO2.get(v.lower(), v[:2].upper())


# ---------------------------------------------------------------------------
# Amazon parser
# ---------------------------------------------------------------------------
_AMAZON_FINGERPRINTS = {
    "amazon-order-id",
    "order-id",
    "merchant-order-id",
    "purchase-date",
    "ship-address-1",
    "ship-country",
}


def parse_amazon_csv(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse Amazon Seller Central order report rows into normalized dicts.

    Amazon exports are typically tab-separated with one row per order
    item. Multiple rows can share the same ``amazon-order-id`` when the
    buyer ordered several items — they are aggregated into a single
    shipment here.
    """
    by_order: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        order_id = _pick(
            row, "amazon-order-id", "order-id", "Order ID", "merchant-order-id"
        )
        if not order_id:
            continue

        buyer_name = _pick(
            row,
            "recipient-name",
            "Recipient Name",
            "buyer-name",
            "ship-name",
        )
        address1 = _pick(row, "ship-address-1", "Ship Address 1")
        address2 = _pick(row, "ship-address-2", "Ship Address 2")
        city = _pick(row, "ship-city", "City")
        state = _pick(row, "ship-state", "State")
        postal = _pick(row, "ship-postal-code", "Zip")
        country = _normalize_country(_pick(row, "ship-country", "Country"))
        phone = _pick(row, "ship-phone-number", "buyer-phone-number", "Phone")
        email = _pick(row, "buyer-email", "Email")

        item_name = _pick(row, "product-name", "Item Name", "Title")
        sku = _pick(row, "sku", "SKU")
        qty = max(_to_int(_pick(row, "quantity-purchased", "quantity", "Qty"), 1), 1)
        item_price = _to_decimal(_pick(row, "item-price", "Price", "item_price"))
        currency = _pick(row, "currency", "Currency", default="EUR").upper()[:3]

        entry = by_order.setdefault(
            order_id,
            {
                "marketplace": "amazon",
                "external_order_id": order_id,
                "buyer_name": buyer_name,
                "buyer_email": email,
                "buyer_phone": phone,
                "address_line1": address1,
                "address_line2": address2,
                "city": city,
                "state": state,
                "postal_code": postal,
                "country": country,
                "items": [],
                "total_amount": Decimal("0"),
                "currency": currency,
                "raw_csv_data": {"rows": []},
            },
        )
        entry["items"].append({"name": item_name, "sku": sku, "quantity": qty, "price": str(item_price)})
        entry["total_amount"] += item_price * Decimal(qty)
        entry["raw_csv_data"]["rows"].append(row)
        if not entry["buyer_name"] and buyer_name:
            entry["buyer_name"] = buyer_name

    results = []
    for entry in by_order.values():
        items = entry.pop("items")
        entry["items_summary"] = " / ".join(
            f"{it['quantity']}x {it['name'] or 'Item'}".strip() for it in items
        )[:1000]
        entry["item_count"] = sum(it["quantity"] for it in items)
        entry["total_amount"] = entry["total_amount"].quantize(Decimal("0.01"))
        entry["total_weight_g"] = max(100, entry["item_count"] * 50)
        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# Etsy parser
# ---------------------------------------------------------------------------
_ETSY_FINGERPRINTS = {
    "Order ID",
    "Transaction ID",
    "Ship Zipcode",
    "Ship City",
    "Item Name",
    "Sale Date",
}


def parse_etsy_csv(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse Etsy Shop Manager order CSV rows into normalized dicts.

    Etsy CSVs use one row per transaction. Orders with multiple items
    produce several rows sharing the same ``Order ID`` — they are
    aggregated into a single shipment.
    """
    by_order: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        order_id = _pick(row, "Order ID", "order_id")
        if not order_id:
            continue

        buyer_name = _pick(row, "Full Name", "Ship Name", "Buyer")
        email = _pick(row, "Buyer Email", "Email")
        phone = _pick(row, "Ship Phone", "Phone")
        address1 = _pick(row, "Street 1", "Ship Address 1")
        address2 = _pick(row, "Street 2", "Ship Address 2")
        city = _pick(row, "Ship City", "City")
        state = _pick(row, "Ship State", "State")
        postal = _pick(row, "Ship Zipcode", "Zip", "Ship Zip")
        country = _normalize_country(_pick(row, "Ship Country", "Country"))

        item_name = _pick(row, "Item Name", "Title")
        sku = _pick(row, "SKU")
        qty = max(_to_int(_pick(row, "Quantity", "Qty"), 1), 1)
        price = _to_decimal(_pick(row, "Price", "Item Total"))
        currency = _pick(row, "Currency", default="EUR").upper()[:3]

        entry = by_order.setdefault(
            order_id,
            {
                "marketplace": "etsy",
                "external_order_id": order_id,
                "buyer_name": buyer_name,
                "buyer_email": email,
                "buyer_phone": phone,
                "address_line1": address1,
                "address_line2": address2,
                "city": city,
                "state": state,
                "postal_code": postal,
                "country": country,
                "items": [],
                "total_amount": Decimal("0"),
                "currency": currency,
                "raw_csv_data": {"rows": []},
            },
        )
        entry["items"].append({"name": item_name, "sku": sku, "quantity": qty, "price": str(price)})
        entry["total_amount"] += price * Decimal(qty)
        entry["raw_csv_data"]["rows"].append(row)
        if not entry["buyer_name"] and buyer_name:
            entry["buyer_name"] = buyer_name

    results = []
    for entry in by_order.values():
        items = entry.pop("items")
        entry["items_summary"] = " / ".join(
            f"{it['quantity']}x {it['name'] or 'Item'}".strip() for it in items
        )[:1000]
        entry["item_count"] = sum(it["quantity"] for it in items)
        entry["total_amount"] = entry["total_amount"].quantize(Decimal("0.01"))
        entry["total_weight_g"] = max(100, entry["item_count"] * 50)
        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# Auto-detect
# ---------------------------------------------------------------------------
def detect_marketplace(headers: Iterable[str]) -> str:
    """Classify a CSV as ``"amazon"``, ``"etsy"``, or ``"unknown"`` based
    on header fingerprints."""
    hset = {(h or "").strip() for h in headers}
    hlower = {h.lower() for h in hset}
    if any(f in hlower for f in (f.lower() for f in _AMAZON_FINGERPRINTS)):
        return "amazon"
    if any(f in hset for f in _ETSY_FINGERPRINTS):
        return "etsy"
    return "unknown"


def parse_csv_auto(
    file_content: bytes, forced_marketplace: Optional[str] = None
) -> Tuple[str, List[Dict[str, Any]]]:
    """Parse ``file_content`` (raw bytes from uploaded file) into normalized
    order dicts. Returns ``(marketplace, list_of_orders)``.

    The function tries UTF-8 BOM then UTF-8 then latin-1. Delimiter is
    sniffed (CSV vs TSV) from the first 4 KB.
    """
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = file_content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("Unable to decode CSV file. Please save it as UTF-8.")

    sniffer = csv.Sniffer()
    sample = text[:4096]
    try:
        dialect = sniffer.sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = "\t" if "\t" in sample else ","

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = [{k: v for k, v in row.items() if k is not None} for row in reader]
    if not rows:
        raise ValueError("CSV file contains no data rows.")

    marketplace = (forced_marketplace or "").strip().lower()
    if marketplace not in ("amazon", "etsy", "ebay", "other"):
        marketplace = detect_marketplace(rows[0].keys())
    if marketplace == "unknown":
        raise ValueError(
            "Could not auto-detect marketplace from CSV headers. "
            "Please select the marketplace manually before uploading."
        )

    if marketplace == "amazon":
        orders = parse_amazon_csv(rows)
    elif marketplace == "etsy":
        orders = parse_etsy_csv(rows)
    else:
        raise ValueError(
            f"Marketplace '{marketplace}' is not yet supported. "
            "Only Amazon and Etsy CSV exports are currently handled."
        )

    logger.info(
        "Parsed %s CSV with %d row(s) → %d distinct order(s)",
        marketplace,
        len(rows),
        len(orders),
    )
    return marketplace, orders
