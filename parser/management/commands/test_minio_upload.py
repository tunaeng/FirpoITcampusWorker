import json
import os
import re
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand
from minio import Minio

from parser.management.commands.collect_exercises import _parse_files_field
from parser.models import ExerciseRecord

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[^\w.\-]', '_', name, flags=re.UNICODE)
    name = re.sub(r'_+', '_', name).strip('_')
    return name or 'unknown'


def _minio_client():
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
    client.put_object(bucket, object_name, BytesIO(content), len(content))


class Command(BaseCommand):
    help = "Read a PHP-response JSON file, download files, upload to MinIO"

    def add_arguments(self, parser):
        parser.add_argument("json_path", type=str, help="Path to JSON file (e.g. php/2906261021.json)")
        parser.add_argument("--no-headless", action="store_true", help="Show browser window")

    def handle(self, *args, **options):
        import asyncio
        asyncio.run(self._run(options))

    async def _run(self, options):
        json_path = Path(options["json_path"])
        if not json_path.exists():
            self.stderr.write(f"File not found: {json_path}")
            sys.exit(1)

        with open(json_path, encoding="utf-8") as f:
            raw = json.load(f)

        records_data: list[dict[str, Any]] = raw if isinstance(raw, list) else [raw]
        self.stdout.write(f"Loaded {len(records_data)} record(s) from {json_path}")

        # ── Parse each record through the model ──────────────────────
        parsed: list[ExerciseRecord] = []
        for data in records_data:
            try:
                record = await sync_to_async(ExerciseRecord.from_api_response)(data)
                record.raw_files = _parse_files_field(data.get("files"))
                parsed.append(record)
                self.stdout.write(f"  📄 {record.user_name or '?'} — {len(record.raw_files)} file(s)")
            except Exception as exc:
                self.stdout.write(f"  ⚠ skip: {exc}")

        all_files = sum(len(r.raw_files) for r in parsed)
        if not all_files:
            self.stdout.write(self.style.WARNING("No files to upload — exiting."))
            return

        # ── Login for cookies ────────────────────────────────────────
        headless = not options.get("no_headless", False)
        self.stdout.write(f"Logging in (headless={headless}) ...")
        cookies: dict[str, str] = {}
        if async_playwright is not None:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=headless)
                page = await browser.new_page()
                await page.goto("https://edu.firpo.ru", wait_until="domcontentloaded")
                await page.fill("input[aria-label='Электронная почта']", os.getenv("FIRPO_LOGIN", ""))
                await page.fill("input[aria-label='Пароль']", os.getenv("FIRPO_PASSWORD", ""))
                async with page.expect_navigation(wait_until="domcontentloaded"):
                    await page.click("button[type='submit']")
                self.stdout.write("  ✅ Login OK")

                cookies = {
                    c["name"]: c["value"]
                    for c in await page.context.cookies()
                    if "edu.firpo.ru" in c["domain"]
                }
                self.stdout.write(f"  🍪 {len(cookies)} cookie(s) from edu.firpo.ru")

                # ── Download & upload ─────────────────────────────────
                ok_count = 0
                fail_count = 0
                self.stdout.write(f"\n📦 bucket={settings.MINIO_BUCKET}, endpoint={settings.MINIO_ENDPOINT}")
                for record in parsed:
                    for entry in record.raw_files:
                        url = entry.get("url", "")
                        name = entry.get("name", "unknown")
                        if not url:
                            continue
                        try:
                            async with httpx.AsyncClient(cookies=cookies, follow_redirects=True, timeout=60.0) as client:
                                resp = await client.get(url)
                                resp.raise_for_status()
                                content = resp.content

                            safe_name = _sanitize_filename(name)
                            object_name = f"solutions/{record.record_id}/{safe_name}"
                            await sync_to_async(_put_to_minio)(object_name, content)
                            ok_count += 1
                            self.stdout.write(
                                f"  ✅ {name}  ({len(content) / 1024:.1f} KB) → {object_name}"
                            )
                        except Exception as exc:
                            fail_count += 1
                            self.stdout.write(self.style.WARNING(f"  ❌ {name} — {exc}"))

                await browser.close()
                self.stdout.write(self.style.SUCCESS(
                    f"\n📊 MinIO upload done: {ok_count} OK, {fail_count} failed"
                ))
        else:
            self.stderr.write("playwright is not installed — cannot obtain cookies for file download")
