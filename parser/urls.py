from django.urls import path
from .views import DashboardDataView

urlpatterns = [
    path("", DashboardDataView.as_view(), name="dashboard_data"),
]
