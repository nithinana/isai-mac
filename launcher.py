"""Isai for macOS: runs the FastAPI backend on 127.0.0.1 and shows the frontend in a native window.

Same idea as the Windows and Android apps: the backend serves the UI and the API from one local
address, so the app works without the shared server. The UI itself is the live isai.thirai.uk page,
saved on disk each time so it also opens offline."""
import asyncio
import os
import re
import sys
import socket
import subprocess
import threading
import time
import traceback
import urllib.request

HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
PORT = 8765   # fixed on purpose: the saved library and downloads belong to this address

# a windowed app has no console, so send output to a log file
LOGDIR = os.path.expanduser("~/Library/Logs/Isai")
os.makedirs(LOGDIR, exist_ok=True)
_log = open(os.path.join(LOGDIR, "isai.log"), "a", buffering=1, encoding="utf-8")
sys.stdout = sys.stderr = _log

# bundled ffmpeg first, so downloads can convert to mp3 / m4a
FF = os.path.join(HERE, "ffmpeg")
if os.path.isdir(FF):
    os.environ["PATH"] = FF + os.pathsep + os.environ.get("PATH", "")

# no Node on a Mac by default: use YouTube clients that need no JS challenge solving
os.environ.setdefault("YTDLP_FAST_CLIENT", "android_vr")
os.environ.setdefault("YTDLP_AUDIO_CLIENTS", "android_vr;tv;mweb;web_safari")
os.environ.setdefault("YTDLP_REMOTE_COMPONENTS", "")

SPLASH = """<body style="margin:0;height:100vh;display:grid;place-items:center;background:#0a0a0b">
<style>@keyframes f{0%,100%{opacity:0;transform:scale(.97)}50%{opacity:1;transform:scale(1)}}
img{height:34px;max-width:60vw;opacity:0;animation:f 1.8s ease-in-out infinite}</style>
<img src="https://thirai.uk/static/thiraiwhite.png" alt="Thirai"></body>"""


def port_open(port):
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def isai_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/__isai_ping", timeout=1.5) as r:
            return r.read() == b"ok"
    except Exception:
        return False


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


LIVE_URL = "https://isai.thirai.uk/"
CACHE_DIR = os.path.expanduser("~/Library/Application Support/Isai")
CACHE_FILE = os.path.join(CACHE_DIR, "index.html")
os.makedirs(CACHE_DIR, exist_ok=True)

# Thirai logo fade shown while the page boots. Added here so it works even if the live page lacks it.
BOOT = """<style>#boot{position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;background:#0a0a0b;transition:opacity .45s ease}
#boot.out{opacity:0;pointer-events:none}
#boot img{height:34px;width:auto;max-width:60vw;object-fit:contain;opacity:0;animation:bootfade 1.8s ease-in-out infinite}
@keyframes bootfade{0%,100%{opacity:0;transform:scale(.97)}50%{opacity:1;transform:scale(1)}}</style>
<div id="boot"><img src="https://thirai.uk/static/thiraiwhite.png" alt="Thirai"></div>
<script>(function(){function h(){var b=document.getElementById('boot');if(!b||b.__h)return;b.__h=1;
var w=Math.max(0,1200-performance.now());setTimeout(function(){b.classList.add('out');setTimeout(function(){b.remove()},500)},w)}
var t=setInterval(function(){if(typeof window.__unlocked!=='undefined'){clearInterval(t);h()}},100);setTimeout(h,10000)})();</script>"""


def fetch_live(timeout=4):
    """The latest page from isai.thirai.uk, or None if offline / unreachable / not a real page."""
    try:
        req = urllib.request.Request(LIVE_URL, headers={"User-Agent": "IsaiMac", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            raw = r.read()
    except Exception:
        return None
    txt = raw.decode("utf-8", "replace")
    if len(raw) < 50000 or "</html>" not in txt.lower() or 'id="lock"' not in txt:
        return None     # an error or captive-portal page: never cache that
    return raw


def read_cache():
    try:
        with open(CACHE_FILE, "rb") as f:
            return f.read()
    except OSError:
        return None


def save_cache(raw):
    try:
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, CACHE_FILE)
    except OSError:
        print("[isai] could not write page cache", flush=True)


def patch_html(raw):
    html = raw.decode("utf-8", "replace")
    # The UI ignores a saved 127.0.0.1:8765 address and falls back to the public API, so change its
    # default server to wherever it is being served from (this backend) instead.
    html, n = re.subn(r"const defBase\s*=\s*[^;]+;", "const defBase = location.origin;", html, count=1)
    print("[isai] default server patched" if n else "[isai] WARNING: defBase not found, UI will use the public API", flush=True)
    # the app counts as installed (no install banner)
    head = "<script>try{localStorage.setItem('installed','1')}catch(e){}</script>"
    i = html.lower().find("<head>")
    html = html[:i + 6] + head + html[i + 6:] if i >= 0 else head + html
    if 'id="boot"' not in html:
        k = html.lower().find("<body")
        k = html.find(">", k) + 1 if k >= 0 else 0
        html = html[:k] + BOOT + html[k:]
    return html.encode("utf-8")


class Frontend:
    """Serves the UI at /. Always the latest isai.thirai.uk page when online (saved to disk each time),
    the saved copy when offline, and the bundled copy only if neither exists.

    It is served from this local address rather than loaded straight from isai.thirai.uk so the sign-in,
    downloads and library stay in one place (browser storage is per address) and work offline."""
    def __init__(self, app):
        self.app = app
        with open(os.path.join(HERE, "index.html"), "rb") as f:
            self.bundled = f.read()
        self.raw = read_cache() or self.bundled
        self.on_update = None
        threading.Thread(target=self._refresh_loop, daemon=True).start()

    def _refresh(self, timeout=4):
        raw = fetch_live(timeout)
        if raw is None:
            return False, False
        changed = raw != self.raw
        if changed:
            self.raw = raw
            save_cache(raw)
            print("[isai] page updated from isai.thirai.uk", flush=True)
        return True, changed

    def _refresh_loop(self):
        """Every 2 minutes: pick up a new page, and tell the window when one is ready."""
        while True:
            time.sleep(120)
            ok, changed = self._refresh()
            if changed and self.on_update:
                try:
                    self.on_update()
                except Exception:
                    pass

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") == "GET":
            p = scope.get("path")
            if p in ("/", "/index.html"):
                await asyncio.get_running_loop().run_in_executor(None, self._refresh)   # online: update; offline: keep saved copy
                body = patch_html(self.raw)
                await send({"type": "http.response.start", "status": 200, "headers": [
                    (b"content-type", b"text/html; charset=utf-8"), (b"cache-control", b"no-cache"),
                    (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
            if p == "/__isai_ping":
                await send({"type": "http.response.start", "status": 200, "headers": [
                    (b"content-type", b"text/plain"), (b"content-length", b"2")]})
                await send({"type": "http.response.body", "body": b"ok"})
                return
        await self.app(scope, receive, send)


class Server:
    """Runs the backend and keeps it running: restarts it if it crashes or stops answering."""
    def __init__(self, port):
        self.port = port
        self.srv = None
        self.thread = None
        self.frontend = None
        self.lock = threading.Lock()
        self.busy = False
        self.on_restart = None

    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    def _run(self):
        try:
            os.environ["PUBLIC_URL"] = f"http://127.0.0.1:{self.port}"
            import backend
            import uvicorn
            if self.frontend is None:
                self.frontend = Frontend(backend.app)
            cfg = uvicorn.Config(self.frontend, host="127.0.0.1", port=self.port, log_config=None,
                                 log_level="warning", access_log=False, loop="asyncio", http="h11",
                                 timeout_graceful_shutdown=2)
            self.srv = uvicorn.Server(cfg)
            self.srv.run()
        except BaseException:
            print("[isai] server crashed:\n" + traceback.format_exc(), flush=True)

    def start(self):
        with self.lock:
            if self.alive():
                return
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def stop(self, wait=8):
        s, t = self.srv, self.thread
        if s is not None:
            s.should_exit = True
        if t is not None:
            t.join(wait)

    def restart(self):
        """Manual or automatic restart. Safe to call from any thread."""
        if self.busy:
            return
        self.busy = True
        try:
            print("[isai] restarting server", flush=True)
            self.stop()
            self.start()
            for _ in range(80):
                if isai_running(self.port):
                    break
                time.sleep(0.25)
        finally:
            self.busy = False
        if self.on_restart:
            try:
                self.on_restart()
            except Exception:
                pass

    def watch(self):
        """Checks every 4 s. Restarts if the server thread died, or if it has not answered for ~20 s."""
        bad = 0
        while True:
            time.sleep(4)
            if self.busy:
                continue
            if isai_running(self.port):
                bad = 0
                continue
            if not self.alive():
                print("[isai] server stopped, restarting", flush=True)
                bad = 0
                self.restart()
                continue
            bad += 1
            if bad >= 5:
                print("[isai] server not answering, restarting", flush=True)
                bad = 0
                self.restart()


def main():
    import webview
    from webview.menu import Menu, MenuAction

    port = PORT
    if port_open(port) and not isai_running(port):
        port = free_port()          # something else owns 8765
    mgr = Server(port)
    owned = not isai_running(port)  # False if another Isai window already runs the server
    if owned:
        mgr.start()
    url = f"http://127.0.0.1:{port}/"
    state = {"failed": False}

    win = webview.create_window("Isai", html=SPLASH, width=1200, height=800,
                                min_size=(420, 600), background_color="#0a0a0b")

    def after_restart():
        if state["failed"]:
            state["failed"] = False
            win.load_url(url)
        else:
            win.evaluate_js("try{toast('Server restarted')}catch(e){}")

    mgr.on_restart = after_restart

    def page_updated():
        win.evaluate_js("try{toast('Update ready \u00b7 choose Server > Reload page')}catch(e){}")

    def wire_update():
        for _ in range(240):
            if mgr.frontend is not None:
                mgr.frontend.on_update = page_updated
                return
            time.sleep(0.25)

    threading.Thread(target=wire_update, daemon=True).start()

    def go():
        for _ in range(240):        # wait up to ~60 s for the server
            if isai_running(port):
                win.load_url(url)
                break
            time.sleep(0.25)
        else:
            state["failed"] = True
            win.load_html("<body style='background:#0a0a0b;color:#f4f4f7;font-family:sans-serif;padding:40px'>"
                          "Isai could not start its server yet.<br><br>Choose Server &gt; Restart server from the menu bar, "
                          "or see ~/Library/Logs/Isai/isai.log</body>")
        if owned:
            threading.Thread(target=mgr.watch, daemon=True).start()

    def restart_server():
        threading.Thread(target=mgr.restart, daemon=True).start()

    def reload_page():
        win.load_url(url)

    def show_log():
        subprocess.Popen(["open", os.path.join(LOGDIR, "isai.log")])

    menu = [Menu("Server", [MenuAction("Restart server", restart_server),
                            MenuAction("Reload page", reload_page),
                            MenuAction("Show log", show_log)])]

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.start(go, menu=menu, private_mode=False)


if __name__ == "__main__":
    main()
