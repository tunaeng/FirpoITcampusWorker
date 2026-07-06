import datetime
import logging
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from scheduler.models import Schedule
from scheduler.runner import ensure_logs_dir, run_schedule

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5


class Command(BaseCommand):
    help = "Демон-планировщик сбора упражнений"

    def handle(self, *args, **options):
        timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
        self.stdout.write(f"[{timestamp}] Демон-планировщик запущен. Проверка расписаний каждые {POLL_INTERVAL}с")
        ensure_logs_dir()

        tick = 0
        while True:
            now = timezone.now()
            schedules = Schedule.objects.filter(is_active=True)
            active_count = len(schedules)

            tick += 1
            ts = now.strftime("%H:%M:%S")
            self.stdout.write(f"[{ts}] [tick={tick}] Проверка: {active_count} активных расписаний")

            for sched in schedules:
                last_log = sched.logs.order_by("-started_at").first()

                if last_log and last_log.started_at:
                    elapsed = (now - last_log.started_at).total_seconds()
                    remaining = sched.interval_seconds - elapsed
                    if elapsed < sched.interval_seconds:
                        self.stdout.write(
                            f"  └─ {sched.name}: пропуск (прошло {elapsed:.0f}с, "
                            f"осталось {remaining:.0f}с до следующего запуска)"
                        )
                        continue
                    self.stdout.write(
                        f"  └─ {sched.name}: прошло {elapsed:.0f}с (интервал {sched.interval_seconds}с) → запуск!"
                    )
                else:
                    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
                    self.stdout.write(
                        f"  └─ {sched.name}: первый запуск в {now_str}"
                    )

                run_schedule(sched.id)

            if active_count == 0:
                self.stdout.write(f"  Нет активных расписаний. Создайте расписание в /admin/ или дождитесь авто-создания после миграции.")

            self.stdout.write(f"  Ожидание {POLL_INTERVAL}с...")
            time.sleep(POLL_INTERVAL)
