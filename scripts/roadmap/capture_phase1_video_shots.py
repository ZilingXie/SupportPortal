from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote

import websockets


ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "roadmap" / "shots"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PORT = 9227


SHOTS_SPEC = [
    ("01-why-now.png", "http://127.0.0.1:8765/docs/roadmap/phase1.html", None),
    ("02-big-picture.png", "http://127.0.0.1:8765/docs/roadmap/phase1.html#architecture", "architecture"),
    ("03-assignment-admin.png", "https://support.stellarix.space/assignment/admin", None),
    ("04-agentrelay-network.png", "http://127.0.0.1:8765/docs/roadmap/phase1.html#agentrelay", None),
    ("05-rnd-agent-example.png", "http://127.0.0.1:8765/docs/roadmap/phase1.html#agentrelay", "rnd"),
    ("06-guardrail-showcase.png", "http://127.0.0.1:8765/docs/roadmap/phase1.html#showcase", None),
    ("07-dashboard-roadmap.png", "http://127.0.0.1:8765/docs/roadmap/phase1.html#dashboard", None),
]


class CdpPage:
    def __init__(self, websocket_url: str) -> None:
        self.websocket_url = websocket_url
        self.next_id = 0

    async def __aenter__(self) -> "CdpPage":
        self.websocket = await websockets.connect(self.websocket_url, max_size=16 * 1024 * 1024)
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.websocket.close()

    async def call(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        self.next_id += 1
        message_id = self.next_id
        await self.websocket.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            raw = await self.websocket.recv()
            response = json.loads(raw)
            if response.get("id") == message_id:
                if "error" in response:
                    raise RuntimeError(f"CDP {method} failed: {response['error']}")
                return response.get("result", {})


def wait_for_debugger() -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1).read()
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("Chrome DevTools endpoint did not become ready")


def new_page(url: str) -> str:
    request = Request(
        f"http://127.0.0.1:{PORT}/json/new?{quote(url, safe='')}",
        method="PUT",
    )
    target = json.load(urlopen(request, timeout=5))
    return target["webSocketDebuggerUrl"]


async def capture_one(name: str, url: str, mode: str | None) -> None:
    async with CdpPage(new_page(url)) as page:
        await page.call("Page.enable")
        await page.call("Runtime.enable")
        await page.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": 1600,
                "height": 900,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        await asyncio.sleep(2.4)
        await page.call(
            "Runtime.evaluate",
            {
                "expression": """
                document.querySelectorAll('.reveal').forEach((node) => node.classList.add('visible'));
                document.documentElement.style.scrollBehavior = 'auto';
                """,
                "awaitPromise": False,
            },
        )
        if mode == "architecture":
            await page.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    const map = document.querySelector('.architecture-map');
                    if (map) map.scrollIntoView({block: 'center'});
                    """,
                    "awaitPromise": False,
                },
            )
            await asyncio.sleep(0.8)
        if mode == "rnd":
            await page.call(
                "Runtime.evaluate",
                {
                    "expression": """
                    const proof = document.querySelector('.rnd-proof');
                    if (proof) proof.scrollIntoView({block: 'center'});
                    """,
                    "awaitPromise": False,
                },
            )
            await asyncio.sleep(0.8)
        screenshot = await page.call(
            "Page.captureScreenshot",
            {
                "format": "png",
                "fromSurface": True,
                "captureBeyondViewport": False,
            },
        )
        output = SHOTS / name
        output.write_bytes(base64.b64decode(str(screenshot["data"])))
        print(f"{name}: {output.stat().st_size} bytes", flush=True)


async def capture_all() -> None:
    for name, url, mode in SHOTS_SPEC:
        await capture_one(name, url, mode)


def main() -> None:
    if not CHROME.exists():
        raise SystemExit(f"Chrome not found: {CHROME}")
    SHOTS.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="phase1-cdp-"))
    server = subprocess.Popen(
        [str(Path("/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python")), "-m", "http.server", "8765", "--bind", "127.0.0.1"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    chrome = subprocess.Popen(
        [
            str(CHROME),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-crash-reporter",
            "--disable-extensions",
            "--disable-sync",
            "--no-first-run",
            "--no-default-browser-check",
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_debugger()
        asyncio.run(capture_all())
    finally:
        chrome.terminate()
        server.terminate()
        try:
            chrome.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chrome.kill()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
