from django import forms
from django.contrib.auth.models import User

from .models import (
    RestaurantProfile,
    VolunteerProfile,
    NGOProfile,
    SurplusFoodRequest,
    PickupTask,
    Donation,
)
from .services.aadhaar_verification_service import AadhaarVerificationService

# ============ RESTAURANT PROFILE FORM ============
class RestaurantForm(forms.ModelForm):
    class Meta:
        model = RestaurantProfile
        fields = ["business_name", "contact_person", "phone", "city", "address"]


# ============ VOLUNTEER PROFILE FORM ============
class VolunteerForm(forms.ModelForm):
    class Meta:
        model = VolunteerProfile
        fields = ["full_name", "phone", "area"]


class VolunteerRegistrationForm(forms.Form):
    full_name = forms.CharField(max_length=150)
    age = forms.IntegerField(min_value=10, max_value=100)
    address = forms.CharField(max_length=255)
    city = forms.CharField(max_length=100)
    phone = forms.CharField(max_length=20)
    aadhaar_holder_name = forms.CharField(max_length=150)
    aadhar_card = forms.CharField(max_length=12, min_length=12)
    profile_photo = forms.ImageField(required=True)
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password1 = forms.CharField(widget=forms.PasswordInput())
    password2 = forms.CharField(widget=forms.PasswordInput())

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Username already taken")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Email already registered")
        return email

    def clean_aadhar_card(self):
        aadhar_card = "".join(filter(str.isdigit, self.cleaned_data["aadhar_card"]))
        if not AadhaarVerificationService.validate_aadhaar_number(aadhar_card):
            raise forms.ValidationError("Aadhaar number must contain exactly 12 digits.")
        if VolunteerProfile.objects.filter(aadhar_card=aadhar_card).exists():
            raise forms.ValidationError("This Aadhaar number is already registered.")
        return aadhar_card

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Passwords do not match")

        full_name = cleaned_data.get("full_name")
        aadhar_card = cleaned_data.get("aadhar_card")
        aadhaar_holder_name = cleaned_data.get("aadhaar_holder_name")
        if full_name and aadhar_card and aadhaar_holder_name:
            verification_result = AadhaarVerificationService.verify_registration_details(
                full_name=full_name,
                aadhaar_number=aadhar_card,
                aadhaar_holder_name=aadhaar_holder_name,
            )
            if not verification_result.is_valid:
                self.add_error("aadhaar_holder_name", verification_result.error_message)
            cleaned_data["aadhaar_verification_result"] = verification_result

        return cleaned_data


# ============ NGO PROFILE FORM ============
class NGOForm(forms.ModelForm):
    class Meta:
        model = NGOProfile
        fields = ["name", "contact_person", "phone", "address", "city"]


# ============ OLD DONATION FORM ============
class DonationForm(forms.ModelForm):
    class Meta:
        model = Donation
        fields = ["restaurant_name", "food_type", "quantity", "city"]


# ============ SURPLUS FOOD REQUEST FORM ============
class SurplusFoodRequestForm(forms.ModelForm):
    class Meta:
        model = SurplusFoodRequest
        fields = ["food_type", "quantity"]
