"""Isai for macOS: runs the FastAPI backend on 127.0.0.1 and shows the frontend in a native window.

Same idea as the Windows and Android apps: the backend serves the UI and the API from one local
address, so the app works without the shared server."""
import os
import re
import sys
import socket
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

SPLASH = """<body style="margin:0;height:100vh;display:grid;place-items:center;background:#0a0a0b;
color:#a3a3b0;font-family:-apple-system,sans-serif;font-size:14px">Starting Isai…</body>"""


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


class Frontend:
    """Serves the bundled UI at / (the backend's own copy is older) and answers a ping."""
    def __init__(self, app):
        self.app = app
        with open(os.path.join(HERE, "index.html"), "rb") as f:
            html = f.read().decode("utf-8", "replace")
        # the app talks to its own backend and counts as installed (no install banner)
        # The UI ignores a saved 127.0.0.1:8765 address and falls back to the public API, so change its
        # default server to wherever it is being served from (this backend) instead.
        html, n = re.subn(r"const defBase\s*=\s*[^;]+;", "const defBase = location.origin;", html, count=1)
        print("[isai] default server patched" if n else "[isai] WARNING: defBase not found, UI will use the public API", flush=True)
        boot = "<script>try{localStorage.setItem('installed','1')}catch(e){}</script>"
        i = html.lower().find("<head>")
        html = html[:i + 6] + boot + html[i + 6:] if i >= 0 else boot + html
        self.html = html.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") == "GET":
            p = scope.get("path")
            if p in ("/", "/index.html"):
                await send({"type": "http.response.start", "status": 200, "headers": [
                    (b"content-type", b"text/html; charset=utf-8"), (b"cache-control", b"no-cache"),
                    (b"content-length", str(len(self.html)).encode())]})
                await send({"type": "http.response.body", "body": self.html})
                return
            if p == "/__isai_ping":
                await send({"type": "http.response.start", "status": 200, "headers": [
                    (b"content-type", b"text/plain"), (b"content-length", b"2")]})
                await send({"type": "http.response.body", "body": b"ok"})
                return
        await self.app(scope, receive, send)


def run_server(port):
    try:
        os.environ["PUBLIC_URL"] = f"http://127.0.0.1:{port}"
        import backend
        import uvicorn
        cfg = uvicorn.Config(Frontend(backend.app), host="127.0.0.1", port=port, log_config=None,
                             log_level="warning", access_log=False, loop="asyncio", http="h11")
        uvicorn.Server(cfg).run()
    except BaseException:
        print("[isai] server crashed:\n" + traceback.format_exc(), flush=True)


def main():
    import webview
    port = PORT
    if port_open(port) and not isai_running(port):
        port = free_port()          # something else owns 8765
    if not isai_running(port):
        threading.Thread(target=run_server, args=(port,), daemon=True).start()
    url = f"http://127.0.0.1:{port}/"

    win = webview.create_window("Isai", html=SPLASH, width=1200, height=800,
                                min_size=(420, 600), background_color="#0a0a0b")

    def go():
        for _ in range(240):        # wait up to ~60 s for the server
            if isai_running(port):
                win.load_url(url)
                return
            time.sleep(0.25)
        win.load_html("<body style='background:#0a0a0b;color:#f4f4f7;font-family:sans-serif;padding:40px'>"
                      "Isai could not start its server.<br><br>See ~/Library/Logs/Isai/isai.log</body>")

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.start(go, private_mode=False)


if __name__ == "__main__":
    main()
