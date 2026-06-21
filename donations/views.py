from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, render, redirect
from django.db import IntegrityError
from django.contrib.auth.decorators import login_required
import requests

from .models import (
    RestaurantProfile,
    VolunteerProfile,
    NGOProfile,
    SurplusFoodRequest,
    PickupTask,
    Donation,
)

from .forms import (
    RestaurantForm,
    VolunteerForm,
    NGOForm,
    DonationForm,
    SurplusFoodRequestForm,
)
from .services import notify_nearby_ngos_about_surplus

# ==========================================================
# 1. HOMEPAGE – RECENT DONATIONS
# ==========================================================
def donation_list(request):
    qs = Donation.objects.all()[:15]
    data = [
        {
            "restaurant_name": d.restaurant_name,
            "food_type": d.food_type,
            "quantity": d.quantity,
            "city": d.city,
            "date": d.date.strftime("%d %b %Y"),
        }
        for d in qs
    ]
    return JsonResponse(data, safe=False)


# ==========================================================
# 2. REGISTRATION HANDLERS
# ==========================================================

@require_POST
def submit_restaurant(request):
    form = RestaurantForm(request.POST)
    if form.is_valid():
        try:
            form.save()
            return JsonResponse({"status": "success"})
        except IntegrityError:
            form.add_error(None, "This restaurant profile already exists.")
    return JsonResponse({"status": "error", "errors": form.errors}, status=400)


@require_POST
def submit_volunteer(request):
    form = VolunteerForm(request.POST)
    if form.is_valid():
        try:
            form.save()
            return JsonResponse({"status": "success"})
        except IntegrityError:
            form.add_error(None, "Volunteer already registered.")
    return JsonResponse({"status": "error", "errors": form.errors}, status=400)


@require_POST
def submit_ngo(request):
    form = NGOForm(request.POST)
    if form.is_valid():
        try:
            form.save()
            return JsonResponse({"status": "success"})
        except IntegrityError:
            form.add_error(None, "NGO already registered.")
    return JsonResponse({"status": "error", "errors": form.errors}, status=400)


# ==========================================================
# 3. OLD DONATION FORM (LEGACY)
# ==========================================================
@require_POST
def submit_donation(request):
    form = DonationForm(request.POST)
    if form.is_valid():
        form.save()
        return JsonResponse({"status": "success"})
    return JsonResponse({"status": "error", "errors": form.errors}, status=400)


# ==========================================================
# 4. SURPLUS FOOD CONFIRMATION WORKFLOW
# ==========================================================
def surplus_form_page(request, restaurant_id):
    restaurant = get_object_or_404(RestaurantProfile, id=restaurant_id)

    if request.method == "POST":
        form = SurplusFoodRequestForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.restaurant = restaurant
            obj.save()
            notify_nearby_ngos_about_surplus(obj)
            return render(request, "surplus_success.html", {"restaurant": restaurant})

    return render(request, "surplus_confirm.html", {"restaurant": restaurant})


@require_POST
def confirm_surplus(request, restaurant_id):
    restaurant = get_object_or_404(RestaurantProfile, id=restaurant_id)
    form = SurplusFoodRequestForm(request.POST)

    if form.is_valid():
        sr = form.save(commit=False)
        sr.restaurant = restaurant
        sr.save()
        notify_nearby_ngos_about_surplus(sr)
        return JsonResponse({"status": "success"})

    return JsonResponse({"status": "error", "errors": form.errors}, status=400)


@login_required(login_url="/")
def submit_surplus(request, profile_id):
    restaurant = get_object_or_404(RestaurantProfile, id=profile_id)

    if request.method == "POST":
        form = SurplusFoodRequestForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.restaurant = restaurant
            obj.save()
            notify_nearby_ngos_about_surplus(obj)
            return redirect("restaurant_dashboard")

    return redirect("restaurant_dashboard")


# ==========================================================
# 5. RESTAURANT DASHBOARD (MAIN)
# ==========================================================
@login_required(login_url="/")
def restaurant_dashboard(request):
    profile = RestaurantProfile.objects.get(user=request.user)

    # -------------------------------------------------
    # HANDLE POST REQUESTS (donation + profile update)
    # -------------------------------------------------
    if request.method == "POST":
        action = request.POST.get("action")

        # Add donation
        if action == "add_donation":
            donation = SurplusFoodRequest.objects.create(
                restaurant=profile,
                food_type=request.POST.get("food_type"),
                quantity=request.POST.get("quantity"),
            )
            notify_nearby_ngos_about_surplus(donation)
            return redirect("restaurant_dashboard")

        # Edit profile
        elif action == "update_profile":
            profile.business_name = request.POST.get("business_name")
            profile.contact_person = request.POST.get("contact_person")
            profile.phone = request.POST.get("phone")
            profile.city = request.POST.get("city")
            profile.address = request.POST.get("address")
            profile.save()
            return redirect("restaurant_dashboard")

    # Normalize common Srirampur spelling variants
    city_input = profile.city.strip().lower()

    if city_input in ["srirampur", "srerampur", "shrirampur"]:
        normalized_city = "Serampore"
    else:
        normalized_city = profile.city

    # Build final address string
    full_address = f"{profile.address}, {normalized_city}, Hooghly, West Bengal, India"

    print("FINAL ADDRESS USED FOR GEOCODING:", full_address)

    # -------------------------------------------------
    # GEOCODING DEBUG
    # -------------------------------------------------
    full_address = f"{profile.address}, {profile.city}, Hooghly, West Bengal, India"

    print("\n============================")
    print(" FULL ADDRESS SENT TO API: ")
    print(full_address)
    print("============================")

    lat, lng = None, None
    data = None

    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": full_address, "format": "json", "limit": 1}
        headers = {"User-Agent": "HappyTummy-App"}

        res = requests.get(url, params=params, headers=headers, timeout=5)
        data = res.json()

        print("RAW RESPONSE FROM API:", data)

        if data:
            lat = float(data[0]["lat"])
            lng = float(data[0]["lon"])
            print("✔ Parsed LAT/LNG:", lat, lng)
        else:
            print("❌ API returned an EMPTY list")

    except Exception as e:
        print("❌ GEOCODING ERROR:", e)

    # Fallback
    if not lat or not lng:
        print("⚠ Falling back to default coordinates")
        lat, lng = 22.5726, 88.3639

    # -------------------------------------------------
    # DASHBOARD DATA
    # -------------------------------------------------
    requests_qs = SurplusFoodRequest.objects.filter(restaurant=profile)

    total_donations = requests_qs.count()
    pending_pickups = requests_qs.filter(is_picked=False).count()
    completed_pickups = requests_qs.filter(is_picked=True).count()

    return render(request, "dashboard/restaurant_dashboard.html", {
        "profile": profile,
        "requests": requests_qs,
        "total_donations": total_donations,
        "pending_pickups": pending_pickups,
        "completed_pickups": completed_pickups,
        "lat": lat,
        "lng": lng,
    })


# ==========================================================
# 6. VOLUNTEER DASHBOARD
# ==========================================================
@login_required(login_url="/")
def volunteer_dashboard(request):
    profile = VolunteerProfile.objects.get(user=request.user)
    tasks = PickupTask.objects.filter(assigned_to=profile)

    return render(request, "dashboard/volunteer_dashboard.html", {
        "profile": profile,
        "tasks": tasks
    })


# ==========================================================
# 7. NGO DASHBOARD
# ==========================================================
@login_required(login_url="/")
def ngo_dashboard(request):
    profile = NGOProfile.objects.get(user=request.user)

    return render(request, "dashboard/ngo_dashboard.html", {
        "profile": profile
    })
