from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.db import transaction
from django.utils import timezone

from donations.models import (
    UserRole,
    RestaurantProfile,
    VolunteerProfile,
    NGOProfile
)
from donations.forms import VolunteerRegistrationForm
from donations.services import monitor_suspicious_deliveries, VOLUNTEER_FLAG_THRESHOLD

VOLUNTEER_SUSPENDED_MESSAGE = (
    "Your volunteer account has been suspended due to repeated failed deliveries."
)
VOLUNTEER_VERIFICATION_REQUIRED_MESSAGE = (
    "Your volunteer account is not Aadhaar verified. Please complete verification to continue."
)


def _get_volunteer_by_login_identifier(identifier):
    if "@" in identifier:
        return VolunteerProfile.objects.filter(user__email=identifier).select_related("user").first()
    return VolunteerProfile.objects.filter(user__username=identifier).select_related("user").first()

# ==========================================
# RESTAURANT REGISTER
# ==========================================
def restaurant_register(request):
    if request.method == "POST":
        u = request.POST["username"]
        e = request.POST["email"]
        p1 = request.POST["password1"]
        p2 = request.POST["password2"]

        if p1 != p2:
            return render(request, "auth/restaurant_register.html",
                          {"error": "Passwords do not match"})

        if User.objects.filter(username=u).exists():
            return render(request, "auth/restaurant_register.html",
                          {"error": "Username already taken"})

        if User.objects.filter(email=e).exists():
            return render(request, "auth/restaurant_register.html",
                          {"error": "Email already registered"})


        # Get restaurant profile fields from POST
        business_name = request.POST.get("business_name", "")
        contact_person = request.POST.get("contact_person", "")
        phone = request.POST.get("phone", "")
        city = request.POST.get("city", "")
        address = request.POST.get("address", "")

        user = User.objects.create_user(username=u, email=e, password=p1)
        UserRole.objects.create(user=user, role="restaurant")
        RestaurantProfile.objects.create(
            user=user,
            business_name=business_name,
            contact_person=contact_person,
            phone=phone,
            city=city,
            address=address
        )

        login(request, user)
        return redirect("/dashboard/")

    return render(request, "auth/restaurant_register.html")


# ==========================================
# RESTAURANT LOGIN
# ==========================================
def restaurant_login(request):
    if request.method == "POST":
        u = request.POST["username"]
        p = request.POST["password"]

        # Allow login with email or username
        if "@" in u:
            try:
                from django.contrib.auth.models import User
                user_obj = User.objects.get(email=u)
                username = user_obj.username
            except User.DoesNotExist:
                return render(request, "auth/restaurant_login.html", {"error": "Invalid username or password"})
        else:
            username = u

        user = authenticate(request, username=username, password=p)

        if not user:
            return render(request, "auth/restaurant_login.html",
                          {"error": "Invalid username or password"})

        # Verify role
        if not hasattr(user, "userrole") or user.userrole.role != "restaurant":
            return render(request, "auth/restaurant_login.html",
                          {"error": "This is not a restaurant account"})

        login(request, user)
        return redirect("/dashboard/restaurant/")

    return render(request, "auth/restaurant_login.html")

def volunteer_register(request):
    if request.method == "POST":
        form = VolunteerRegistrationForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, "auth/volunteer_register.html", {"form": form})

        cleaned = form.cleaned_data
        verification_result = cleaned["aadhaar_verification_result"]
        if not verification_result.is_valid:
            form.add_error(
                "aadhar_card",
                "Aadhaar verification failed. You cannot register as a volunteer until verification succeeds.",
            )
            return render(request, "auth/volunteer_register.html", {"form": form})

        with transaction.atomic():
            user = User.objects.create_user(
                username=cleaned["username"],
                email=cleaned["email"],
                password=cleaned["password1"],
                is_active=True,
            )

            # Assign ROLE
            UserRole.objects.create(user=user, role="volunteer")

            # Create volunteer profile only after Aadhaar verification succeeds
            VolunteerProfile.objects.create(
                user=user,
                full_name=cleaned["full_name"],
                phone=cleaned["phone"],
                area=f"{cleaned['address']}, {cleaned['city']}" if cleaned["city"] else cleaned["address"],
                profile_photo=cleaned["profile_photo"],
                aadhar_card=cleaned["aadhar_card"],
                aadhar_verified=True,
                verification_status=VolunteerProfile.VERIFICATION_STATUS_VERIFIED,
                verified_at=timezone.now(),
            )

        login(request, user)
        return redirect("/dashboard/")

    return render(request, "auth/volunteer_register.html", {"form": VolunteerRegistrationForm()})

def volunteer_login(request):
    if request.method == "POST":
        monitor_suspicious_deliveries()
        u = request.POST["username"]
        p = request.POST["password"]

        volunteer_profile = _get_volunteer_by_login_identifier(u)
        if volunteer_profile and volunteer_profile.is_blocked:
            if volunteer_profile.user.is_active:
                volunteer_profile.user.is_active = False
                volunteer_profile.user.save(update_fields=["is_active"])
            if volunteer_profile.blocked_at is None:
                volunteer_profile.blocked_at = timezone.now()
                volunteer_profile.save(update_fields=["blocked_at"])
            return render(request, "auth/volunteer_login.html", {"error": VOLUNTEER_SUSPENDED_MESSAGE})
        if volunteer_profile and (
            not getattr(volunteer_profile, "aadhar_verified", False)
            or getattr(
                volunteer_profile,
                "verification_status",
                VolunteerProfile.VERIFICATION_STATUS_PENDING,
            ) != VolunteerProfile.VERIFICATION_STATUS_VERIFIED
        ):
            if volunteer_profile.user.is_active:
                volunteer_profile.user.is_active = False
                volunteer_profile.user.save(update_fields=["is_active"])
            return render(request, "auth/volunteer_login.html", {"error": VOLUNTEER_VERIFICATION_REQUIRED_MESSAGE})

        user = authenticate(request, username=u, password=p)

        if not user:
            if volunteer_profile and volunteer_profile.is_blocked:
                return render(request, "auth/volunteer_login.html", {"error": VOLUNTEER_SUSPENDED_MESSAGE})
            return render(request, "auth/volunteer_login.html",
                          {"error": "Invalid username or password"})

        if not hasattr(user, "userrole") or user.userrole.role != "volunteer":
            return render(request, "auth/volunteer_login.html",
                          {"error": "This account is not a Volunteer account"})

        volunteer_profile = VolunteerProfile.objects.select_related("user").filter(user=user).first()
        if volunteer_profile and (volunteer_profile.is_blocked or volunteer_profile.flag_count >= VOLUNTEER_FLAG_THRESHOLD):
            if not volunteer_profile.is_blocked:
                volunteer_profile.block()
            return render(request, "auth/volunteer_login.html", {"error": VOLUNTEER_SUSPENDED_MESSAGE})
        if volunteer_profile and (
            not getattr(volunteer_profile, "aadhar_verified", False)
            or getattr(
                volunteer_profile,
                "verification_status",
                VolunteerProfile.VERIFICATION_STATUS_PENDING,
            ) != VolunteerProfile.VERIFICATION_STATUS_VERIFIED
        ):
            if volunteer_profile.user.is_active:
                volunteer_profile.user.is_active = False
                volunteer_profile.user.save(update_fields=["is_active"])
            return render(request, "auth/volunteer_login.html", {"error": VOLUNTEER_VERIFICATION_REQUIRED_MESSAGE})

        login(request, user)
        return redirect("/dashboard/")

    return render(request, "auth/volunteer_login.html")

def ngo_register(request):
    if request.method == "POST":
        u = request.POST["username"]
        e = request.POST["email"]
        p1 = request.POST["password1"]
        p2 = request.POST["password2"]

        if p1 != p2:
            return render(request, "auth/ngo_register.html",
                          {"error": "Passwords do not match"})

        if User.objects.filter(username=u).exists():
            return render(request, "auth/ngo_register.html",
                          {"error": "Username already taken"})

        if User.objects.filter(email=e).exists():
            return render(request, "auth/ngo_register.html",
                          {"error": "Email already registered"})


        name = request.POST.get("name", "")
        contact_person = request.POST.get("contact_person", "")
        phone = request.POST.get("phone", "")
        address = request.POST.get("address", "")
        city = request.POST.get("city", "")

        user = User.objects.create_user(username=u, email=e, password=p1)
        UserRole.objects.create(user=user, role="ngo")
        NGOProfile.objects.create(
            user=user,
            name=name,
            contact_person=contact_person,
            phone=phone,
            address=address,
            city=city
        )

        login(request, user)
        return redirect("/dashboard/")

    return render(request, "auth/ngo_register.html")

def ngo_login(request):
    if request.method == "POST":
        u = request.POST["username"]
        p = request.POST["password"]

        user = authenticate(request, username=u, password=p)

        if not user:
            return render(request, "auth/ngo_login.html",
                          {"error": "Invalid username or password"})

        if not hasattr(user, "userrole") or user.userrole.role != "ngo":
            return render(request, "auth/ngo_login.html",
                          {"error": "This account is not an NGO account"})

        login(request, user)
        return redirect("/dashboard/")

    return render(request, "auth/ngo_login.html")

# ==========================================
# DASHBOARD REDIRECTOR
# ==========================================
@login_required(login_url="/")   # if NOT logged in → redirect to homepage
def dashboard_redirect(request):
    """Send logged-in user to the correct dashboard based on role."""

    role = request.user.userrole.role  # get assigned role

    if role == "restaurant":
        return redirect("/dashboard/restaurant/")

    elif role == "volunteer":
        return redirect("/dashboard/volunteer/")

    elif role == "ngo":
        return redirect("/dashboard/ngo/")

    # fallback in case role missing
    return redirect("/")

def logout_view(request):
    logout(request)
    return redirect("/")
