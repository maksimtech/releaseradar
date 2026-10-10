"""A local dashboard: `releaseradar web` polls GitHub every few minutes and
serves one page on 127.0.0.1 that shows what changed.

Standard library only — `http.server` and a thread — because a page that reads
a local SQLite file does not need a web framework, and the CLI must not grow
the dependency closure the `[mcp]` extra was kept out of the base install for.
Bound to the loopback address and nothing else: there is no authentication,
because there is nobody else on the other end.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from . import db as _db
from .sync import sync_all

ICONS = {
    "issue_opened": "🐛",
    "issue_closed": "✅",
    "pr_opened": "🔀",
    "pr_merged": "🟣",
    "pr_closed": "❌",
    "release_published": "🚀",
}


def _now() -> datetime:
    return datetime.now(UTC)


class Poller(threading.Thread):
    """Runs `sync_all` on start and then every `interval` seconds, or sooner when
    `trigger()` is called. What the last run produced is kept for the page."""

    def __init__(self, interval: float, token: str | None, db_path: Path) -> None:
        super().__init__(name="releaseradar-poller", daemon=True)
        self.interval = interval
        self.token = token
        self.db_path = db_path
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.running = False
        self.runs = 0
        self.last_started: datetime | None = None
        self.last_finished: datetime | None = None
        self.next_at: datetime | None = None
        self.last_events: dict[str, list[dict]] = {}
        self.last_errors: dict[str, str] = {}
        self.last_warnings: list[str] = []
        self.progress = ""

    # ── control ──────────────────────────────────────────────────────────────

    def trigger(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stop.is_set():
            self.sync_once()
            self._wake.clear()
            self._wake.wait(self.interval)

    # ── one run ──────────────────────────────────────────────────────────────

    def sync_once(self) -> None:
        warnings: list[str] = []
        with self._lock:
            self.running = True
            self.last_started = _now()
            self.progress = ""
        try:
            results, errors = sync_all(token=self.token, db_path=self.db_path,
                                       on_warning=warnings.append, on_progress=self._set_progress)
        except Exception as exc:  # a poller that dies silently is worse than one that reports
            results, errors = {}, {"*": f"{type(exc).__name__}: {exc}"}
        with self._lock:
            self.running = False
            self.runs += 1
            self.last_finished = _now()
            self.next_at = datetime.fromtimestamp(self.last_finished.timestamp() + self.interval, tz=UTC)
            self.last_events = results
            self.last_errors = errors
            self.last_warnings = warnings
            self.progress = ""

    def _set_progress(self, line: str) -> None:
        with self._lock:
            self.progress = line

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "runs": self.runs,
                "interval_seconds": self.interval,
                "last_started": self.last_started.isoformat() if self.last_started else None,
                "last_finished": self.last_finished.isoformat() if self.last_finished else None,
                "next_at": self.next_at.isoformat() if self.next_at else None,
                "new_events_last_run": sum(len(v) for v in self.last_events.values()),
                "errors": dict(self.last_errors),
                "warnings": list(self.last_warnings),
                "progress": self.progress,
            }


# ── what the page shows ─────────────────────────────────────────────────────

def _event_dict(row) -> dict:
    payload = json.loads(row["payload"]) if row["payload"] else {}
    return {
        "id": row["id"],
        "repo": f"{row['owner']}/{row['name']}",
        "kind": row["kind"],
        "icon": ICONS.get(row["kind"], "•"),
        "ref": row["ref_tag"] or (f"#{row['ref_number']}" if row["ref_number"] else ""),
        "title": payload.get("title") or payload.get("name") or "",
        "url": payload.get("url"),
        "seen_at": row["seen_at"],
        "notified": bool(row["notified"]),
    }


def state(db_path: Path, poller: Poller | None, limit: int = 100) -> dict[str, Any]:
    database = _db.open_db(db_path)
    repos = []
    for row in database.list_repos():
        summary = database.repo_summary(row["owner"], row["name"])
        latest = summary.get("latest_release")
        repos.append({
            "repo": f"{row['owner']}/{row['name']}",
            "open_issues": summary.get("open_issues", 0),
            "open_prs": summary.get("open_prs", 0),
            "latest_release": latest["tag_name"] if latest else None,
            "latest_release_at": latest["published_at"] if latest else None,
            "last_synced_at": row["last_synced_at"],
        })
    return {
        "version": __version__,
        "now": _now().isoformat(),
        "repos": repos,
        "events": [_event_dict(r) for r in database.recent_events(limit)],
        "poller": poller.snapshot() if poller else None,
    }


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ReleaseRadar</title>
<style>
  :root { --bg:#0f1115; --card:#171a21; --fg:#e6e6e6; --dim:#8a919e; --line:#262b35; --accent:#7aa2f7; --warn:#e0af68; --err:#f7768e; }
  @media (prefers-color-scheme: light) { :root { --bg:#f6f7f9; --card:#fff; --fg:#1b1f27; --dim:#6b7280; --line:#e5e7eb; --accent:#2f5fd0; --warn:#b45309; --err:#b91c1c; } }
  body { margin:0; font:15px/1.45 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background:var(--bg); color:var(--fg); }
  header { display:flex; align-items:center; gap:16px; padding:14px 20px; border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--bg); }
  header h1 { font-size:18px; margin:0; } header .dim { color:var(--dim); font-size:13px; }
  button { background:var(--accent); color:#fff; border:0; border-radius:8px; padding:8px 14px; font-size:14px; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  main { display:grid; grid-template-columns: minmax(280px, 1fr) minmax(320px, 2fr); gap:16px; padding:16px 20px; }
  @media (max-width: 860px) { main { grid-template-columns: 1fr; } }
  section { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
  h2 { font-size:14px; text-transform:uppercase; letter-spacing:.04em; color:var(--dim); margin:0 0 10px; }
  table { width:100%; border-collapse:collapse; } td, th { padding:6px 4px; text-align:left; border-bottom:1px solid var(--line); font-size:14px; }
  th { color:var(--dim); font-weight:500; } td.num { text-align:right; font-variant-numeric:tabular-nums; }
  .ev { display:grid; grid-template-columns: 28px 1fr auto; gap:8px; padding:8px 4px; border-bottom:1px solid var(--line); align-items:baseline; }
  .ev .repo { color:var(--dim); font-size:12px; } .ev .t { font-size:12px; color:var(--dim); white-space:nowrap; }
  .ev a { color:var(--fg); text-decoration:none; } .ev a:hover { text-decoration:underline; }
  .ev.fresh { background: color-mix(in srgb, var(--accent) 10%, transparent); }
  .warn { color:var(--warn); font-size:13px; } .err { color:var(--err); font-size:13px; }
  .empty { color:var(--dim); padding:12px 4px; }
  .progress { font-size:12px; color:var(--dim); }
</style>
</head>
<body>
<header>
  <h1>ReleaseRadar</h1>
  <span class="dim" id="status">loading…</span>
  <span class="progress" id="progress"></span>
  <span style="flex:1"></span>
  <button id="sync">Sync now</button>
</header>
<main>
  <section>
    <h2>Tracked repositories</h2>
    <table><thead><tr><th>Repository</th><th class="num">Issues</th><th class="num">PRs</th><th>Latest release</th><th>Synced</th></tr></thead>
    <tbody id="repos"></tbody></table>
    <div id="notes"></div>
  </section>
  <section>
    <h2>What changed <span class="dim" id="count"></span></h2>
    <div id="events"></div>
  </section>
</main>
<script>
const $ = (id) => document.getElementById(id);
let seen = new Set(), first = true;
function ago(iso) {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z")) / 1000);
  if (s < 60) return Math.round(s) + " s ago"; if (s < 3600) return Math.round(s/60) + " min ago";
  if (s < 86400) return Math.round(s/3600) + " h ago"; return Math.round(s/86400) + " d ago";
}
function inFuture(iso) { if (!iso) return ""; const s = (new Date(iso) - Date.now())/1000; return s <= 0 ? "now" : (s < 60 ? Math.round(s) + " s" : Math.round(s/60) + " min"); }
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
async function refresh() {
  let st; try { st = await (await fetch("/api/state")).json(); } catch (e) { $("status").textContent = "server unreachable"; return; }
  const p = st.poller || {};
  $("status").textContent = p.running ? "syncing…" : `last sync ${ago(p.last_finished)} · next in ${inFuture(p.next_at)} · every ${Math.round((p.interval_seconds||0)/60)} min · v${st.version}`;
  $("progress").textContent = p.running ? (p.progress || "") : "";
  $("sync").disabled = !!p.running;
  $("repos").innerHTML = st.repos.map(r => `<tr><td>${esc(r.repo)}</td><td class="num">${r.open_issues}</td><td class="num">${r.open_prs}</td><td>${esc(r.latest_release || "—")}</td><td>${ago(r.last_synced_at)}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">Nothing tracked yet: <code>releaseradar track owner/repo</code></td></tr>`;
  const notes = [...(p.warnings||[]).map(w => `<div class="warn">! ${esc(w)}</div>`), ...Object.entries(p.errors||{}).map(([k,v]) => `<div class="err">✗ ${esc(k)}: ${esc(v)}</div>`)];
  $("notes").innerHTML = notes.join("");
  $("count").textContent = st.events.length ? `(${st.events.length})` : "";
  $("events").innerHTML = st.events.map(e => {
    const fresh = !first && !seen.has(e.id); seen.add(e.id);
    const title = e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title || e.ref)}</a>` : esc(e.title || e.ref);
    return `<div class="ev${fresh ? " fresh" : ""}"><span>${e.icon}</span><span><div class="repo">${esc(e.repo)} · ${esc(e.kind.replace("_"," "))} ${esc(e.ref)}</div>${title}</span><span class="t">${ago(e.seen_at)}</span></div>`;
  }).join("") || `<div class="empty">No events yet. The first sync of a repository seeds its state and reports nothing; from then on, every transition lands here.</div>`;
  if (first) { st.events.forEach(e => seen.add(e.id)); first = false; }
}
$("sync").onclick = async () => { $("sync").disabled = true; await fetch("/api/sync", {method: "POST"}); setTimeout(refresh, 500); };
refresh(); setInterval(refresh, 5000);
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    server: DashboardServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the signature is the stdlib's
        return  # a dashboard polled every five seconds would drown the terminal

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
        if self.path == "/" or self.path.startswith("/?"):
            self._send(HTTPStatus.OK, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path.startswith("/api/state"):
            body = json.dumps(state(self.server.db_path, self.server.poller), default=str).encode("utf-8")
            self._send(HTTPStatus.OK, body, "application/json")
        else:
            self._send(HTTPStatus.NOT_FOUND, b'{"error": "not found"}', "application/json")

    def do_POST(self) -> None:  # noqa: N802 - the stdlib's name
        if self.path == "/api/sync":
            if self.server.poller is not None:
                self.server.poller.trigger()
            self._send(HTTPStatus.ACCEPTED, b'{"triggered": true}', "application/json")
        else:
            self._send(HTTPStatus.NOT_FOUND, b'{"error": "not found"}', "application/json")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host: str, port: int, db_path: Path, poller: Poller | None) -> None:
        super().__init__((host, port), _Handler)
        self.db_path = db_path
        self.poller = poller
        self.host = host

    @property
    def url(self) -> str:
        # server_port, not the requested port: 0 asks the OS for a free one.
        return f"http://{self.host}:{self.server_port}/"


def serve(db_path: Path, token: str | None, every_minutes: float, host: str = "127.0.0.1",
          port: int = 8765, open_browser: bool = True) -> None:
    """Start the poller and the page, open the browser, and block until Ctrl-C."""
    poller = Poller(interval=every_minutes * 60, token=token, db_path=db_path)
    server = DashboardServer(host, port, db_path, poller)
    poller.start()
    print(f"ReleaseRadar dashboard on {server.url} — syncing every {every_minutes:g} min; Ctrl-C to stop.")
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
        server.server_close()
