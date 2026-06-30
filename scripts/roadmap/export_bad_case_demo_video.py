from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import websockets


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs" / "roadmap" / "phase1_video" / "bad-case-support-failure-demo.mp4"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PORT = 9228
FRAME_DIR_NAME = "bad-case-frames"
FPS = 30
WIDTH = 1180
HEIGHT = 740
DURATION_SECONDS = 10


class CdpPage:
    def __init__(self, websocket_url: str) -> None:
        self.websocket_url = websocket_url
        self.next_id = 0

    async def __aenter__(self) -> "CdpPage":
        self.websocket = await websockets.connect(self.websocket_url, max_size=32 * 1024 * 1024)
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


async def capture_frames(frame_dir: Path) -> None:
    url = "http://127.0.0.1:8765/docs/roadmap/phase1.html#showcase"
    async with CdpPage(new_page(url)) as page:
        await page.call("Page.enable")
        await page.call("Runtime.enable")
        await page.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": WIDTH,
                "height": HEIGHT,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        await asyncio.sleep(2.0)
        await page.call(
            "Runtime.evaluate",
            {
                "expression": """
                document.querySelectorAll('.reveal').forEach((node) => node.classList.add('visible'));
                document.documentElement.style.scrollBehavior = 'auto';
                const demo = document.getElementById('badCaseDemo');
                if (demo) demo.scrollIntoView({block: 'center'});
                """,
                "awaitPromise": False,
            },
        )
        await asyncio.sleep(0.8)
        await page.call(
            "Runtime.evaluate",
            {
                "expression": "document.getElementById('playBadCaseDemo')?.click();",
                "awaitPromise": False,
            },
        )

        total_frames = DURATION_SECONDS * FPS
        frame_interval = 1 / FPS
        started = time.monotonic()
        for frame_index in range(total_frames):
            target_time = started + (frame_index * frame_interval)
            now = time.monotonic()
            if target_time > now:
                await asyncio.sleep(target_time - now)
            screenshot = await page.call(
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "fromSurface": True,
                    "captureBeyondViewport": False,
                },
            )
            (frame_dir / f"frame-{frame_index:04d}.png").write_bytes(base64.b64decode(str(screenshot["data"])))


def encode_video(frame_dir: Path) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(FPS),
            "-i",
            str(frame_dir / "frame-%04d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(OUTPUT),
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"{OUTPUT}: {OUTPUT.stat().st_size} bytes")


def main() -> None:
    if not CHROME.exists():
        raise SystemExit(f"Chrome not found: {CHROME}")
    profile = Path(tempfile.mkdtemp(prefix="bad-case-cdp-profile-"))
    frame_root = Path(tempfile.mkdtemp(prefix=FRAME_DIR_NAME))
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8765", "--bind", "127.0.0.1"],
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
        asyncio.run(capture_frames(frame_root))
        encode_video(frame_root)
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
        shutil.rmtree(frame_root, ignore_errors=True)


if __name__ == "__main__":
    main()
