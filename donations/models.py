import uuid
from pathlib import Path

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone
from datetime import timedelta


def validate_gallery_image_size(file_obj):
    max_size_bytes = 5 * 1024 * 1024
    if file_obj.size > max_size_bytes:
        raise ValidationError("Image size must be 5 MB or smaller.")


def ngo_gallery_upload_to(instance, filename):
    extension = Path(filename).suffix.lower() or ".jpg"
    return f"ngo_gallery/{instance.ngo_id}/{uuid.uuid4().hex}{extension}"


def volunteer_delivery_proof_upload_to(instance, filename):
    extension = Path(filename).suffix.lower() or ".jpg"
    return f"volunteer_delivery_proofs/{instance.assigned_to_id or 'unassigned'}/{uuid.uuid4().hex}{extension}"

# NGO can request food from restaurants
class NGOFoodRequest(models.Model):
    ngo = models.ForeignKey('NGOProfile', on_delete=models.CASCADE)
    food_type = models.CharField(max_length=120)
    quantity = models.PositiveIntegerField()
    timestamp = models.DateTimeField(auto_now_add=True)
    fulfilled = models.BooleanField(default=False)
    accepted_by = models.ForeignKey('RestaurantProfile', null=True, blank=True, on_delete=models.SET_NULL, related_name='accepted_ngo_requests')

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.ngo.name} requests {self.quantity} {self.food_type}"

# ===========================================
# USER PROFILES & ROLES
# ===========================================

class RestaurantProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    business_name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, unique=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    district = models.CharField(max_length=100, blank=True, null=True)
    city = models.CharField(max_length=100)  # keep
    pincode = models.CharField(max_length=10, blank=True, null=True)
    address = models.CharField(max_length=255)

    def __str__(self):
        return self.business_name


class VolunteerProfile(models.Model):
    VERIFICATION_STATUS_PENDING = "pending"
    VERIFICATION_STATUS_VERIFIED = "verified"
    VERIFICATION_STATUS_REJECTED = "rejected"
    VERIFICATION_STATUS_CHOICES = (
        (VERIFICATION_STATUS_PENDING, "Pending"),
        (VERIFICATION_STATUS_VERIFIED, "Verified"),
        (VERIFICATION_STATUS_REJECTED, "Rejected"),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, unique=True)
    area = models.CharField(max_length=150)
    is_available = models.BooleanField(default=True)
    trust_score = models.IntegerField(default=100)
    successful_deliveries = models.PositiveIntegerField(default=0)
    failed_deliveries = models.PositiveIntegerField(default=0)
    suspicious_deliveries = models.PositiveIntegerField(default=0)
    ngo_confirmations = models.PositiveIntegerField(default=0)
    flag_count = models.PositiveIntegerField(default=0)
    is_blocked = models.BooleanField(default=False)
    blocked_at = models.DateTimeField(null=True, blank=True)
    current_lat = models.FloatField(null=True, blank=True)
    current_lng = models.FloatField(null=True, blank=True)
    location_updated_at = models.DateTimeField(null=True, blank=True)
    profile_photo = models.ImageField(upload_to='volunteer_photos/', blank=True, null=True)
    aadhar_card = models.CharField(max_length=12, unique=True)
    aadhar_verified = models.BooleanField(default=False)
    verification_status = models.CharField(
        max_length=20,
        choices=VERIFICATION_STATUS_CHOICES,
        default=VERIFICATION_STATUS_PENDING,
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.full_name

    def block(self, blocked_at=None):
        blocked_at = blocked_at or timezone.now()
        self.is_blocked = True
        self.is_available = False
        self.blocked_at = blocked_at
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.save(update_fields=["is_blocked", "is_available", "blocked_at"])

    @property
    def masked_aadhar(self):
        if not self.aadhar_card:
            return ""
        return f"XXXXXXXX{self.aadhar_card[-4:]}"

    @property
    def trust_status_label(self):
        if self.is_blocked:
            return "Account Suspended"
        if self.trust_score >= 80:
            return "Trusted Volunteer"
        if self.trust_score >= 60:
            return "Volunteer Under Observation"
        if self.trust_score >= 40:
            return "Limited Delivery Access"
        return "Highly Restricted Access"


class NGOProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, unique=True)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    current_lat = models.FloatField(null=True, blank=True)
    current_lng = models.FloatField(null=True, blank=True)
    priority_score = models.IntegerField(default=0)
    email_verified = models.BooleanField(default=True)
    donation_notifications_enabled = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class NGOGalleryImage(models.Model):
    ngo = models.ForeignKey(
        NGOProfile,
        on_delete=models.CASCADE,
        related_name="gallery_images",
    )
    pickup_task = models.ForeignKey(
        "PickupTask",
        on_delete=models.CASCADE,
        related_name="distribution_proof_images",
        null=True,
        blank=True,
    )
    image = models.ImageField(
        upload_to=ngo_gallery_upload_to,
        validators=[
            FileExtensionValidator(
                allowed_extensions=["jpg", "jpeg", "png", "webp", "gif"]
            ),
            validate_gallery_image_size,
        ],
    )
    caption = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["ngo", "-created_at"]),
        ]

    def __str__(self):
        caption = self.caption or "Food distribution photo"
        return f"{self.ngo.name} - {caption}"


class UserRole(models.Model):
    ROLE_CHOICES = (
        ("restaurant", "Restaurant"),
        ("volunteer", "Volunteer"),
        ("ngo", "NGO"),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    def __str__(self):
        return f"{self.user.username} — {self.role}"


# ===========================================
# OPERATIONAL MODELS
# ===========================================

class SurplusFoodRequest(models.Model):
    STORAGE_CHOICES = (
        ("hot", "Hot"),
        ("cold", "Cold"),
        ("room_temp", "Room Temperature"),
    )

    STATUS_CHOICES = (
        ('posted', 'Posted'),
        ('notifying', 'Notifying NGOs'),
        ('accepted', 'Accepted by NGO'),
        ('picked', 'Picked Up'),
        ('expired', 'Expired'),
        ('archived', 'Archived'),
    )

    EXPIRY_REASON_CHOICES = (
        ('manual_delete', 'Manually Deleted by Restaurant'),
        ('auto_expired', 'Auto-Expired - No Acceptance'),
        ('picked_up', 'Successfully Picked Up'),
    )

    # link to RestaurantProfile, not old Restaurant
    restaurant = models.ForeignKey(RestaurantProfile, on_delete=models.CASCADE)
    food_type = models.CharField(max_length=120)
    quantity = models.PositiveIntegerField()
    timestamp = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(auto_now_add=True, null=True)
    cooked_at = models.DateTimeField(null=True, blank=True)
    expiry_at = models.DateTimeField(null=True, blank=True)
    storage_type = models.CharField(max_length=20, choices=STORAGE_CHOICES, default="room_temp")
    safety_notes = models.TextField(blank=True)
    is_picked = models.BooleanField(default=False)
    accepted_by_ngo = models.ForeignKey(
        'NGOProfile',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='accepted_surplus_donations',
    )
    
    # Geolocation
    restaurant_lat = models.FloatField(null=True, blank=True)
    restaurant_lng = models.FloatField(null=True, blank=True)
    
    # Expiry management
    donation_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='posted'
    )
    
    # Notification tracking
    current_radius_km = models.IntegerField(default=5)
    ngos_notified_at = models.DateTimeField(null=True, blank=True)
    last_radius_expansion_at = models.DateTimeField(null=True, blank=True)
    notified_ngo_ids = models.JSONField(default=list)
    notification_debug = models.JSONField(default=dict, blank=True)
    
    # Archival info
    expiry_reason = models.CharField(
        max_length=50,
        choices=EXPIRY_REASON_CHOICES,
        null=True,
        blank=True
    )
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=['donation_status', 'expiry_at']),
            models.Index(fields=['restaurant', 'donation_status']),
        ]

    def __str__(self):
        return f"{self.restaurant.business_name} - {self.quantity} meals"

    @property
    def time_remaining_seconds(self):
        """Returns seconds until expiry, or None if no expiry time set"""
        if not self.expiry_at:
            return None
        now = timezone.now()
        remaining = (self.expiry_at - now).total_seconds()
        return max(0, remaining)

    @property
    def time_remaining_readable(self):
        """Returns human-readable time (e.g., '45 mins', '2 hours 30 mins')"""
        seconds = self.time_remaining_seconds
        if seconds is None:
            return "Unknown"
        if seconds <= 0:
            return "Expired"
        
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        
        if hours > 0:
            return f"{hours}h {mins}m"
        else:
            return f"{mins}m"

    @property
    def percent_time_remaining(self):
        """Returns percentage of original time remaining (0-100)"""
        if not self.cooked_at or not self.expiry_at:
            return None
        
        total_duration = (self.expiry_at - self.cooked_at).total_seconds()
        time_left = self.time_remaining_seconds
        
        if total_duration <= 0:
            return 0
        
        percent = (time_left / total_duration) * 100
        return max(0, min(100, percent))

    @property
    def urgency_level(self):
        """Returns urgency: 'SAFE', 'EXPIRING_SOON', 'CRITICAL', 'EXPIRED'"""
        seconds_left = self.time_remaining_seconds
        
        if seconds_left is None:
            return "UNKNOWN"
        if seconds_left <= 0:
            return "EXPIRED"
        if seconds_left < 1800:  # < 30 mins
            return "CRITICAL"
        if seconds_left < 7200:  # < 2 hours
            return "EXPIRING_SOON"
        return "SAFE"

    @property
    def urgency_color(self):
        """Returns color code for UI display"""
        urgency_map = {
            'SAFE': 'green',
            'EXPIRING_SOON': 'orange',
            'CRITICAL': 'red',
            'EXPIRED': 'dark-red',
            'UNKNOWN': 'gray',
        }
        return urgency_map.get(self.urgency_level, 'gray')

    @property
    def can_be_accepted_now(self):
        """Checks if donation can still be accepted"""
        return self.urgency_level != "EXPIRED" and self.donation_status in ['posted', 'notifying']

    @property
    def is_radius_reevaluation_active(self):
        """Returns True while this donation should keep shrinking NGO visibility."""
        return (
            not self.is_picked
            and self.accepted_by_ngo_id is None
            and self.donation_status in ["posted", "notifying"]
        )

    @property
    def safety_status(self):
        if not self.expiry_at:
            return "Unknown"

        now = timezone.now()
        if self.expiry_at <= now:
            return "Expired"
        if self.expiry_at <= now + timedelta(hours=2):
            return "Expiring Soon"
        return "Safe"

    @property
    def safety_status_class(self):
        status = self.safety_status
        if status == "Safe":
            return "status-complete"
        if status == "Expiring Soon":
            return "status-pending"
        if status == "Expired":
            return "status-danger"
        return "status-neutral"

    @property
    def is_safe_to_accept(self):
        return self.safety_status != "Expired"

    def mark_as_expired(self, reason='auto_expired'):
        """Archive donation as expired"""
        self.donation_status = 'archived'
        self.expiry_reason = reason
        self.archived_at = timezone.now()
        self.save(update_fields=['donation_status', 'expiry_reason', 'archived_at'])


class DonationNotificationLog(models.Model):
    """Tracks notification attempts for each donation"""
    
    NOTIFICATION_STATUS = (
        ('sent', 'SMS Sent'),
        ('pending', 'Awaiting Response'),
        ('read', 'Read on Dashboard'),
        ('accepted', 'Accepted'),
        ('rejected', 'Not Interested'),
        ('withdrawn', 'Withdrawn - Out Of Radius'),
        ('expired', 'Donation Expired'),
        ('failed', 'Send Failed'),
    )

    EMAIL_STATUS_CHOICES = (
        ('not_sent', 'Not Sent'),
        ('sent', 'Email Sent'),
        ('skipped', 'Email Skipped'),
        ('failed', 'Email Failed'),
    )
    
    donation = models.ForeignKey(
        SurplusFoodRequest,
        on_delete=models.CASCADE,
        related_name='notification_logs'
    )
    ngo = models.ForeignKey(
        NGOProfile,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    
    status = models.CharField(
        max_length=20,
        choices=NOTIFICATION_STATUS,
        default='pending'
    )
    
    radius_km = models.IntegerField()
    distance_km = models.FloatField(null=True, blank=True)
    notified_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    response_time_seconds = models.IntegerField(null=True, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    
    sms_provider_response = models.JSONField(default=dict)
    email_status = models.CharField(
        max_length=20,
        choices=EMAIL_STATUS_CHOICES,
        default='not_sent',
    )
    email_sent_at = models.DateTimeField(null=True, blank=True)
    email_provider_response = models.JSONField(default=dict, blank=True)
    email_error = models.TextField(blank=True)
    debug_context = models.JSONField(default=dict, blank=True)
    
    class Meta:
        ordering = ['-notified_at']
        indexes = [
            models.Index(fields=['donation', 'status']),
            models.Index(fields=['ngo', 'status']),
            models.Index(fields=['ngo', 'is_active', 'is_read']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['donation', 'ngo'],
                name='unique_donation_notification_per_ngo',
            )
        ]
    
    def __str__(self):
        return f"Notification {self.donation.id} to {self.ngo.name if self.ngo else 'Unknown'} - {self.status}"


class PickupTask(models.Model):
    STATUS_ACCEPTED = "accepted"
    STATUS_IN_TRANSIT = "in_transit"
    STATUS_DELIVERED = "delivered"
    STATUS_FAILED = "failed"
    STATUS_SUSPICIOUS = "suspicious"
    STATUS_CHOICES = (
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_IN_TRANSIT, "In Transit"),
        (STATUS_DELIVERED, "Delivered"),
        (STATUS_FAILED, "Failed"),
        (STATUS_SUSPICIOUS, "Suspicious"),
    )
    TRANSPARENCY_STATUS_DELIVERED = "delivered"
    TRANSPARENCY_STATUS_AWAITING_PROOF = "awaiting_distribution_proof"
    TRANSPARENCY_STATUS_PROOF_UPLOADED = "proof_uploaded"
    TRANSPARENCY_STATUS_VERIFIED = "verified"
    TRANSPARENCY_STATUS_CHOICES = (
        (TRANSPARENCY_STATUS_DELIVERED, "Delivered"),
        (TRANSPARENCY_STATUS_AWAITING_PROOF, "Awaiting Distribution Proof"),
        (TRANSPARENCY_STATUS_PROOF_UPLOADED, "Proof Uploaded"),
        (TRANSPARENCY_STATUS_VERIFIED, "Verified"),
    )

    # For surplus food: request is SurplusFoodRequest, ngo_request is null
    # For NGO food request: ngo_request is NGOFoodRequest, request is null
    request = models.ForeignKey(SurplusFoodRequest, on_delete=models.CASCADE, null=True, blank=True)
    ngo_request = models.ForeignKey(NGOFoodRequest, on_delete=models.CASCADE, null=True, blank=True)
    assigned_to = models.ForeignKey(VolunteerProfile, on_delete=models.SET_NULL, null=True)
    assigned_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACCEPTED)
    delivery_otp = models.CharField(max_length=6, blank=True)
    otp_verified = models.BooleanField(default=False)
    delivered_to_ngo = models.BooleanField(default=False)
    delivered_at = models.DateTimeField(null=True, blank=True)
    ngo_confirmed_at = models.DateTimeField(null=True, blank=True)
    transparency_status = models.CharField(
        max_length=32,
        choices=TRANSPARENCY_STATUS_CHOICES,
        default=TRANSPARENCY_STATUS_DELIVERED,
    )
    delivery_proof_image = models.ImageField(
        upload_to=volunteer_delivery_proof_upload_to,
        validators=[
            FileExtensionValidator(
                allowed_extensions=["jpg", "jpeg", "png", "webp", "gif"]
            ),
            validate_gallery_image_size,
        ],
        null=True,
        blank=True,
    )
    proof_uploaded_at = models.DateTimeField(null=True, blank=True)
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    flagged_at = models.DateTimeField(null=True, blank=True)
    flag_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-assigned_at"]
        indexes = [
            models.Index(fields=["status", "assigned_at"]),
            models.Index(fields=["assigned_to", "status"]),
        ]

    def __str__(self):
        if self.request:
            return f"Pickup for: {self.request.restaurant.business_name} (Surplus)"
        elif self.ngo_request:
            return f"Pickup for: {self.ngo_request.accepted_by.business_name if self.ngo_request.accepted_by else 'Unassigned'} (NGO Request)"
        return "Pickup Task"

    @property
    def source_address(self):
        if self.request:
            return self.request.restaurant.address
        elif self.ngo_request and self.ngo_request.accepted_by:
            return self.ngo_request.accepted_by.address
        return "-"

    @property
    def destination_address(self):
        if self.request:
            # Surplus food always goes to the NGO that accepted
            return getattr(self.request.accepted_by_ngo, 'address', '-')
        elif self.ngo_request and self.ngo_request.ngo:
            return self.ngo_request.ngo.address
        return "-"


class VolunteerTrustFlag(models.Model):
    volunteer = models.ForeignKey(
        VolunteerProfile,
        on_delete=models.CASCADE,
        related_name="trust_flags",
    )
    pickup_task = models.OneToOneField(
        PickupTask,
        on_delete=models.CASCADE,
        related_name="trust_flag",
    )
    reason = models.CharField(max_length=255)
    flagged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-flagged_at"]
        indexes = [
            models.Index(fields=["volunteer", "flagged_at"]),
        ]

    def __str__(self):
        return f"{self.volunteer.full_name} - {self.reason}"


class VolunteerTrustScoreLog(models.Model):
    volunteer = models.ForeignKey(
        VolunteerProfile,
        on_delete=models.CASCADE,
        related_name="trust_score_logs",
    )
    pickup_task = models.ForeignKey(
        PickupTask,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trust_score_logs",
    )
    score_change = models.IntegerField()
    reason = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["volunteer", "created_at"]),
        ]

    def __str__(self):
        return f"{self.volunteer.full_name}: {self.score_change:+d} ({self.reason})"


class Donation(models.Model):
    restaurant_name = models.CharField(max_length=200)
    food_type = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField()
    city = models.CharField(max_length=120)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.restaurant_name} - {self.quantity} meals"
