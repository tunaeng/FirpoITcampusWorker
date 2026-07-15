import asyncio
import logging
import os
import sys
from datetime import datetime
from typing import Any
from dotenv import load_dotenv
load_dotenv()

from django.core.management.base import BaseCommand, CommandError
from playwright.async_api import async_playwright, Browser, Page, Response

from parser.management.commands.collect_exercises import (
    _is_exercise,
    _load_credentials,
    API_QUERY_URL,
    EXERCISES_URL,
    LOGIN_SELECTOR,
    LOGIN_URL,
    PASSWORD_SELECTOR,
    REQUIRED_KEYS,
    SUBMIT_SELECTOR,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

TAKE_LAST = 10
PAGE_WAIT_SECONDS = 5


def _summarize_query(body: Any) -> dict[str, Any]:
    """Return a verdict + key info for ONE query.php response body."""
    if isinstance(body, dict):
        records = [body]
        shape = "DICT (single record)"
    elif isinstance(body, list):
        records = [r for r in body if isinstance(r, dict)]
        shape = f"LIST ({len(records)} record(s))"
    else:
        return {
            "shape": f"type={type(body).__name__}",
            "record_count": 0,
            "exercise": 0,
            "unknown": 0,
            "needed": False,
            "sample_keys": [],
        }

    exercise = sum(1 for r in records if _is_exercise(r))
    unknown = len(records) - exercise
    sample_keys = sorted(records[0].keys()) if records else []

    return {
        "shape": shape,
        "record_count": len(records),
        "exercise": exercise,
        "unknown": unknown,
        "needed": exercise > 0,
        "sample_keys": sample_keys,
    }


def generate_report(samples: list[tuple[int, Any]]) -> str:
    total = len(samples)
    needed = sum(1 for _, b in samples if _summarize_query(b)["needed"])
    not_needed = total - needed

    sep = "=" * 80
    lines: list[str] = []

    def w(line: str = "") -> None:
        lines.append(line)

    w(sep)
    w("  TEST: LAST 10 query.php REQUESTS — KEY REPORT")
    w(sep)
    w(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    w(f"  Source:    {API_QUERY_URL} (POST)")
    w(sep)
    w(f"  Exercise identifiers (needed):  {sorted(REQUIRED_KEYS)}")
    w(sep)
    w(f"  Queries taken:          {total}")
    w(f"  NEEDED (schema match):  {needed}")
    w(f"  NOT NEEDED (unknown):   {not_needed}")
    w(sep)
    w()

    for global_idx, body in samples:
        info = _summarize_query(body)
        marker = "✓" if info["needed"] else "✗"
        verdict = "NEEDED" if info["needed"] else "NOT NEEDED"

        w(f"  [{marker}] Query #{global_idx} | {info['shape']} | → {verdict}")
        w(f"       Records: exercise={info['exercise']}  unknown={info['unknown']}")
        if info["sample_keys"]:
            w(f"       Sample keys ({len(info['sample_keys'])}): {info['sample_keys']}")
        else:
            w("       Sample keys: (none)")
        w()

    w(sep)
    if not_needed > 0:
        w(f"  ⚠ {not_needed} of {total} query request(s) are NOT needed (no schema match in body)")
    else:
        w(f"  ✓ All {total} query request(s) are needed")
    w(sep)

    return "\n".join(lines)


async def probe_last10() -> int:
    login, password = _load_credentials()
    query_responses: list[Any] = []

    async def _on_response(response: Response) -> None:
        if (
            response.request.method == "POST"
            and API_QUERY_URL in response.url
        ):
            try:
                query_responses.append(await response.json())
            except Exception:
                pass

    def handle_response(response: Response) -> None:
        asyncio.ensure_future(_on_response(response))

    browser: Browser | None = None
    try:
        logger.info("Launching browser ...")
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            page: Page = await browser.new_page(viewport={"width": 1920, "height": 1080})
            page.on("response", handle_response)

            logger.info("Logging in ...")
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_selector(LOGIN_SELECTOR, timeout=10000)
            await page.fill(LOGIN_SELECTOR, login)
            await page.fill(PASSWORD_SELECTOR, password)
            await page.click(SUBMIT_SELECTOR)
            await page.wait_for_url("**/operator/courses**", timeout=30000)

            logger.info("Navigating to exercises (first page only) ...")
            await page.goto(EXERCISES_URL, wait_until="networkidle", timeout=30000)

            logger.info("Waiting %d seconds for responses ...", PAGE_WAIT_SECONDS)
            await asyncio.sleep(PAGE_WAIT_SECONDS)

            if not query_responses:
                logger.warning("No query.php responses captured!")
                return 0

            total = len(query_responses)
            last10 = query_responses[-TAKE_LAST:]
            start_global = total - len(last10)
            samples = [(start_global + i, b) for i, b in enumerate(last10)]

            logger.info(
                "Captured %d query.php response(s), analyzing last %d (#%d..#%d) ...",
                total, len(samples), start_global, total - 1,
            )

            report = generate_report(samples)

            out_dir = "php"
            os.makedirs(out_dir, exist_ok=True)
            filename = f"test_last10_{datetime.now():%d%m%y_%H%M}.txt"
            filepath = os.path.join(out_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(report)

            print()
            print("\n".join(report.splitlines()[-5:]))
            print()
            print(f"  Report saved: {filepath}")
            return len(samples)

    except Exception:
        logger.exception("Probe failed")
        raise
    finally:
        if browser is not None:
            await browser.close()


class Command(BaseCommand):
    help = "TEST: on first page, take last 10 query.php responses and report keys + whether needed"

    def handle(self, *args, **options):
        try:
            count = asyncio.run(probe_last10())
            self.stdout.write(self.style.SUCCESS(f"Analyzed {count} sample(s) from last 10 responses"))
        except Exception as exc:
            raise CommandError(str(exc)) from exc
