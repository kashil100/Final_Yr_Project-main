from django.urls import path
from . import views
from .views_location_api import states, districts, talukas, villages

urlpatterns = [
    path("list/", views.donation_list, name="donation_list"),

    path("submit-restaurant/", views.submit_restaurant, name="submit_restaurant"),
    path("submit-ngo/", views.submit_ngo, name="submit_ngo"),
    path("submit-volunteer/", views.submit_volunteer, name="submit_volunteer"),
    path("submit-donation/", views.submit_donation, name="submit_donation"),  
    path("api/states/", states),
    path("api/districts/", districts),
    path("api/talukas/", talukas),
    path("api/villages/", villages),
    # Show HTML page
    path(
        "surplus/<int:restaurant_id>/",
        views.surplus_form_page,
        name="surplus_form_page"
    ),

    # Handle AJAX API (optional)
    path(
        "confirm-surplus/<int:restaurant_id>/",
        views.confirm_surplus,
        name="confirm_surplus"
    ),

]
