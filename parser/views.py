import json

import pandas as pd
import plotly.express as px
import plotly.utils
from django.http import JsonResponse
from django.views import View

from .models import ExerciseRecord


class DashboardDataView(View):
    def get(self, request, *args, **kwargs):
        course_filter = request.GET.getlist("course_id")
        queryset = ExerciseRecord.objects.all()
        if course_filter:
            queryset = queryset.filter(course_id__in=course_filter)

        all_courses = list(
            queryset.values_list("course_id", flat=True).distinct()
        )

        data = list(
            queryset.values(
                "created_at", "stars", "passed", "checked", "approved", "course_id"
            )
        )

        charts: dict = {}
        if data:
            df = pd.DataFrame(data)

            fig_stars = px.pie(df, names="stars", title="Распределение оценок (Stars)")
            charts["stars_pie"] = json.loads(fig_stars.to_json())

            df["date"] = pd.to_datetime(df["created_at"]).dt.date
            df_timeline = df.groupby("date").size().reset_index(name="count")
            fig_line = px.line(
                df_timeline, x="date", y="count", title="Активность отправки решений"
            )
            charts["activity_line"] = json.loads(fig_line.to_json())

            status_counts = {
                "Проверено (checked)": int(df["checked"].sum()),
                "Успешно (passed)": int(df["passed"].sum()),
                "Одобрено (approved)": int(df["approved"].sum()),
            }
            df_status = pd.DataFrame(
                list(status_counts.items()), columns=["Статус", "Количество"]
            )
            fig_status = px.bar(
                df_status,
                x="Статус",
                y="Количество",
                title="Статистика по статусам проверки",
                color="Статус",
            )
            charts["status_bar"] = json.loads(fig_status.to_json())

        return JsonResponse({
            "courses": all_courses,
            "charts": charts,
        })
