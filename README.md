# Desktop XHR Collector (Python)

This project now includes a desktop application with:

- Browser area (left side) for normal browsing
- Capture panel (right side) that shows all captured XHR/fetch URLs in a list
- `Save` button to export captured list
- `Clear` button to reset the list

## Setup

1. Create virtual environment (optional but recommended):
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
2. Install dependencies:
   - `pip install -r requirements.txt`

## Run Desktop App

- `python desktop_xhr_collector.py`

If you get a Qt error about platform plugin `xcb`, install this Linux dependency first:

- `sudo apt install libxcb-cursor0`

If you are on Xorg (Linux Mint default), run with XCB explicitly:

- `QT_QPA_PLATFORM=xcb python desktop_xhr_collector.py`

If XCB still fails on Xorg, install common Qt XCB runtime libraries:

- `sudo apt install libxkbcommon-x11-0 libxcb-icccm4 libxcb-keysyms1 libxcb-xinerama0 libxcb-render-util0`

Optional fallback on Wayland sessions:

- `QT_QPA_PLATFORM=wayland python desktop_xhr_collector.py`

If the app exits immediately with no window, verify GUI session variables:

- `echo $XDG_SESSION_TYPE`
- `echo $DISPLAY`

Expected on Linux Mint Xorg: `XDG_SESSION_TYPE=x11` and a non-empty `DISPLAY` (for example `:0`).

How to use:

1. Enter a URL and click `Go`.
2. Browse normally in the embedded browser.
3. Captured `xhr` and `fetch` calls appear in the right panel.
4. Click `Save` to export data as `.json` or `.txt`.
5. Click `Clear` to empty the current captured list.

## Saved Data

Each captured item includes:

- started_at
- finished_at
- api (`xhr` or `fetch`)
- method
- url
- page (where request originated)
- request_headers
- request_body_preview
- response_status
- response_status_text
- response_ok
- response_headers
- response_body_preview (for textual responses)
- response_body_error (if response body could not be read)

## Legacy CLI Capture (Optional)

The previous CLI collector is still available:

- `python xhr_collector.py --url https://www.carrefour.es`

For the CLI tool, install browser runtime once:

- `python -m playwright install chromium`
