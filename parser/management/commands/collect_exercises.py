import ast
import asyncio
import io
import json
import logging
import os
import re
import sys
from typing import Any
from dotenv import load_dotenv
load_dotenv()

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from playwright.async_api import async_playwright, Browser, Page, Response

from parser.models import ExerciseRecord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

BASE_URL = "https://edu.firpo.ru"
LOGIN_URL = f"{BASE_URL}/campus"
EXERCISES_URL = f"{BASE_URL}/campus/operator/exercises"
API_QUERY_URL = f"{BASE_URL}/api/query.php"

LOGIN_SELECTOR = "input[aria-label='Электронная почта']"
PASSWORD_SELECTOR = "input[aria-label='Пароль']"
SUBMIT_SELECTOR = "button.login-button"

PAGE_WAIT_SECONDS = 5

REQUIRED_KEYS = {"exerciseTitle", "taskTitle", "userName"}


def _load_credentials() -> tuple[str, str]:
    login = os.getenv("FIRPO_LOGIN")
    password = os.getenv("FIRPO_PASSWORD")
    if not login or not password:
        raise ValueError("FIRPO_LOGIN and FIRPO_PASSWORD must be set in .env")
    return login, password


def _is_exercise(item: dict[str, Any]) -> bool:
    return isinstance(item, dict) and REQUIRED_KEYS.issubset(item.keys())


def _extract_exercises(responses: list[Any]) -> list[dict[str, Any]]:
    """Pull every exercise record out of the given query.php response bodies."""
    out: list[dict[str, Any]] = []
    for body in responses:
        records = body if isinstance(body, list) else [body]
        if not isinstance(records, list):
            continue
        for rec in records:
            if _is_exercise(rec):
                out.append(rec)
    return out


def _parse_files_field(value: Any) -> list:
    """Parse API ``files`` field: JSON, Python repr (single quotes), or already a list."""
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, list):
            return parsed
    except (ValueError, SyntaxError, MemoryError, TypeError):
        pass
    return []


async def _save_records(records: list[dict[str, Any]]) -> list[ExerciseRecord]:
    """Save records via update_or_create and return the saved objects."""
    saved_objs: list[ExerciseRecord] = []
    for idx, data in enumerate(records, 1):
        try:
            record = await sync_to_async(ExerciseRecord.from_api_response)(data)
            field_names = [
                f.name for f in ExerciseRecord._meta.get_fields()
                if not f.auto_created
                and not f.primary_key
                and f.name not in ("id", "record_id", "created", "updated", "files")
            ]
            defaults = {f: getattr(record, f) for f in field_names}
            obj, created = await sync_to_async(
                ExerciseRecord.objects.update_or_create
            )(record_id=record.record_id, defaults=defaults)
            obj.raw_files = _parse_files_field(data.get("files"))  # attach raw API files for later upload
            saved_objs.append(obj)
            logger.info(
                "  [%d/%d] %s %s",
                idx, len(records),
                "✓ new" if created else "🔄 upd",
                record.user_name,
            )
        except Exception as exc:
            logger.warning("  [%d/%d] ✗ skip: %s", idx, len(records), exc)
    return saved_objs


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[^\w.\-]', '_', name, flags=re.UNICODE)
    name = re.sub(r'_+', '_', name).strip('_')
    return name or 'unknown'


def _minio_client():
    from minio import Minio
    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE,
    )


def _put_to_minio(object_name: str, content: bytes) -> None:
    client = _minio_client()
    bucket = settings.MINIO_BUCKET
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.put_object(bucket, object_name, io.BytesIO(content), len(content))


async def _download_file(url: str, cookies: dict[str, str]) -> bytes:
    async with httpx.AsyncClient(cookies=cookies, follow_redirects=True, timeout=60.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def _upload_records_files(page: Page, objs: list[ExerciseRecord]) -> None:
    """Download each file from the raw API urls and upload to MinIO.

    Updates the record's ``files`` field to a list of MinIO paths.
    """
    if not settings.MINIO_ACCESS_KEY or not settings.MINIO_ENDPOINT:
        logger.info("  MinIO not configured — skipping file upload")
        return

    logger.info("  📦 bucket=%s, endpoint=%s", settings.MINIO_BUCKET, settings.MINIO_ENDPOINT)

    cookies = {
        c["name"]: c["value"]
        for c in await page.context.cookies()
        if "edu.firpo.ru" in c["domain"]
    }

    total_files = sum(1 for obj in objs for e in (getattr(obj, "raw_files", []) or []) if isinstance(e, dict) and e.get("url"))
    total_records = sum(1 for obj in objs if getattr(obj, "raw_files", []))
    ok_count = 0
    fail_count = 0

    if not total_files:
        logger.info("  No files to upload for %d record(s)", len(objs))
        return

    logger.info("  %d file(s) across %d record(s)", total_files, total_records)

    for obj in objs:
        raw_files: Any = getattr(obj, "raw_files", [])
        if not raw_files or not isinstance(raw_files, list):
            continue

        new_paths: list[str] = []
        for entry in raw_files:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url", "")
            name = entry.get("name", "unknown")
            if not url or not isinstance(url, str):
                continue

            try:
                content = await _download_file(url, cookies)
                safe_name = _sanitize_filename(name)
                object_name = f"solutions/{obj.record_id}/{safe_name}"
                await asyncio.to_thread(_put_to_minio, object_name, content)
                new_paths.append(object_name)
                ok_count += 1
                logger.info("    ✅ %s  (%.1f KB) → %s", name, len(content) / 1024, object_name)
            except Exception as exc:
                fail_count += 1
                logger.warning("    ❌ %s — %s", name, exc)

        if new_paths:
            obj.files = new_paths
            await sync_to_async(obj.save)(update_fields=["files"])
            logger.info("    🗂  record %s: %d file(s) saved", obj.record_id, len(new_paths))

    logger.info("  📊 MinIO upload done: %d OK, %d failed", ok_count, fail_count)


async def _has_next_page(page: Page) -> bool:
    """Click the enabled '<i>chevron_right</i>' (exact target, like DevTools).

    The control may live inside an iframe, so every frame is scanned.
    """
    frames = page.frames
    logger.info("  Scanning %d frame(s) for <i>chevron_right</i> ...", len(frames))

    for fi, frame in enumerate(frames):
        try:
            diag = await frame.evaluate("""() => {
                const all = Array.from(document.querySelectorAll('i'));
                const withClass = all.filter(i =>
                    i.classList.contains('material-icons') || i.classList.contains('q-icon'));
                const chevronLike = all.filter(i => /chevron/i.test(i.textContent || ''));
                const full = all.filter(i => (i.textContent || '').trim() === 'chevron_right');
                return {
                    total_i: all.length,
                    with_class: withClass.length,
                    chevron_like: chevronLike.map(i => (i.textContent || '').trim()),
                    full_match: full.length,
                };
            }""")
        except Exception as exc:
            logger.info("    frame #%d: evaluate failed: %s", fi, exc)
            continue
        logger.info(
            "    frame #%d [%s]: total_i=%d with_class=%d chevron_like=%s full=%d",
            fi, (frame.url or "")[:50],
            diag["total_i"], diag["with_class"], diag["chevron_like"], diag["full_match"],
        )

    for fi, frame in enumerate(frames):
        try:
            handle = await frame.evaluate_handle("""() => {
                const icons = Array.from(document.querySelectorAll('i'));
                const icon = icons.find(i => (i.textContent || '').trim() === 'chevron_right');
                if (!icon) return null;
                const btn = icon.closest('button, a, [role="button"], .q-btn');
                if (btn && (btn.disabled || btn.classList.contains('disabled') ||
                            btn.getAttribute('aria-disabled') === 'true')) {
                    return null;
                }
                return icon;
            }""")
        except Exception:
            continue
        element = handle.as_element() if handle else None
        if element is not None:
            await element.evaluate("el => el.click()")
            logger.info("  Clicked <i>chevron_right</i> in frame #%d", fi)
            return True

    for fi, frame in enumerate(frames):
        try:
            locator = frame.locator('button.q-btn[aria-label="Следующая страница"]')
            if await locator.count() > 0 and not await locator.first.is_disabled():
                await locator.first.evaluate("el => el.click()")
                logger.info("  Clicked next-page (aria-label fallback) in frame #%d", fi)
                return True
        except Exception:
            continue

    logger.info("  No ENABLED <i>chevron_right</i> in any frame — assuming last page")
    return False


async def _wait_for_new_response(
    responses: list[Any], baseline: int, timeout: float = 15.0
) -> bool:
    """Block until a new query.php response arrives, or timeout."""
    step = 0.5
    elapsed = 0.0
    while len(responses) <= baseline and elapsed < timeout:
        await asyncio.sleep(step)
        elapsed += step
        if int(elapsed) % 2 == 0:
            logger.info(
                "    waiting for new response ... %.0fs (have %d, baseline %d)",
                elapsed, len(responses), baseline,
            )
    arrived = len(responses) > baseline
    logger.info(
        "  new response %s (total %d)",
        "arrived" if arrived else "NOT arrived (timeout)",
        len(responses),
    )
    return arrived


async def collect_exercises_data(headless: bool = True) -> int:
    login, password = _load_credentials()
    # Each entry = parsed JSON body of ONE query.php POST response (list or dict).
    query_responses: list[Any] = []

    async def _on_response(response: Response) -> None:
        if response.request.method == "POST" and API_QUERY_URL in response.url:
            try:
                body = await response.json()
                query_responses.append(body)
                idx = len(query_responses)
                if isinstance(body, list):
                    needed = sum(1 for r in body if _is_exercise(r))
                    logger.info("  [resp #%d] LIST  records=%d  exercise=%d", idx, len(body), needed)
                elif isinstance(body, dict):
                    needed = 1 if _is_exercise(body) else 0
                    logger.info("  [resp #%d] DICT  keys=%d  exercise=%d", idx, len(body.keys()), needed)
                else:
                    logger.info("  [resp #%d] OTHER type=%s", idx, type(body).__name__)
            except Exception as exc:
                logger.warning("  [resp] failed to parse JSON: %s", exc)

    def handle_response(response: Response) -> None:
        asyncio.ensure_future(_on_response(response))

    browser: Browser | None = None
    try:
        async with async_playwright() as pw:
            logger.info("Launching Chromium (headless=%s) ...", headless)
            browser = await pw.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            page: Page = await browser.new_page(viewport={"width": 1920, "height": 1080})
            page.on("response", handle_response)
            logger.info("Browser ready, response interceptor attached")

            logger.info("Login: navigating to %s", LOGIN_URL)
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_selector(LOGIN_SELECTOR, timeout=10000)
            logger.info("Login form present, filling credentials ...")
            await page.fill(LOGIN_SELECTOR, login)
            await page.fill(PASSWORD_SELECTOR, password)
            await page.click(SUBMIT_SELECTOR)
            await page.wait_for_url("**/operator/courses**", timeout=30000)
            logger.info("Login OK -> %s", page.url)

            logger.info("Open exercises: navigating to %s", EXERCISES_URL)
            await page.goto(EXERCISES_URL, wait_until="networkidle", timeout=30000)
            logger.info("Exercises page loaded -> %s", page.url)

            total_saved = 0
            page_num = 0
            slice_start = 0

            while True:
                page_num += 1
                logger.info("PAGE %d — waiting %ds for responses ...", page_num, PAGE_WAIT_SECONDS)
                await asyncio.sleep(PAGE_WAIT_SECONDS)

                new_responses = query_responses[slice_start:]
                records = _extract_exercises(new_responses)
                logger.info(
                    "  Page %d: scanned %d new response(s), exercise records=%d",
                    page_num, len(new_responses), len(records),
                )
                saved_objs = await _save_records(records)
                total_saved += len(saved_objs)
                logger.info("  Page %d saved %d record(s)", page_num, len(saved_objs))

                if saved_objs:
                    await _upload_records_files(page, saved_objs)

                if not await _has_next_page(page):
                    logger.info("No next page — done (%d page(s), %d saved)", page_num, total_saved)
                    break

                # Click happened inside _has_next_page; wait for next page data.
                slice_start = len(query_responses)
                logger.info("  slice_start=%d, waiting for page %d data ...", slice_start, page_num + 1)
                if not await _wait_for_new_response(query_responses, slice_start):
                    logger.info("No data after next-page click — done (%d saved)", total_saved)
                    break

            logger.info("TOTAL captured query.php responses: %d", len(query_responses))
            return total_saved

    except Exception:
        logger.exception("Collection failed")
        raise
    finally:
        if browser is not None:
            await browser.close()


class Command(BaseCommand):
    help = "Collect exercise records from edu.firpo.ru via Playwright"

    def add_arguments(self, parser):
        parser.add_argument("--no-headless", action="store_false", dest="headless",
                            help="Run browser in visible mode (for debugging)")
        parser.add_argument("--headless", action="store_true", dest="headless",
                            default=True, help="Run headless (default)")

    def handle(self, *args, **options):
        try:
            saved = asyncio.run(collect_exercises_data(headless=options["headless"]))
            self.stdout.write(self.style.SUCCESS(f"Saved {saved} record(s)"))
        except Exception as exc:
            raise CommandError(str(exc)) from exc
