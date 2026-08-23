"""平台级 URL；业务 URL 将由对应纵向切片自行拥有。"""

from django.urls import include, path

from pms.platform import views

urlpatterns = [
    path("", include("pms.web.urls")),
    path("health/live", views.live, name="platform-live"),
    path("health/ready", views.ready, name="platform-ready"),
]
