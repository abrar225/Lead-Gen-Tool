"""
Shared utilities for the Google Maps Lead Harvester pipeline.

Centralizes: CSV I/O, progress tracking, data extraction helpers,
phone validation, logging, and configuration constants.
"""

import csv
import json
import os
import re
import logging
import tempfile
import shutil
from datetime import datetime
from pathlib import Path


# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

PROJECT_DIR = Path(__file__).parent
TASKS_FILE = PROJECT_DIR / "tasks.csv"
LEADS_FILE = PROJECT_DIR / "leads.csv"
PROGRESS_FILE = PROJECT_DIR / ".progress.json"

LEADS_COLUMNS = [
    "Business Name", "Phone", "Rating", "Reviews",
    "Google Maps URL", "Search Query",
]
TASKS_COLUMNS = ["url"]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

DEFAULT_VIEWPORT = {"width": 1280, "height": 720}


# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────

def setup_logging(name: str, log_file: str = None) -> logging.Logger:
    """Configure structured logging with timestamps and levels."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # Avoid duplicate handlers

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    # Optional file handler
    if log_file:
        log_path = PROJECT_DIR / log_file
        fh = logging.FileHandler(str(log_path), encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger


# ──────────────────────────────────────────────
# Atomic / Safe CSV Operations
# ──────────────────────────────────────────────

def atomic_csv_write(filepath: Path, rows: list, columns: list):
    """Write CSV atomically: temp-file → rename (prevents corruption)."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".csv", dir=str(filepath.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(rows)
        # Atomic on same filesystem
        if os.path.exists(str(filepath)):
            os.replace(tmp_path, str(filepath))
        else:
            shutil.move(tmp_path, str(filepath))
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def append_csv_row(filepath: Path, row: list, columns: list):
    """Append a single row to CSV, creating with headers if needed."""
    file_exists = filepath.exists() and filepath.stat().st_size > 0
    with open(str(filepath), "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(columns)
        writer.writerow(row)


def read_csv_safe(filepath: Path) -> list:
    """Read CSV safely, return list of dicts — empty list on failure."""
    if not filepath.exists() or filepath.stat().st_size == 0:
        return []
    try:
        rows = []
        with open(str(filepath), "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        return rows
    except (csv.Error, UnicodeDecodeError, KeyError, OSError) as exc:
        logging.getLogger(__name__).warning("Failed to read %s: %s", filepath, exc)
        return []


def load_existing_leads(filepath: Path) -> set:
    """Load already-extracted lead URLs for resume capability."""
    seen = set()
    rows = read_csv_safe(filepath)
    for row in rows:
        url = row.get("Google Maps URL", "")
        if url:
            seen.add(url)
    return seen


def load_seen_phones(filepath: Path) -> set:
    """Load already-seen phone numbers for cross-run deduplication."""
    phones = set()
    rows = read_csv_safe(filepath)
    for row in rows:
        phone = row.get("Phone", "")
        if phone and phone != "N/A":
            phones.add(phone)
    return phones


# ──────────────────────────────────────────────
# Progress Tracking (subprocess ↔ dashboard)
# ──────────────────────────────────────────────

def write_progress(
    step: str,
    current: int,
    total: int,
    status: str = "running",
    message: str = "",
):
    """Write progress JSON for the Streamlit dashboard to read."""
    data = {
        "step": step,
        "current": current,
        "total": total,
        "status": status,
        "message": message,
        "timestamp": datetime.now().isoformat(),
        "pid": os.getpid(),
    }
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".json", dir=str(PROJECT_DIR)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        if os.path.exists(str(PROGRESS_FILE)):
            os.replace(tmp_path, str(PROGRESS_FILE))
        else:
            shutil.move(tmp_path, str(PROGRESS_FILE))
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def read_progress() -> dict:
    """Read progress JSON (returns empty dict on any error)."""
    try:
        if PROGRESS_FILE.exists():
            with open(str(PROGRESS_FILE), "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, PermissionError, OSError):
        pass
    return {}


def clear_progress():
    """Remove the progress file."""
    try:
        if PROGRESS_FILE.exists():
            PROGRESS_FILE.unlink()
    except OSError:
        pass


# ──────────────────────────────────────────────
# Phone Number Validation
# ──────────────────────────────────────────────

def is_valid_phone(text: str) -> bool:
    """
    Validate whether a string looks like a real phone number.
    
    Rejects: zip codes, suite numbers, short digit strings,
    addresses, and other non-phone numeric content.
    """
    if not text or not isinstance(text, str):
        return False

    text = text.strip()
    if len(text) < 7 or len(text) > 25:
        return False

    # Strip common formatting
    cleaned = re.sub(r'[\s\-\(\)\.\+\u2013\u2014]', '', text)

    # Count digits
    digit_count = sum(1 for c in cleaned if c.isdigit())

    # Must have at least 7 digits (minimum valid phone)
    if digit_count < 7:
        return False

    # At least 70% of characters must be digits
    if len(cleaned) > 0 and digit_count / len(cleaned) < 0.7:
        return False

    # Reject obvious non-phone patterns
    # e.g. "Suite 200", "Floor 3", year numbers like "2024"
    lower = text.lower()
    reject_patterns = [
        r'\b(suite|floor|unit|apt|room|bldg|ste)\b',
        r'^\d{4}$',          # 4-digit numbers (years, floors)
        r'^\d{5}(-\d{4})?$', # ZIP codes
    ]
    for pattern in reject_patterns:
        if re.search(pattern, lower):
            return False

    return True


# ──────────────────────────────────────────────
# Data Extraction Helpers (Playwright)
# ──────────────────────────────────────────────

def extract_rating_reviews(detail_panel) -> tuple:
    """
    Extract rating and review count from a Google Maps detail panel.
    Returns (rating: str, reviews: str) — defaults to ("N/A", "N/A").
    """
    rating = "N/A"
    reviews = "N/A"
    log = logging.getLogger(__name__)

    try:
        # ── Rating ──
        rating_selectors = [
            'div.F7nice span[aria-hidden="true"]',
            'span.ceNzKf[role="img"]',
            'div.fontDisplayLarge',
        ]
        for selector in rating_selectors:
            try:
                el = detail_panel.locator(selector).first
                if el.count() > 0:
                    text = el.inner_text().strip()
                    if re.match(r'^\d+\.?\d*$', text):
                        rating = text
                        break
            except (TimeoutError, Exception):
                continue

        # ── Reviews ──
        review_selectors = [
            'div.F7nice span[aria-label*="review"]',
            'button[aria-label*="review"]',
            'span[aria-label*="review"]',
        ]
        for selector in review_selectors:
            try:
                el = detail_panel.locator(selector).first
                if el.count() > 0:
                    aria = el.get_attribute("aria-label") or ""
                    text = el.inner_text().strip()
                    combined = f"{aria} {text}"
                    match = re.search(
                        r'([\d,]+)\s*review', combined, re.IGNORECASE
                    )
                    if not match:
                        match = re.search(r'([\d,]+)', combined)
                    if match:
                        reviews = match.group(1)
                        break
            except (TimeoutError, Exception):
                continue

    except Exception as exc:
        log.warning("Rating/reviews extraction error: %s", exc)

    return rating, reviews


def extract_phone(detail_panel) -> str:
    """
    Extract phone number from Google Maps detail panel, with validation.
    Returns the phone string or "N/A".
    """
    log = logging.getLogger(__name__)

    try:
        # ── Primary: dedicated phone button ──
        phone_btn = detail_panel.locator('button[data-item-id^="phone:"]')
        if phone_btn.count() > 0:
            aria_label = phone_btn.first.get_attribute("aria-label")
            if aria_label:
                phone = aria_label.replace("Phone: ", "").strip()
                if is_valid_phone(phone):
                    return phone

        # ── Fallback: scan buttons for phone patterns ──
        try:
            candidates = detail_panel.locator("button").all()
        except Exception:
            candidates = []

        for btn in candidates:
            try:
                text = btn.inner_text().strip()
                if is_valid_phone(text):
                    return text
            except Exception:
                continue

    except Exception as exc:
        log.warning("Phone extraction error: %s", exc)

    return "N/A"


def extract_business_name(detail_panel) -> str:
    """Extract the business name (H1) from a detail panel."""
    try:
        h1 = detail_panel.locator("h1").first
        if h1.count() > 0:
            return h1.inner_text().strip()
    except Exception:
        pass
    return "Unknown"


def is_business_closed(detail_panel) -> bool:
    """Check if business is temporarily or permanently closed."""
    try:
        text = detail_panel.inner_text()
        return "Temporarily closed" in text or "Permanently closed" in text
    except Exception:
        return False


def has_website(detail_panel) -> bool:
    """Check if the business has a website listed."""
    try:
        if detail_panel.locator('[data-item-id="authority"]').count() > 0:
            return True
        if detail_panel.locator('[aria-label*="Website"]').count() > 0:
            return True
    except Exception:
        pass
    return False


# ──────────────────────────────────────────────
# Input Sanitization
# ──────────────────────────────────────────────

def sanitize_search_query(query: str) -> str:
    """Sanitize and validate search query for safe use in URLs and CLI."""
    if not query or not query.strip():
        raise ValueError("Search query cannot be empty.")

    query = query.strip()

    # Length limit
    if len(query) > 200:
        query = query[:200]

    # Remove characters dangerous for shell / URL injection
    query = re.sub(r'[<>|&;`$\\]', '', query)

    if not query.strip():
        raise ValueError("Search query is empty after sanitization.")

    return query


# ──────────────────────────────────────────────
# Browser Helpers
# ──────────────────────────────────────────────

def human_delay(low: float = 1.0, high: float = 3.0):
    """Random human-like delay."""
    import time
    import random
    time.sleep(random.uniform(low, high))


def human_click(page, element):
    """Simulate a human interaction: hover → pause → click → pause."""
    import time
    import random

    try:
        element.hover()
        time.sleep(random.uniform(0.3, 1.0))
        element.click()
        time.sleep(random.uniform(0.2, 0.5))
    except Exception:
        # Fallback if hover fails (e.g., element covered by overlay)
        try:
            element.click(force=True)
        except Exception:
            pass


def create_stealth_context(browser):
    """Create a browser context with stealth settings."""
    return browser.new_context(
        user_agent=DEFAULT_USER_AGENT,
        viewport=DEFAULT_VIEWPORT,
        locale="en-US",
        timezone_id="America/New_York",
    )


# ──────────────────────────────────────────────
# Process Management
# ──────────────────────────────────────────────

def is_pid_running(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if pid is None or pid <= 0:
        return False
    try:
        # Windows-compatible check
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, 0, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except (AttributeError, OSError):
        # Fallback: POSIX signal 0
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def terminate_process_tree(pid: int):
    """Terminate a process and its children (best-effort)."""
    if pid is None or pid <= 0:
        return
    try:
        import subprocess
        # Windows: use taskkill to kill the process tree
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # Fallback: simple kill
        try:
            os.kill(pid, 9)
        except (OSError, ProcessLookupError):
            pass
