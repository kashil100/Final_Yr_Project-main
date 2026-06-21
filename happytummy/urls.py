from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve

from donations import auth_views
from donations import dashboard_views
from donations import views_notification_api
from django.views.generic import TemplateView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", TemplateView.as_view(template_name="index.html"), name="home"),
    path("restaurant/login/", auth_views.restaurant_login, name="restaurant_login"),
    path("restaurant/register/", auth_views.restaurant_register, name="restaurant_register"),
    path("volunteer/login/", auth_views.volunteer_login, name="volunteer_login"),
    path("volunteer/register/", auth_views.volunteer_register, name="volunteer_register"),
    path("ngo/login/", auth_views.ngo_login, name="ngo_login"),
    path("ngo/register/", auth_views.ngo_register, name="ngo_register"),
    path("logout/", auth_views.logout_view, name="logout"),
    path("dashboard/", auth_views.dashboard_redirect, name="dashboard_redirect"),
    path("dashboard/restaurant/", dashboard_views.restaurant_dashboard, name="restaurant_dashboard"),
    path(
        "dashboard/restaurant/csr-certificate/",
        dashboard_views.restaurant_csr_certificate,
        name="restaurant_csr_certificate",
    ),
    path("dashboard/volunteer/", dashboard_views.volunteer_dashboard, name="volunteer_dashboard"),
    path("dashboard/ngo/", dashboard_views.ngo_dashboard, name="ngo_dashboard"),
    path("ngo/gallery/", dashboard_views.ngo_gallery, name="ngo_gallery"),
    path("api/volunteer/location/update/", dashboard_views.volunteer_location_update, name="volunteer_location_update"),
    path("api/ngo/live-volunteers/", dashboard_views.ngo_live_volunteer_locations, name="ngo_live_volunteer_locations"),
    path("api/ngo/notifications/", views_notification_api.ngo_notification_feed, name="ngo_notification_feed"),
    path("api/ngo/notifications/read/", views_notification_api.ngo_mark_notifications_read, name="ngo_mark_notifications_read"),
    path("api/restaurant/donations/status/", views_notification_api.restaurant_donation_status_feed, name="restaurant_donation_status_feed"),
    path("donations/", include("donations.urls")),
    path("dashboard/volunteer/certificate/", dashboard_views.volunteer_monthly_certificate, name="volunteer_monthly_certificate"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.SERVE_MEDIA:
    urlpatterns += [
        re_path(
            r"^media/(?P<path>.*)$",
            serve,
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
