#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless mobile UI QA for the SahilOps dashboard.

Checks the 390x844 Android-sized viewport for:
  * the guest generator and guild configuration cards,
  * 44px minimum touch targets,
  * zero horizontal overflow and card clipping,
  * the add-account modal opening/closing cleanly,
  * saving a guild target and rendering its badge/metadata,
  * a mocked generation job completing through the UI,
  * no console errors or uncaught page errors.

The dashboard is started as a subprocess with a throwaway SQLite database.
Garena calls are mocked with Playwright routes, so the test is deterministic
and never touches a live account.

    .venv/bin/pip install playwright
    .venv/bin/python tests/ui_qa_playwright.py --port 20335
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
WORK_DIR = Path(tempfile.mkdtemp(prefix="sahilops-ui-qa-"))
DB_PATH = WORK_DIR / "ui.db"
SERVER_LOG = WORK_DIR / "server.log"

SERVER_CODE = r"""
import asyncio
import os
import sys

os.environ["SAHILOPS_DB"] = sys.argv[2]
os.environ.setdefault("SAHILOPS_API_KEY", "SAHILOPS")

from core import storage
storage.init()

from dashboard.dashboard_server import start_web_dashboard

async def main():
    await start_web_dashboard(host="127.0.0.1", port=int(sys.argv[1]))
    await asyncio.Event().wait()

asyncio.run(main())
"""

CHECKS = []


def check(name: str, condition: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(condition), detail))
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not condition else ""))


def wait_for_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.15)
    return False


def browser_executable() -> str:
    override = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if override and os.path.exists(override):
        return override
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=20335)
    parser.add_argument("--keep-open", action="store_true",
                        help="leave the dashboard subprocess running for manual inspection")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. Run: .venv/bin/pip install playwright")
        return 2

    executable = browser_executable()
    if not executable:
        print("No Chromium executable found. Set PLAYWRIGHT_CHROMIUM_EXECUTABLE.")
        return 2

    env = os.environ.copy()
    env["SAHILOPS_DB"] = str(DB_PATH)
    env.setdefault("SAHILOPS_API_KEY", "SAHILOPS")
    log_handle = open(SERVER_LOG, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-c", SERVER_CODE, str(args.port), str(DB_PATH)],
        cwd=str(BASE_DIR),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        if not wait_for_port(args.port):
            print("Dashboard did not start. Server log:")
            print(SERVER_LOG.read_text(encoding="utf-8", errors="replace")[-4000:])
            return 1

        base = f"http://127.0.0.1:{args.port}"
        console_errors = []
        page_errors = []

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=executable,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                viewport={"width": 390, "height": 844},
                device_scale_factor=2,
                is_mobile=True,
                has_touch=True,
            )
            page = context.new_page()
            page.on("console", lambda msg: console_errors.append(msg.text)
                    if msg.type == "error" else None)
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))
            page.add_init_script("""
                window.__layoutShifts = [];
                try {
                    new PerformanceObserver((list) => {
                        for (const entry of list.getEntries()) {
                            if (!entry.hadRecentInput) window.__layoutShifts.push(entry.value);
                        }
                    }).observe({type: 'layout-shift', buffered: true});
                } catch (e) {}
            """)

            guild_state = {
                "guild_id": "",
                "region": "IND",
                "name": "SahilOps Elite",
                "level": 7,
                "members": 42,
                "xp": 987654,
                "online": 11,
            }

            def fulfill_json(route, payload, status=200):
                route.fulfill(
                    status=status,
                    content_type="application/json",
                    body=json.dumps(payload),
                )

            def handle_guild_config(route):
                if route.request.method == "GET":
                    fulfill_json(route, {
                        "status": "ok",
                        "guild_id": guild_state["guild_id"],
                        "region": guild_state["region"],
                        "logo_url": (f"/api/guild/badge/{guild_state['guild_id']}.svg"
                                     if guild_state["guild_id"] else None),
                    })
                    return
                body = route.request.post_data_json or {}
                guild_state["guild_id"] = str(body.get("guild_id") or "")
                guild_state["region"] = str(body.get("region") or "IND")
                fulfill_json(route, {
                    "status": "ok",
                    "guild_id": guild_state["guild_id"],
                    "region": guild_state["region"],
                    "logo_url": f"/api/guild/badge/{guild_state['guild_id']}.svg",
                })

            def handle_guild_info(route):
                guild_id = guild_state["guild_id"] or "3055517625"
                data = {
                    "guild_id": guild_id,
                    "clan_id": guild_id,
                    "name": guild_state["name"],
                    "clan_name": guild_state["name"],
                    "level": guild_state["level"],
                    "clan_level": guild_state["level"],
                    "member_count": guild_state["members"],
                    "current_members": guild_state["members"],
                    "members_online": guild_state["online"],
                    "region": guild_state["region"],
                    "xp": guild_state["xp"],
                    "Total guild xp": guild_state["xp"],
                    "old_xp": guild_state["xp"] - 500,
                    "gained_exp": 500,
                    "logo_url": f"/api/guild/badge/{guild_id}.svg",
                }
                fulfill_json(route, {
                    "status": "success",
                    "requested_region": guild_state["region"],
                    "queried_clan_id": guild_id,
                    "guild": data,
                    "api_response": {
                        "OPERATOR": "SahilOps",
                        "clan_basic_info": data,
                    },
                })

            def handle_generate(route):
                fulfill_json(route, {
                    "status": "started",
                    "job_id": "ui-job-1",
                    "count": 1,
                    "region": "IND",
                    "region_label": "IN",
                    "delay": 8.0,
                }, status=202)

            def handle_generate_status(route):
                fulfill_json(route, {
                    "job_id": "ui-job-1",
                    "status": "completed",
                    "count": 1,
                    "success": 1,
                    "failed": 0,
                    "rate_limited": 0,
                    "region": "IND",
                    "region_label": "IN",
                    "delay": 8.0,
                    "created": [{
                        "uid": "ui-generated-1",
                        "nickname": "SahilOpsUI",
                        "region": "IND",
                        "status": "active",
                    }],
                    "error": None,
                })

            page.route("**/api/guild/config", handle_guild_config)
            page.route("**/api/guildinfo*", handle_guild_info)
            page.route("**/api/accounts/generate/status*", handle_generate_status)
            page.route("**/api/accounts/generate", handle_generate)

            page.goto(base, wait_until="networkidle")
            page.wait_for_selector("#guild-config-card")
            page.wait_for_selector("#generator-card")

            check("ui: dashboard title renders", page.title() == "SahilOps Dashboard")
            check("ui: guild config card visible", page.is_visible("#guild-config-card"))
            check("ui: generator card visible", page.is_visible("#generator-card"))
            check("ui: guild input visible", page.is_visible("#guildIdInput"))
            check("ui: generator controls visible",
                  page.is_visible("#generatorRegionInput")
                  and page.is_visible("#generatorCountInput"))

            metrics = page.evaluate("""() => ({
                doc: document.documentElement.scrollWidth,
                body: document.body.scrollWidth,
                inner: window.innerWidth
            })""")
            check("ui: zero horizontal overflow at 390px",
                  metrics["doc"] <= metrics["inner"] + 1
                  and metrics["body"] <= metrics["inner"] + 1,
                  str(metrics))

            clipped = page.evaluate("""() => {
                return [...document.querySelectorAll('.top-navbar, .premium-card')]
                    .filter((el) => {
                        const r = el.getBoundingClientRect();
                        return r.left < -0.5 || r.right > window.innerWidth + 0.5;
                    })
                    .map((el) => el.id || el.className);
            }""")
            check("ui: cards stay inside the mobile viewport", not clipped, str(clipped))

            small_targets = page.evaluate("""() => {
                const ids = ['generateGuestBtn', 'saveGuildBtn', 'guildIdInput',
                             'guildRegionInput', 'generatorRegionInput', 'generatorCountInput'];
                return ids.map((id) => {
                    const el = document.getElementById(id);
                    return {id, height: el ? Math.round(el.getBoundingClientRect().height) : 0};
                }).filter((item) => item.height < 44);
            }""")
            check("ui: new controls are at least 44px tall", not small_targets, str(small_targets))

            page.wait_for_timeout(500)
            shift = page.evaluate("window.__layoutShifts.reduce((a, b) => a + b, 0)")
            check("ui: no significant initial layout shift", shift <= 0.1, f"{shift:.3f}")

            page.click("button:has-text('+ Add Account')")
            page.wait_for_selector("#addAccountModal", state="visible")
            modal_box = page.locator(".modal-card").bounding_box()
            check("ui: add-account modal opens inside viewport",
                  bool(modal_box) and modal_box["x"] >= 0
                  and modal_box["x"] + modal_box["width"] <= 390.5,
                  str(modal_box))
            page.click("button:has-text('Cancel')")
            page.wait_for_selector("#addAccountModal", state="hidden")
            check("ui: add-account modal closes cleanly", not page.is_visible("#addAccountModal"))

            page.fill("#guildIdInput", "3055517625")
            page.select_option("#guildRegionInput", "IND")
            page.click("#saveGuildBtn")
            page.wait_for_function(
                "document.getElementById('guild-config-msg').textContent.includes('Live guild metadata loaded')",
                timeout=5000,
            )
            check("ui: guild target saves from the card",
                  page.text_content("#guild-config-state").strip() == "SAVED")
            check("ui: guild name/level/member count render",
                  page.text_content("#info-clan-name").strip() == "SahilOps Elite"
                  and page.text_content("#info-clan-level").strip() == "7"
                  and page.text_content("#info-clan-members").strip() == "42")
            avatar_src = page.get_attribute("#guild-avatar", "src") or ""
            check("ui: guild badge/logo renders from backend",
                  "/api/guild/badge/3055517625.svg" in avatar_src, avatar_src)
            try:
                page.wait_for_function(
                    "() => { const img = document.getElementById('guild-avatar');"
                    " return img && img.complete && img.naturalWidth > 0; }",
                    timeout=5000,
                )
                badge_loaded = page.evaluate(
                    "document.getElementById('guild-avatar').naturalWidth") > 0
            except Exception:
                badge_loaded = False
            check("ui: guild badge SVG actually loads", badge_loaded)

            page.click("#generateGuestBtn")
            page.wait_for_function(
                "document.getElementById('generator-msg').textContent.includes('Generated 1/1')",
                timeout=5000,
            )
            check("ui: one-click generator reports completion",
                  page.text_content("#generator-state").strip() == "DONE")
            check("ui: generator button re-enables after job",
                  page.is_enabled("#generateGuestBtn"))

            after_metrics = page.evaluate("""() => ({
                doc: document.documentElement.scrollWidth,
                inner: window.innerWidth
            })""")
            check("ui: no overflow after interactions",
                  after_metrics["doc"] <= after_metrics["inner"] + 1,
                  str(after_metrics))

            screenshot = Path("/tmp/opencode/ui-qa-390x844.png")
            page.screenshot(path=str(screenshot), full_page=True)
            print(f"[info] screenshot: {screenshot}")

            check("ui: no browser console errors", not console_errors, str(console_errors[:3]))
            check("ui: no uncaught page errors", not page_errors, str(page_errors[:3]))

            context.close()
            browser.close()

        if args.keep_open:
            print(f"[info] dashboard left running at {base}")
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        log_handle.close()
        if proc.poll() is None and not args.keep_open:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    failed = [name for name, ok, _detail in CHECKS if not ok]
    print("-" * 64)
    print(f" {len(CHECKS) - len(failed)}/{len(CHECKS)} UI checks passed")
    if failed:
        for name in failed:
            print(f"   FAILED: {name}")
        return 1
    print(" UI QA OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
