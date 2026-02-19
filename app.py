"""
Google Maps Lead Harvester — Streamlit Dashboard

A two-stage pipeline controller with real-time progress monitoring,
proper subprocess and error management.
"""

import streamlit as st
import pandas as pd
import subprocess
import time
import os
import sys
from pathlib import Path

# ── Must be first Streamlit call ──
st.set_page_config(
    page_title="Google Maps Lead Harvester",
    page_icon="📍",
    layout="wide",
)

# ── Project paths ──
PROJECT_DIR = Path(__file__).parent
TASKS_FILE = PROJECT_DIR / "tasks.csv"
LEADS_FILE = PROJECT_DIR / "leads.csv"
PROGRESS_FILE = PROJECT_DIR / ".progress.json"
PID_FILE_STEP1 = PROJECT_DIR / ".pid_step1"
PID_FILE_STEP2 = PROJECT_DIR / ".pid_step2"

# ── Import shared utilities ──
sys.path.insert(0, str(PROJECT_DIR))
from utils import (
    read_progress,
    clear_progress,
    read_csv_safe,
    sanitize_search_query,
    is_pid_running,
    terminate_process_tree,
)

# ── Auto-install Playwright (for Streamlit Cloud) ──
def ensure_playwright_installed():
    """
    Ensure Playwright browsers are installed. 
    Critical for Streamlit Cloud where we can't easily run shell commands.
    """
    import subprocess
    try:
        # Check if we can proceed without installing (naive check)
        # Better: just try to install, it handles existence check internally or is fast
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,  # Don't crash if it fails, might already be there
            capture_output=True
        )
    except Exception:
        pass

# Run installation check once on startup
if "playwright_installed" not in st.session_state:
    with st.spinner("Preparing application engine (one-time setup)..."):
        ensure_playwright_installed()
    st.session_state["playwright_installed"] = True


# ──────────────────────────────────────────────
# Process Management (PID file based)
# ──────────────────────────────────────────────

def save_pid(pid_file: Path, pid: int):
    """Save a subprocess PID to a file."""
    pid_file.write_text(str(pid), encoding="utf-8")


def load_pid(pid_file: Path) -> int | None:
    """Load a PID from file, return None if not found or invalid."""
    try:
        if pid_file.exists():
            text = pid_file.read_text(encoding="utf-8").strip()
            if text.isdigit():
                return int(text)
    except (OSError, ValueError):
        pass
    return None


def clear_pid(pid_file: Path):
    """Remove a PID file."""
    try:
        if pid_file.exists():
            pid_file.unlink()
    except OSError:
        pass


def is_step_running(pid_file: Path) -> bool:
    """Check if a step's subprocess is currently running."""
    pid = load_pid(pid_file)
    if pid and is_pid_running(pid):
        return True
    # Process gone — clean up stale PID file
    clear_pid(pid_file)
    return False


def stop_all_processes():
    """Terminate all running pipeline subprocesses."""
    stopped = False
    for pid_file in [PID_FILE_STEP1, PID_FILE_STEP2]:
        pid = load_pid(pid_file)
        if pid and is_pid_running(pid):
            terminate_process_tree(pid)
            stopped = True
        clear_pid(pid_file)
    clear_progress()
    return stopped


# ──────────────────────────────────────────────
# UI: Sidebar
# ──────────────────────────────────────────────

st.title("📍 Google Maps Lead Harvester")
st.caption("Two-stage pipeline: Collect links → Extract leads")

with st.sidebar:
    st.header("⚙️ Configuration")

    search_query = st.text_input(
        "Search Query",
        value="Interior designers in Ahmedabad",
        help="E.g., 'Plumbers in New York' or 'Dentists near Chicago'",
    )
    limit = st.number_input(
        "Target Lead Count",
        min_value=1,
        max_value=500,
        value=20,
        step=5,
    )
    headless_mode = st.checkbox(
        "Headless Mode",
        value=True,  # Default to True for server/cloud compatibility
        help="Run browser invisibly (recommended for servers / background runs).",
    )

    st.divider()
    st.header("🚀 Pipeline Controls")

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        btn_step1 = st.button(
            "▶ Step 1: Collect",
            use_container_width=True,
            type="primary",
        )
    with col_s2:
        btn_step2 = st.button(
            "▶ Step 2: Extract",
            use_container_width=True,
            type="primary",
        )

    st.divider()

    col_stop, col_clear = st.columns(2)
    with col_stop:
        btn_stop = st.button(
            "⛔ Stop All",
            use_container_width=True,
        )
    with col_clear:
        btn_clear = st.button(
            "🗑️ Clear Data",
            use_container_width=True,
        )


# ──────────────────────────────────────────────
# Python Environment Helper
# ──────────────────────────────────────────────

def get_python_runner() -> str:
    """
    Find a Python interpreter that has 'playwright' installed.
    Checks: 1. Current sys.executable
            2. Local .venv/Scripts/python.exe (Windows)
            3. Local .venv/bin/python (Linux/Mac)
    """
    candidates = [sys.executable]
    
    # Check for common venv patterns
    for venv_name in [".venv", "venv", "env"]:
        if sys.platform == "win32":
            candidates.append(str(PROJECT_DIR / venv_name / "Scripts" / "python.exe"))
        else:
            candidates.append(str(PROJECT_DIR / venv_name / "bin" / "python"))

    for py_path in candidates:
        if not os.path.exists(py_path):
            continue
            
        try:
            # Quick check if playwright is importable
            result = subprocess.run(
                [py_path, "-c", "import playwright; print('ok')"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0 and "ok" in result.stdout:
                return py_path
        except Exception:
            continue
            
    return sys.executable  # Fallback to current (will fail later if invalid)


# ──────────────────────────────────────────────
# Action Handlers
# ──────────────────────────────────────────────

# ── Stop ──
if btn_stop:
    if stop_all_processes():
        st.toast("All processes stopped.", icon="⛔")
    else:
        st.toast("No running processes found.", icon="ℹ️")

# ── Clear ──
if btn_clear:
    stop_all_processes()
    for f in [TASKS_FILE, LEADS_FILE, PROGRESS_FILE]:
        try:
            if f.exists():
                f.unlink()
        except OSError:
            pass
    st.toast("All data cleared.", icon="🗑️")
    st.rerun()

# ── Start Step 1 ──
if btn_step1:
    # Validate query
    try:
        clean_query = sanitize_search_query(search_query)
    except ValueError as exc:
        st.error(f"Invalid search query: {exc}")
        st.stop()

    # Stop any running processes first
    stop_all_processes()
    clear_progress()

    python_exe = get_python_runner()
    cmd = [python_exe, str(PROJECT_DIR / "step1_collector.py"),
           clean_query, str(limit)]
    if headless_mode:
        cmd.append("--headless")

    # Use a log file for stderr capturing to avoid pipe buffer deadlocks
    params = dict(
        cwd=str(PROJECT_DIR),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP 
        if sys.platform == "win32" else 0,
        stderr=subprocess.PIPE,
        text=True
    )
    
    proc = subprocess.Popen(cmd, **params)
    save_pid(PID_FILE_STEP1, proc.pid)
    
    # Check for immediate failure
    time.sleep(2)
    if proc.poll() is not None:
        # Process died immediately
        _, err = proc.communicate()
        save_pid(PID_FILE_STEP1, proc.pid) # Should clean this up but keeping logic simple
        st.error(f"Step 1 failed to start using {python_exe}!\nError:\n{err or 'Unknown error'}")
        clear_pid(PID_FILE_STEP1) 
    else:
        st.toast(f"Step 1 started (PID: {proc.pid})", icon="🚀")
        st.rerun()

# ── Start Step 2 ──
if btn_step2:
    if not TASKS_FILE.exists() or TASKS_FILE.stat().st_size == 0:
        st.error("No tasks file found! Run **Step 1** first to collect links.")
        st.stop()

    # Validate query  
    clean_query = search_query.strip() if search_query else ""

    stop_all_processes()
    clear_progress()

    python_exe = get_python_runner()
    cmd = [python_exe, str(PROJECT_DIR / "step2_extractor.py"), clean_query]
    if headless_mode:
        cmd.append("--headless")

    params = dict(
        cwd=str(PROJECT_DIR),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP 
        if sys.platform == "win32" else 0,
        stderr=subprocess.PIPE,
        text=True
    )

    proc = subprocess.Popen(cmd, **params)
    save_pid(PID_FILE_STEP2, proc.pid)
    
    # Check for immediate failure
    time.sleep(2)
    if proc.poll() is not None:
        _, err = proc.communicate()
        st.error(f"Step 2 failed to start using {python_exe}!\nError:\n{err or 'Unknown error'}")
        clear_pid(PID_FILE_STEP2)
    else:
        st.toast(f"Step 2 started (PID: {proc.pid})", icon="🚀")
        st.rerun()


# ──────────────────────────────────────────────
# UI: Dashboard
# ──────────────────────────────────────────────

# ── Live Progress ──
progress = read_progress()
step1_running = is_step_running(PID_FILE_STEP1)
step2_running = is_step_running(PID_FILE_STEP2)
any_running = step1_running or step2_running

if progress and (any_running or progress.get("status") in ("done", "error")):
    st.divider()

    step_label = progress.get("step", "").upper().replace("STEP", "Step ")
    status = progress.get("status", "running" if any_running else "unknown")
    current = progress.get("current", 0)
    total = progress.get("total", 1)
    message = progress.get("message", "")

    if any_running:
        st.info(f"🔄 **{step_label}** — {message}", icon="⏳")
        fraction = min(max(current / max(total, 1), 0.0), 1.0)
        st.progress(fraction, text=f"{current} / {total}")
    elif status == "done":
        st.success(f"✅ **{step_label}** — {message}", icon="✅")
    elif status == "error":
        st.error(f"❌ **{step_label}** — {message}", icon="❌")

st.divider()

# ── Data Columns ──
col1, col2 = st.columns(2)

# ── Column 1: Tasks ──
with col1:
    st.subheader("📋 Step 1: Collected Links")

    if step1_running:
        st.status("✨ Collection in progress...", expanded=False)
        st.caption(f"🔄 PID: {load_pid(PID_FILE_STEP1)}")

    if TASKS_FILE.exists() and TASKS_FILE.stat().st_size > 0:
        try:
            df_tasks = pd.read_csv(str(TASKS_FILE), encoding="utf-8-sig")
            st.metric("Links Collected", len(df_tasks))
            with st.expander("View Collected Links", expanded=False):
                st.dataframe(df_tasks, use_container_width=True)
        except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as exc:
            st.warning(f"Cannot read tasks file: {exc}")
    else:
        if not step1_running:
            st.caption("_No tasks collected yet. Run Step 1 to start._")
        else:
            st.caption("_Collecting links... numbers will appear here soon._")


# ── Column 2: Leads ──
with col2:
    st.subheader("🎯 Step 2: Extracted Leads")

    if step2_running:
        st.status("⛏️ Extraction in progress...", expanded=False)
        st.caption(f"🔄 PID: {load_pid(PID_FILE_STEP2)}")

    if LEADS_FILE.exists() and LEADS_FILE.stat().st_size > 0:
        try:
            df_leads = pd.read_csv(str(LEADS_FILE), encoding="utf-8-sig")

            # Metrics row
            m1, m2, m3 = st.columns(3)
            m1.metric("Total Leads", len(df_leads))
            
            phone_col = "Phone" if "Phone" in df_leads.columns else ""
            if phone_col:
                has_phone = df_leads[
                    (df_leads[phone_col] != "N/A") & (df_leads[phone_col].notna())
                ]
                m2.metric("With Phone", len(has_phone))
                m3.metric("Without", len(df_leads) - len(has_phone))
            else:
                m2.metric("With Phone", 0)
                m3.metric("Without", len(df_leads))

            st.dataframe(df_leads, use_container_width=True)

            # Download button
            csv_bytes = df_leads.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="📥 Download Leads CSV",
                data=csv_bytes,
                file_name="leads_export.csv",
                mime="text/csv",
                use_container_width=True,
            )

        except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as exc:
            st.warning(f"Cannot read leads file: {exc}")
    else:
        if not step2_running:
            st.caption("_No leads extracted yet. Run Step 2 after collecting links._")
        else:
            st.caption("_Extracting leads... progress will appear here._")


# ──────────────────────────────────────────────
# Auto-refresh while a process is running
# ──────────────────────────────────────────────
if any_running:
    time.sleep(1.5)
    st.rerun()
