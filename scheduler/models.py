from django.db import models


class Schedule(models.Model):
    name = models.CharField(max_length=256, verbose_name="Название")
    interval_seconds = models.PositiveIntegerField(
        verbose_name="Интервал (сек)", default=3600,
        help_text="Как часто запускать процесс (в секундах)"
    )
    is_active = models.BooleanField(default=True, verbose_name="Активно")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Изменён")

    class Meta:
        verbose_name = "Расписание"
        verbose_name_plural = "Расписания"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.name} (каждые {self.interval_seconds}с)"


class ScheduleLog(models.Model):
    schedule = models.ForeignKey(
        Schedule, on_delete=models.CASCADE,
        related_name="logs", verbose_name="Расписание"
    )
    started_at = models.DateTimeField(verbose_name="Начало", auto_now_add=True)
    finished_at = models.DateTimeField(verbose_name="Конец", null=True, blank=True)
    status = models.CharField(
        max_length=32, verbose_name="Статус",
        default="in_progress",
        choices=[("in_progress", "В прогрессе"), ("done", "Завершен")]
    )
    log_hash = models.CharField(max_length=64, verbose_name="SHA-256 лога", null=True, blank=True)
    log_path = models.CharField(max_length=1024, verbose_name="Путь к логу", null=True, blank=True)

    class Meta:
        verbose_name = "Лог расписания"
        verbose_name_plural = "Логи расписаний"
        ordering = ("-started_at",)

    def __str__(self):
        return f"[{self.status}] {self.schedule.name} @ {self.started_at:%Y-%m-%d %H:%M}"
