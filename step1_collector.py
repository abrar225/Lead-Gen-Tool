"""
Step 1: Link Collector

Searches Google Maps for a query, scrolls the results feed,
and collects unique place URLs into tasks.csv.

Usage:
    python step1_collector.py "Plumbers in New York" 50
    python step1_collector.py "Plumbers in New York" 50 --headless
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
    atomic_csv_write,
    write_progress,
    clear_progress,
    TASKS_FILE,
    TASKS_COLUMNS,
    DEFAULT_USER_AGENT,
    DEFAULT_VIEWPORT,
)

# ── Logger ──
log = setup_logging("step1_collector", log_file="step1.log")

# ── Graceful shutdown flag ──
_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    log.warning("Shutdown signal received (signal %s). Finishing up...", signum)


# Register signal handlers
if sys.platform != "win32":
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Step 1: Collect Google Maps place links."
    )
    parser.add_argument("search_query", type=str, help="Search query string")
    parser.add_argument("limit", type=int, help="Target number of links to collect")
    parser.add_argument(
        "--headless", action="store_true", default=False,
        help="Run browser in headless mode."
    )
    return parser.parse_args()


def save_tasks_incremental(unique_urls, limit):
    """Save collected URLs to tasks.csv incrementally."""
    try:
        trimmed = list(unique_urls)[:limit]
        rows = [[u] for u in trimmed]
        atomic_csv_write(TASKS_FILE, rows, TASKS_COLUMNS)
    except Exception as exc:
        log.error("Failed to save tasks incrementally: %s", exc)


def handle_consent_screen(page):
    """Detect and click 'Accept all' or 'Agree' on Google consent popups."""
    try:
        # Common selectors for consent buttons
        consent_selectors = [
            'button[aria-label="Accept all"]',
            'button[aria-label="Agree"]',
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            'button:has-text("Accept")',
        ]
        for selector in consent_selectors:
            btn = page.locator(selector).first
            if btn.is_visible():
                log.info("Consent screen detected. Clicking '%s'...", selector)
                btn.click()
                time.sleep(2)
                return True
    except Exception:
        pass
    return False


def main():
    global _shutdown_requested

    args = parse_args()

    # ── Sanitize input ──
    try:
        search_query = sanitize_search_query(args.search_query)
    except ValueError as exc:
        log.error("Invalid search query: %s", exc)
        write_progress("step1", 0, 0, status="error", message=str(exc))
        sys.exit(1)

    limit = max(1, args.limit)
    headless = args.headless

    log.info("Starting link collection for '%s' | Target: %d | Headless: %s",
             search_query, limit, headless)
    
    # Initialize progress immediately so UI updates
    write_progress("step1", 0, limit, status="running",
                   message=f"Initializing browser...")

    unique_urls = set()
    browser = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=DEFAULT_USER_AGENT,
                viewport=DEFAULT_VIEWPORT,
                locale="en-US",
            )
            page = context.new_page()

            # ── Navigate ──
            query_formatted = search_query.replace(" ", "+")
            url = f"https://www.google.com/maps/search/{query_formatted}"
            log.info("Navigating to: %s", url)
            write_progress("step1", 0, limit, status="running",
                           message=f"Navigating to Google Maps...")
            
            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # Handle possible consent screen
            handle_consent_screen(page)

            # ── Wait for results feed ──
            # Multiple possible selectors as Google Gmaps UI varies
            feed_selectors = [
                'div[role="feed"]',
                '.m67q60', # Internal class
                'div[aria-label*="Results for"]',
                '.Nv2PK' # Result item class
            ]
            
            found_feed = False
            for selector in feed_selectors:
                try:
                    log.info("Checking for feed selector: %s", selector)
                    page.wait_for_selector(selector, timeout=10000)
                    found_feed = True
                    break
                except PwTimeout:
                    continue

            if not found_feed:
                # Check if it was "no results"
                if page.locator("text=Google Maps can't find").is_visible() or \
                   page.locator("text=No results found").is_visible():
                    log.warning("No results found for query.")
                    write_progress("step1", 0, limit, status="done",
                                   message="No results found.")
                    return
                
                log.error("Results feed not found — page may have failed to load results.")
                write_progress("step1", 0, limit, status="error",
                               message="Results feed not found. Maps might be blocking requests.")
                return

            # Main feed object (use generic role if possible, fallback to body)
            feed = page.locator('div[role="feed"]').first
            if feed.count() == 0:
                feed = page.locator('body')

            # ── Helper: extract visible place links ──
            def extract_visible():
                try:
                    # Look for links that contain maps/place/
                    links = page.locator('a[href*="/maps/place/"]').all()
                    added_new = False
                    for link in links:
                        try:
                            href = link.get_attribute("href")
                            if href and "/maps/place/" in href:
                                if href not in unique_urls:
                                    unique_urls.add(href)
                                    added_new = True
                        except Exception:
                            continue
                    return added_new
                except Exception:
                    return False

            # ── Scroll + collect loop ──
            stale_rounds = 0
            max_stale = 8 

            while len(unique_urls) < limit and not _shutdown_requested:
                added = extract_visible()
                
                # Update progress and save incrementally
                write_progress(
                    "step1", len(unique_urls), limit,
                    status="running",
                    message=f"Collecting links: found {len(unique_urls)} so far...",
                )
                if added:
                    save_tasks_incremental(unique_urls, limit)
                    stale_rounds = 0
                else:
                    stale_rounds += 1

                if len(unique_urls) >= limit:
                    break

                # Check for end-of-list indicator
                try:
                    if page.locator("text=reached the end").is_visible() or \
                       page.locator("text=You've reached the end").is_visible():
                        log.info("Reached the end of Google Maps results.")
                        break
                except Exception:
                    pass

                # Scroll down
                try:
                    # Move mouse to results area and scroll
                    page.mouse.wheel(0, 2000)
                    # Also try keyboard End if we can focus
                    page.keyboard.press("End")
                except Exception:
                    pass
                
                time.sleep(random.uniform(2.0, 4.0))

                if stale_rounds >= max_stale:
                    log.warning("No new links after %d scroll rounds. Stopping.", max_stale)
                    break

            # Final sweep and write
            extract_visible()
            save_tasks_incremental(unique_urls, limit)

            if _shutdown_requested:
                log.warning("Shutdown requested — tasks saved.")

            log.info("Collection complete. Found %d unique links.", len(unique_urls))

    except KeyboardInterrupt:
        log.warning("Interrupted by user.")
    except Exception as exc:
        log.error("Critical error in Step 1: %s", exc, exc_info=True)
        write_progress("step1", len(unique_urls), limit, status="error",
                       message=str(exc))
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass

    if unique_urls:
        write_progress(
            "step1", len(unique_urls), limit,
            status="done",
            message=f"Done! Collected {len(unique_urls)} links.",
        )
    else:
        log.warning("No links were collected.")
        if not _shutdown_requested:
            write_progress("step1", 0, limit, status="done",
                           message="Finished — no links found for this query.")

    log.info("Step 1 finished.")


if __name__ == "__main__":
    main()
