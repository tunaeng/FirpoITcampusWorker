import subprocess
import sys
from pathlib import Path

from django.contrib import admin
from django.utils.html import format_html

from scheduler.models import Schedule, ScheduleLog


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "interval_seconds", "is_active", "created_at", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name",)
    ordering = ("-created_at",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.is_active:
            manage_py = Path(__file__).resolve().parent.parent.parent / "manage.py"
            subprocess.Popen(
                [sys.executable, str(manage_py), "process_schedule", str(obj.id)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


@admin.register(ScheduleLog)
class ScheduleLogAdmin(admin.ModelAdmin):
    list_display = ("id", "schedule", "status", "started_at", "finished_at", "log_hash_short")
    list_filter = ("status", "schedule")
    ordering = ("-started_at",)
    readonly_fields = ("started_at", "finished_at", "status", "log_hash", "log_path", "schedule", "log_preview")

    @admin.display(description="SHA-256 лога")
    def log_hash_short(self, obj):
        return obj.log_hash[:16] + "…" if obj.log_hash else "—"

    @admin.display(description="Превью лога")
    def log_preview(self, obj):
        if not obj.log_path or not Path(obj.log_path).exists():
            return format_html("<pre>Лог ещё не создан</pre>")
        try:
            text = Path(obj.log_path).read_text(encoding="utf-8")
            return format_html("<pre style='max-height:400px;overflow:auto;background:#f5f5f5;padding:8px;'>{}</pre>", text[-5000:])
        except Exception:
            return format_html("<pre>Ошибка чтения лога</pre>")
