from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required


# =======================
# HOME PAGE
# =======================
def home_view(request):
    return render(request, "index.html")


# =======================
# AUTH REQUIRED PAGE
# =======================
def auth_required_view(request):
    return render(request, "auth_required.html")


# =======================
# LOGIN PAGE
# =======================
def login_view(request):
    if request.method == "POST":
        user = authenticate(
            request,
            username=request.POST.get("username"),
            password=request.POST.get("password")
        )
        if user:
            login(request, user)
            return redirect("/dashboard/")
        else:
            return render(request, "login.html", {
                "error": "Invalid username or password"
            })

    return render(request, "login.html")


# =======================
# REGISTER PAGE
# =======================
def register_view(request):
    if request.method == "POST":
        username = request.POST["username"]
        email = request.POST["email"]
        p1 = request.POST["password1"]
        p2 = request.POST["password2"]

        # Password mismatch error
        if p1 != p2:
            return render(request, "register.html", {"error": "Passwords do not match."})

        # Username already taken
        if User.objects.filter(username=username).exists():
            return render(request, "register.html", {"error": "Username already taken."})

        # Email already registered
        if User.objects.filter(email=email).exists():
            return render(request, "register.html", {"error": "Email is already registered."})

        # Create user
        user = User.objects.create_user(
            username=username,
            email=email,
            password=p1
        )

        # ⭐ Auto-login immediately
        login(request, user)

        # Redirect to dashboard instantly
        return redirect("/dashboard/")

    return render(request, "register.html")

# =======================
# DASHBOARD PAGE
# =======================
@login_required(login_url="/auth-required/")
def dashboard_view(request):
    return render(request, "dashboard.html")