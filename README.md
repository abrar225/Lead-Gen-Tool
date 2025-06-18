# 📍 Google Maps Lead Harvester

A two-stage Google Maps scraper built with **Streamlit** and **Playwright**.  
It collects business links in **Step 1** and extracts detailed lead information (Business Name, Phone, Rating, Reviews) in **Step 2**.

---

## 🚀 Setup

### 1. Install Python Dependencies

Requires **Python 3.10+**.

```bash
pip install -r requirements.txt
```

### 2. Install Playwright Browsers

Playwright needs its own browser binaries:

```bash
playwright install chromium
```

---

## 🛠️ Usage

### Running the Dashboard

```bash
streamlit run app.py
```

### Pipeline Overview

1. **Step 1 — Collect Links**: Enter a search query (e.g., *"Plumbers in New York"*), set a target count, and click **Collect**. Links are saved to `tasks.csv`.
2. **Step 2 — Extract Leads**: Click **Extract** to visit each link, filter by criteria (no website, not closed), and save valid leads to `leads.csv`.
3. **Download**: Export leads as a CSV file directly from the dashboard.

### Command-Line Usage (Optional)

You can also run each step independently:

```bash
# Step 1: Collect links
python step1_collector.py "Dentists in Chicago" 50

# Step 1: Headless mode (no browser window)
python step1_collector.py "Dentists in Chicago" 50 --headless

# Step 2: Extract leads
python step2_extractor.py "Dentists in Chicago"

# Step 2: Headless + resume (automatically skips already-processed URLs)
python step2_extractor.py "Dentists in Chicago" --headless
```

---

## 📁 Project Structure

| File | Description |
|---|---|
| `app.py` | Streamlit dashboard — pipeline controller & data viewer |
| `step1_collector.py` | Stage 1: Scrolls Google Maps and collects place URLs |
| `step2_extractor.py` | Stage 2: Visits each URL and extracts business details |
| `utils.py` | Shared utilities: extraction helpers, CSV I/O, validation, logging |
| `requirements.txt` | Python dependencies |
| `tasks.csv` | *(Generated)* Collected place URLs |
| `leads.csv` | *(Generated)* Extracted lead data |

---

## ✨ Features

- **Two-stage pipeline** — Collect then extract, with independent controls  
- **Resume capability** — Step 2 skips already-processed URLs on restart  
- **Cross-run deduplication** — Phone numbers are deduplicated across runs  
- **Real-time progress** — Live progress bar on the Streamlit dashboard  
- **Headless mode** — Run without a visible browser window  
- **Graceful shutdown** — Saves progress on Ctrl+C or Stop button  
- **Structured logging** — Timestamped logs saved to `step1.log` / `step2.log`  
- **Input validation** — Sanitized queries prevent injection or malformed URLs  

---

## ⚠️ Disclaimer

This tool is for **educational and personal use only**. Automated scraping of Google Maps may violate Google's Terms of Service. Use responsibly and at your own risk.
<!-- [2025-04-16T11:56:14] style: improve formatting and badge alignment -->
<!-- [2025-06-14T18:12:00] docs(readme): update project documentation and overview -->
<!-- [2025-06-16T18:57:53] style: improve formatting and badge alignment -->
<!-- [2025-06-18T16:07:31] style: improve formatting and badge alignment -->
