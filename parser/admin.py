from django.contrib import admin
from parser.models import ExerciseRecord

admin.site.site_header = "IT campus Administration"
admin.site.site_title = "IT campus Admin"
admin.site.index_title = "Добро пожаловать в панель управления IT campus"

@admin.register(ExerciseRecord)
class ExerciseRecordAdmin(admin.ModelAdmin):
    list_display = ("record_id", "user_name", "module_title", "status", "short_answer", "files")
    search_fields = ("record_id",)

    @admin.display(description="answer")
    def short_answer(self, obj):
        words = (obj.answer or "").split()
        if len(words) <= 30:
            return obj.answer
        return " ".join(words[:30]) + "…"