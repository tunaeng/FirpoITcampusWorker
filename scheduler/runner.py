import hashlib
import logging
import sys
from pathlib import Path

from django.core.management import call_command
from django.utils import timezone

from scheduler.models import Schedule, ScheduleLog

logger = logging.getLogger(__name__)

LOGS_DIR = Path("media") / "schedule_logs"


class Tee:
    def __init__(self, *files):
        self.files = files

    def write(self, text):
        for f in self.files:
            f.write(text)
            f.flush()

    def flush(self):
        for f in self.files:
            f.flush()


def ensure_logs_dir():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def finalize_log(tmp_path: Path) -> tuple[str, str]:
    content = tmp_path.read_bytes()
    sha256 = hashlib.sha256(content).hexdigest()
    final_path = LOGS_DIR / f"{sha256}.log"
    if not final_path.exists():
        tmp_path.rename(final_path)
    else:
        tmp_path.unlink(missing_ok=True)
    return sha256, str(final_path)


def write_log(log_path: Path, schedule: Schedule) -> None:
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"Расписание: {schedule.name} (ID={schedule.id})\n")
        f.write(f"Запуск: {timezone.now():%Y-%m-%d %H:%M:%S}\n\n")
        f.flush()

        tee = Tee(sys.stdout, f)
        old_stdout = sys.stdout
        sys.stdout = tee
        try:
            call_command("collect_exercises")
        except Exception as e:
            msg = f"\nОшибка: {e}\n"
            f.write(msg)
            sys.stdout = old_stdout
            print(msg.strip())
            logger.exception("Ошибка выполнения расписания %s", schedule.name)
        finally:
            sys.stdout = old_stdout

        finish = f"\nЗавершено: {timezone.now():%Y-%m-%d %H:%M:%S}\n"
        f.write(finish)
        print(finish.strip())


def run_schedule(schedule_id: int) -> None:
    ensure_logs_dir()
    sched = Schedule.objects.filter(id=schedule_id).first()
    if not sched:
        print(f"[Scheduler] Расписание ID={schedule_id} не найдено")
        return

    print(f"[Scheduler] Запуск расписания: {sched.name} (ID={sched.id})")
    log_entry = ScheduleLog.objects.create(schedule=sched, status="in_progress")
    tmp_path = LOGS_DIR / f"tmp_{log_entry.id}.log"
    log_entry.log_path = str(tmp_path)
    log_entry.save(update_fields=["log_path"])

    try:
        write_log(tmp_path, sched)
        log_hash, final_path = finalize_log(tmp_path)
        log_entry.log_hash = log_hash
        log_entry.log_path = final_path
        log_entry.status = "done"
        print(f"[Scheduler] Расписание {sched.name}: готово (лог: {final_path})")
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        log_entry.log_path = None
        log_entry.log_hash = None
        log_entry.status = "done"
        print(f"[Scheduler] Ошибка выполнения расписания {sched.name}: {e}")
        logger.exception("Ошибка выполнения расписания %s", sched.name)
    finally:
        log_entry.finished_at = timezone.now()
        log_entry.save(update_fields=["finished_at", "status", "log_hash", "log_path"])
