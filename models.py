"""Domain constants and row helpers (SQLite rows -> dicts)."""
import json
import sqlite3
from datetime import datetime, timezone

STATUS_QUEUED, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED = "Queued", "Running", "Completed", "Failed"
STATUSES = (STATUS_QUEUED, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED)

# category -> template key
CATEGORIES = {"Billing": "billing", "GST": "gst", "Inventory": "inventory", "CRM": "crm", "Custom": "custom"}
TEMPLATE_INFO = {
    "billing": ("Billing Software", "Products, customers, invoices with totals and invoice history."),
    "gst": ("GST Invoice App", "Invoices with configurable tax rates, taxable amounts and tax summaries."),
    "inventory": ("Inventory Manager", "Products, stock quantities, movement history and low-stock flags."),
    "crm": ("Customer Management", "Customer records, contact details, notes and history."),
    "custom": ("Custom Project", "Basic runnable starter built from supported options."),
}
CUSTOM_SUPPORTED_FEATURES = ("items", "notes", "csv_export")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def row_to_project(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["features"] = json.loads(d.get("features") or "[]")
    d["checks"] = json.loads(d.get("checks") or "[]")
    d["is_demo"] = bool(d["is_demo"])
    d.pop("output_dir", None)  # never expose server paths
    return d
