"""
Step 2: Lead Extractor

Reads place URLs from tasks.csv, visits each page,
extracts business details, and appends valid leads to leads.csv.

Features:
  - Resume capability: skips URLs already processed.
  - Cross-run phone deduplication.
  - Configurable headless mode.
  - Graceful shutdown with data preservation.

Usage:
    python step2_extractor.py "Interior designers in Ahmedabad"
    python step2_extractor.py "Plumbers in NY" --headless
"""

import sys
import signal
import argparse
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from utils import (
    setup_logging,
    sanitize_search_query,
    read_csv_safe,
    append_csv_row,
    load_existing_leads,
    load_seen_phones,
    write_progress,
    clear_progress,
    extract_rating_reviews,
    extract_phone,
    extract_business_name,
    is_business_closed,
    has_website,
    human_delay,
    create_stealth_context,
    TASKS_FILE,
    LEADS_FILE,
    LEADS_COLUMNS,
)

# ── Logger ──
log = setup_logging("step2_extractor", log_file="step2.log")

# ── Graceful shutdown ──
_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    log.warning("Shutdown signal received (signal %s). Saving progress...", signum)


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Step 2: Extract lead details from collected links."
    )
    parser.add_argument(
        "search_query", type=str, nargs="?", default="",
        help="Search query (saved alongside leads for context)."
    )
    parser.add_argument(
        "--headless", action="store_true", default=False,
        help="Run browser in headless mode."
    )
    return parser.parse_args()


def main():
    global _shutdown_requested

    args = parse_args()
    headless = args.headless
    search_query = args.search_query.strip()

    # ── Load tasks ──
    task_rows = read_csv_safe(TASKS_FILE)
    if not task_rows:
        log.error("No tasks found in %s. Run Step 1 first.", TASKS_FILE)
        write_progress("step2", 0, 0, status="error",
                       message="No tasks found. Run Step 1 first.")
        sys.exit(1)

    all_urls = []
    for row in task_rows:
        url = row.get("url", "").strip().strip('"')
        if url:
            all_urls.append(url)

    if not all_urls:
        log.error("Tasks file has no valid URLs.")
        write_progress("step2", 0, 0, status="error",
                       message="Tasks file has no valid URLs.")
        sys.exit(1)

    # ── Resume: skip already-processed URLs ──
    already_done = load_existing_leads(LEADS_FILE)
    pending_urls = [u for u in all_urls if u not in already_done]

    total = len(all_urls)
    already_count = total - len(pending_urls)

    if already_count > 0:
        log.info("Resuming: %d/%d already processed, %d remaining.",
                 already_count, total, len(pending_urls))

    if not pending_urls:
        log.info("All %d tasks already processed. Nothing to do.", total)
        write_progress("step2", total, total, status="done",
                       message="All tasks already processed.")
        return

    # ── Load seen phones for cross-run deduplication ──
    seen_phones = load_seen_phones(LEADS_FILE)
    log.info("Loaded %d previously-seen phone numbers.", len(seen_phones))

    log.info("Starting extraction: %d URLs to process.", len(pending_urls))
    write_progress("step2", already_count, total, status="running",
                   message="Starting extraction...")

    # ── Stats ──
    stats = {
        "processed": already_count,
        "leads_found": 0,
        "skipped_closed": 0,
        "skipped_website": 0,
        "skipped_duplicate": 0,
        "errors": 0,
    }

    browser = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = create_stealth_context(browser)
            page = context.new_page()

            for i, url in enumerate(pending_urls):
                if _shutdown_requested:
                    log.warning("Shutdown — stopping after %d/%d URLs.",
                                i, len(pending_urls))
                    break

                stats["processed"] += 1
                current = stats["processed"]

                log.info("[%d/%d] Processing: %s", current, total, url[:80])
                write_progress(
                    "step2", current, total,
                    status="running",
                    message=f"Processing {current}/{total}...",
                )

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)

                    # ── Wait for page to fully load ──
                    try:
                        # Fix strict mode violation by taking .first
                        page.locator(
                            'button[data-value="Directions"]'
                        ).or_(
                            page.locator('button[aria-label="Directions"]')
                        ).first.wait_for(state="visible", timeout=15000)
                    except PwTimeout:
                        log.warning("  Page load timeout — skipping.")
                        stats["errors"] += 1
                        continue

                    detail_panel = page.locator('div[role="main"]')

                    # ── Filter: closed businesses ──
                    if is_business_closed(detail_panel):
                        log.info("  SKIP: Business is closed.")
                        stats["skipped_closed"] += 1
                        continue

                    # ── Filter: has website (DISABLED to maximize leads) ──
                    # Previously this skipped businesses with websites. 
                    # We now collect them too.
                    website_found = has_website(detail_panel)
                    if website_found:
                        log.info("  Info: Business has a website (collecting anyway).")

                    # ── Extract data ──
                    name = extract_business_name(detail_panel)
                    rating, reviews = extract_rating_reviews(detail_panel)
                    phone = extract_phone(detail_panel)

                    # ── Deduplication ──
                    if phone != "N/A" and phone in seen_phones:
                        log.info("  SKIP: Duplicate phone %s.", phone)
                        stats["skipped_duplicate"] += 1
                        continue

                    # Track phone
                    if phone != "N/A":
                        seen_phones.add(phone)

                    # ── Save lead ──
                    row = [name, phone, rating, reviews, url, search_query]
                    append_csv_row(LEADS_FILE, row, LEADS_COLUMNS)
                    stats["leads_found"] += 1

                    log.info("  FOUND: %s | %s | ★%s (%s reviews)",
                             name, phone, rating, reviews)

                    # Human delay between requests
                    human_delay(2.0, 5.0)

                except PwTimeout as exc:
                    log.warning("  Timeout error: %s", exc)
                    stats["errors"] += 1
                except Exception as exc:
                    log.error("  Error processing URL: %s", exc, exc_info=True)
                    stats["errors"] += 1

    except KeyboardInterrupt:
        log.warning("Interrupted by user.")
    except Exception as exc:
        log.error("Critical error in Step 2: %s", exc, exc_info=True)
        write_progress("step2", stats["processed"], total,
                       status="error", message=str(exc))
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass

    # ── Final report ──
    log.info("=" * 50)
    log.info("STEP 2 COMPLETE")
    log.info("  Total processed : %d / %d", stats["processed"], total)
    log.info("  Leads found     : %d", stats["leads_found"])
    log.info("  Skipped (closed): %d", stats["skipped_closed"])
    log.info("  Skipped (website): %d", stats["skipped_website"])
    log.info("  Skipped (dupe)  : %d", stats["skipped_duplicate"])
    log.info("  Errors          : %d", stats["errors"])
    log.info("=" * 50)

    write_progress(
        "step2", stats["processed"], total,
        status="done",
        message=(
            f"Done! {stats['leads_found']} leads found, "
            f"{stats['errors']} errors."
        ),
    )


if __name__ == "__main__":
    main()
