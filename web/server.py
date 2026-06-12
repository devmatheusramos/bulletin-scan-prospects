import csv
import json
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

app = FastAPI()

_TEMPLATES = Path(__file__).parent / "templates"

# Shared data volume mounted at /data; fall back to repo-relative for local runs
_DATA_CANDIDATES = [Path("/data"), Path("data")]


def _data_dir() -> Path:
    for p in _DATA_CANDIDATES:
        if p.exists():
            return p
    return Path("/data")


def _find_csv() -> Path | None:
    d = _data_dir()
    primary = d / "prospects_refined.csv"
    if primary.exists():
        return primary
    candidates = sorted(d.glob("*.csv"))
    return candidates[-1] if candidates else None


def _lead_key(raw: str) -> str:
    return raw.lower().replace(" lead", "").replace("not suitable", "none").strip()


def _read_rows() -> list[dict]:
    path = _find_csv()
    if not path:
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _to_church(row: dict) -> dict:
    lat = row.get("Lat", "")
    lng = row.get("Lng", "")
    return {
        "name": row.get("Name", ""),
        "diocese": row.get("Diocese", ""),
        "lead": _lead_key(row.get("Lead", "none")),
        "score": float(row.get("Score") or 0),
        "bulletinFound": row.get("Bulletin PDF", "No") == "Yes",
        "bulletinUrl": row.get("Bulletin URL") or None,
        "address": row.get("Address", ""),
        "phone": row.get("Phone") or None,
        "email": row.get("Email") or None,
        "website": row.get("Website") or None,
        "socials": {
            "facebook": row.get("Facebook", "No") == "Yes",
            "instagram": row.get("Instagram", "No") == "Yes",
            "youtube": row.get("YouTube", "No") == "Yes",
            "twitter": row.get("Twitter", "No") == "Yes",
        },
        "lat": float(lat) if lat else None,
        "lng": float(lng) if lng else None,
    }


def _compute_stats(rows: list[dict]) -> dict:
    counts: dict[str, int] = {"hot": 0, "warm": 0, "cold": 0, "none": 0}
    for row in rows:
        key = _lead_key(row.get("Lead", "none"))
        counts[key] = counts.get(key, 0) + 1
    return {"total": len(rows), **counts}


# ── Scraper state ───────────────────────────────────────────────
_lock = threading.Lock()
_state: dict = {"running": False, "last_run": None, "last_error": None}


def _run_scraper() -> None:
    try:
        result = subprocess.run(
            ["python", "-u", "/app/scraper/refinar_prospects.py"],
            capture_output=True,
            text=True,
            timeout=3600,
        )
        with _lock:
            _state["last_error"] = (result.stderr or "")[-1000:] if result.returncode != 0 else None
    except Exception as exc:
        with _lock:
            _state["last_error"] = str(exc)
    finally:
        with _lock:
            _state["running"] = False
            _state["last_run"] = datetime.now().isoformat()


# ── Routes ──────────────────────────────────────────────────────

@app.get("/", response_class=RedirectResponse, status_code=302)
def root():
    return "/dashboard"


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return (_TEMPLATES / "dashboard_prospects.html").read_text(encoding="utf-8")


@app.get("/mapa", response_class=HTMLResponse)
def mapa():
    return (_TEMPLATES / "mapa_prospects.html").read_text(encoding="utf-8")


@app.get("/prospects_data.js", response_class=PlainTextResponse)
def prospects_data_js():
    """Serve pre-loaded data as a JS file so the map page can consume it."""
    rows = _read_rows()
    churches = [_to_church(r) for r in rows]
    counts = _compute_stats(rows) if rows else {}
    js = (
        f"// Gerado em {datetime.now().strftime('%Y-%m-%d')} | {len(churches)} igrejas\n"
        f"// Hot:{counts.get('hot',0)} Warm:{counts.get('warm',0)} Cold:{counts.get('cold',0)}\n"
        f"var PRELOADED_DATA = {json.dumps(churches, ensure_ascii=False)};\n"
    )
    return PlainTextResponse(js, media_type="application/javascript")


@app.get("/api/stats")
def api_stats():
    rows = _read_rows()
    if not rows:
        return JSONResponse({"error": "no_data"}, status_code=404)
    stats = _compute_stats(rows)
    csv_path = _find_csv()
    if csv_path:
        stats["generated"] = datetime.fromtimestamp(csv_path.stat().st_mtime).strftime("%d/%m/%Y")
    return stats


@app.get("/api/data")
def api_data():
    return [_to_church(r) for r in _read_rows()]


@app.get("/api/status")
def api_status():
    rows = _read_rows()
    csv_path = _find_csv()
    with _lock:
        state = dict(_state)
    return {
        "scraper": state,
        "csv_exists": csv_path is not None,
        "csv_updated": (
            datetime.fromtimestamp(csv_path.stat().st_mtime).isoformat()
            if csv_path
            else None
        ),
        "stats": _compute_stats(rows) if rows else None,
    }


@app.post("/api/scrape")
def api_scrape():
    with _lock:
        if _state["running"]:
            return JSONResponse({"status": "already_running"}, status_code=409)
        _state["running"] = True
        _state["last_error"] = None
    threading.Thread(target=_run_scraper, daemon=True).start()
    return {"status": "started"}
