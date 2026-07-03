import asyncio
import logging
import os
import sys
from typing import Any
from dotenv import load_dotenv
load_dotenv()

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
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

REQUIRED_KEYS = {"exerciseTitle", "taskTitle", "userName"}

LOGIN_SELECTOR = "input[aria-label='Электронная почта']"
PASSWORD_SELECTOR = "input[aria-label='Пароль']"
SUBMIT_SELECTOR = "button.login-button"

PAGE_WAIT_SECONDS = 5
NEW_RESPONSES_SLICE = 20


def _load_credentials() -> tuple[str, str]:
    login = os.getenv("FIRPO_LOGIN", "admin")
    password = os.getenv("FIRPO_PASSWORD", "admin")
    if not login or not password:
        raise ValueError(
            "Environment variables FIRPO_LOGIN and FIRPO_PASSWORD must be set"
        )
    return login, password


def _describe_keys(data: object) -> str:
    if not isinstance(data, dict):
        return f"type={type(data).__name__} (not a dict)"
    present = set(data.keys())
    missing = REQUIRED_KEYS - present
    parts = [f"keys={len(present)}"]
    if missing:
        parts.append(f"missing={missing}")
    else:
        parts.append("ALL REQUIRED OK")
    return " | ".join(parts)


def _find_all_valid(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for i, item in enumerate(reversed(responses)):
        idx = len(responses) - 1 - i
        desc = _describe_keys(item)
        if isinstance(item, dict) and REQUIRED_KEYS.issubset(item.keys()):
            valid.append(item)
            logger.info(
                "  [#%d] VALID   — %s | exerciseTitle=%s  taskTitle=%s  userName=%s",
                idx, desc,
                item.get("exerciseTitle", "")[:40],
                item.get("taskTitle", "")[:40],
                item.get("userName", "")[:40],
            )
        else:
            present_keys = sorted(item.keys()) if isinstance(item, dict) else []
            logger.info(
                "  [#%d] SKIPPED — %s | present_keys=%s",
                idx, desc, present_keys[:20],
            )
    valid.reverse()
    return valid


async def _save_valid_records(
    records: list[dict[str, Any]],
) -> int:
    saved_count = 0
    valid_count = len(records)
    for idx, record_data in enumerate(records, 1):
        try:
            record = await sync_to_async(ExerciseRecord.from_api_response)(record_data)
            await sync_to_async(record.save)()
            saved_count += 1
            logger.info(
                "  [%d/%d] ✓ %s — %s (score=%s)",
                idx, valid_count,
                record.user_name,
                record.task_title,
                record.result_score,
            )
        except IntegrityError:
            logger.info(
                "  [%d/%d] – %s — %s (already exists, skipped)",
                idx, valid_count,
                record_data.get("userName", "?"),
                record_data.get("taskTitle", "?"),
            )
        except Exception as exc:
            logger.warning(
                "  [%d/%d] ✗ Failed to save record #%s: %s",
                idx, valid_count,
                record_data.get("id", "?"),
                exc,
            )
    return saved_count


async def _process_page(
    captured_responses: list[dict[str, Any]],
    page_num: int,
    slice_start: int = 0,
) -> int:
    total = len(captured_responses)
    new_responses = captured_responses[slice_start:]
    scan_pool = new_responses[-NEW_RESPONSES_SLICE:] if len(new_responses) > NEW_RESPONSES_SLICE else new_responses

    if not scan_pool:
        logger.info("  No new responses to scan on page %d", page_num)
        return 0

    logger.info(
        "  Scanning %d new response(s) (slice start=%d, total=%d)",
        len(scan_pool),
        slice_start,
        total,
    )

    valid_records = _find_all_valid(scan_pool)
    if not valid_records:
        logger.info("  No valid records found on page %d", page_num)
        return 0

    logger.info("─" * 70)
    logger.info("  Page %d: saving %d valid record(s)", page_num, len(valid_records))
    logger.info("─" * 60)
    saved = await _save_valid_records(valid_records)
    logger.info("  Page %d done: %d record(s) saved", page_num, saved)
    return saved


async def collect_exercises_data(headless: bool = True) -> int:
    login, password = _load_credentials()

    captured_responses: list[dict[str, Any]] = []

    async def _on_response(response: Response) -> None:
        if (
            response.request.method == "POST"
            and API_QUERY_URL in response.url
        ):
            try:
                body: Any = await response.json()
                if isinstance(body, list):
                    captured_responses.extend(body)
                    logger.info("  Captured list with %d records", len(body))
                elif isinstance(body, dict):
                    captured_responses.append(body)
                    logger.info("  Captured single dict response")
            except Exception as exc:
                logger.warning("  Failed to parse response JSON: %s", exc)

    def handle_response(response: Response) -> None:
        asyncio.ensure_future(_on_response(response))

    browser: Browser | None = None
    try:
        logger.info("─" * 60)
        logger.info("STEP 1/6: Launching browser")
        logger.info("─" * 60)
        async with async_playwright() as pw:
            logger.info("  Launching Chromium (headless=%s) ...", headless)
            browser = await pw.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            logger.info(
                "  Browser PID: %s",
                getattr(browser, "pid", "N/A") if browser else "N/A",
            )

            logger.info("  Creating new page (1920x1080) ...")
            page: Page = await browser.new_page(
                viewport={"width": 1920, "height": 1080}
            )
            page.on("response", handle_response)
            logger.info("  Response interceptor attached")

            logger.info("─" * 60)
            logger.info("STEP 2/6: Logging in")
            logger.info("─" * 60)
            logger.info("  Navigating to %s", LOGIN_URL)
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            logger.info("  Page loaded, waiting for login form ...")
            await page.wait_for_selector(LOGIN_SELECTOR, timeout=10000)
            logger.info("  Login form found, filling credentials ...")

            await page.fill(LOGIN_SELECTOR, login)
            logger.info("    login field filled")
            await page.fill(PASSWORD_SELECTOR, password)
            logger.info("    password field filled")

            logger.info("  Clicking submit button ...")
            await page.click(SUBMIT_SELECTOR)

            logger.info("  Waiting for redirect to /operator/courses ...")
            await page.wait_for_url("**/operator/courses**", timeout=30000)
            logger.info("  Login successful! Current URL: %s", page.url)

            logger.info("─" * 60)
            logger.info("STEP 3/6: Navigating to exercises page")
            logger.info("─" * 60)
            logger.info("  Going to %s", EXERCISES_URL)
            await page.goto(EXERCISES_URL, wait_until="networkidle", timeout=30000)
            logger.info("  Exercises page loaded. URL: %s", page.url)

            # ── Pagination loop ──
            logger.info("─" * 60)
            logger.info("STEP 4/6: Pagination loop")
            logger.info("─" * 60)

            total_saved: int = 0
            page_num: int = 0
            slice_start: int = 0

            while True:
                page_num += 1
                logger.info("─" * 60)
                logger.info("  PAGE %d", page_num)
                logger.info("─" * 60)

                logger.info(
                    "  Waiting %d seconds for AJAX requests ...",
                    PAGE_WAIT_SECONDS,
                )
                await asyncio.sleep(PAGE_WAIT_SECONDS)

                if page_num == 1 and not captured_responses:
                    raise RuntimeError(
                        f"No POST responses captured from {API_QUERY_URL}"
                    )

                saved = await _process_page(
                    captured_responses,
                    page_num,
                    slice_start,
                )
                total_saved += saved

                logger.info("  Looking for next-page button ...")

                next_button = None

                main_button = page.locator('button.q-btn[aria-label="Следующая страница"]')
                if await main_button.count() > 0:
                    next_button = main_button
                else:
                    for frame in page.frames:
                        frame_button = frame.locator('button.q-btn[aria-label="Следующая страница"]')
                        if await frame_button.count() > 0:
                            logger.info("  Found in frame: %s", frame.url)
                            next_button = frame_button
                            break

                if next_button:
                    is_disabled = await next_button.evaluate(
                        "el => el.classList.contains('disabled') || el.disabled"
                    )
                    has_next = not is_disabled
                else:
                    has_next = False

                if not has_next:
                    logger.info(
                        "  Next-page button not found – parsing complete "
                        "(%d page(s), %d record(s) saved)",
                        page_num,
                        total_saved,
                    )
                    break

                logger.info("  Clicking next-page button ...")
                await next_button.click()

                # Capture state NOW so any responses arriving after the click
                # but before the wait are included in the next batch
                slice_start = len(captured_responses)
                logger.info(
                    "  Click done, slice_start=%d, moving to page %d",
                    slice_start,
                    page_num + 1,
                )

            logger.info("─" * 60)
            logger.info(
                "  DONE! Total: %d page(s), %d record(s) saved",
                page_num,
                total_saved,
            )
            logger.info("─" * 60)
            return total_saved

    except Exception:
        logger.exception("✗ Exercise collection failed")
        raise
    finally:
        if browser is not None:
            logger.info("  Closing browser ...")
            await browser.close()
            logger.info("  Browser closed")
        logger.info("─" * 60)


class Command(BaseCommand):
    help = "Collect exercises JSON from edu.firpo.ru via Playwright"

    def add_arguments(self, parser):
        parser.add_argument(
            "--headless",
            action="store_true",
            default=True,
            help="Run browser in headless mode (default: True)",
        )
        parser.add_argument(
            "--no-headless",
            action="store_false",
            dest="headless",
            help="Run browser in visible mode (for debugging)",
        )

    def handle(self, *args, **options):
        try:
            saved_count = asyncio.run(collect_exercises_data(headless=options["headless"]))
            self.stdout.write(self.style.SUCCESS(f"Saved {saved_count} exercise record(s)"))
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            logger.exception("Exercise collection failed")
            raise CommandError(f"Unexpected error: {exc}") from exc
