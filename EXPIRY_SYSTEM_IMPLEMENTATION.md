# Dynamic Food Expiry & Donation Management System

## Overview
This document outlines the implementation of a dynamic donation management system that:
1. Tracks food **cooked_at** and **expiry_at** times
2. Calculates remaining time until expiration
3. Dynamically renders requests to appropriate NGOs based on remaining time
4. Implements progressive radius expansion if donations are not accepted
5. Auto-deletes expired donations from NGO dashboard with restaurant notifications

---

## System Architecture

### Key Components

#### 1. **Time-Based Donation States**
```
State Flow:
┌─────────────┐      ┌────────────────┐      ┌──────────────┐      ┌─────────┐
│   Posted    │─────▶│ Awaiting NGO   │─────▶│ Accepted by  │─────▶│ Picked  │
│ (Fresh)     │      │ (With Radius)  │      │ NGO          │      │ Up      │
└─────────────┘      └────────────────┘      └──────────────┘      └─────────┘
       ▲              (0-100%) time left                               │
       │                                                                │
       │            ┌──────────────────────────────────────────────────┘
       │            │
       └────────────┴─ Auto-delete if expiry approaches
                      & no acceptance
```

#### 2. **Time Window Categories**
- **SAFE**: > 2 hours remaining
- **EXPIRING_SOON**: 30 mins - 2 hours remaining
- **CRITICAL**: < 30 mins remaining → auto-delete + notify restaurant

#### 3. **Radius Expansion Logic**
```
Attempt 1: Initial radius (e.g., 5 km) - Notify NGOs
  ↓ (No acceptance after X mins)
Attempt 2: Expand radius (e.g., 10 km) - Notify new NGOs
  ↓ (No acceptance after X mins)
Attempt 3: Expand radius (e.g., 15 km) - Notify new NGOs
  ↓ (Critical time reached)
Auto-delete + Archive + Notify Restaurant
```

---

## Database Schema Enhancements

### 1. Update SurplusFoodRequest Model

```python
# In donations/models.py

from django.db import models
from django.utils import timezone
from datetime import timedelta
from django.contrib.gis.db import models as gis_models

class SurplusFoodRequest(models.Model):
    # ... existing fields ...
    
    # Time tracking
    cooked_at = models.DateTimeField(null=True, blank=True)
    expiry_at = models.DateTimeField(null=True, blank=True)
    posted_at = models.DateTimeField(auto_now_add=True)
    
    # Geolocation
    restaurant_lat = models.FloatField(null=True, blank=True)
    restaurant_lng = models.FloatField(null=True, blank=True)
    
    # Expiry management
    STATUS_CHOICES = (
        ('posted', 'Posted'),
        ('notifying', 'Notifying NGOs'),
        ('accepted', 'Accepted by NGO'),
        ('picked', 'Picked Up'),
        ('expired', 'Expired'),
        ('archived', 'Archived'),
    )
    donation_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='posted'
    )
    
    # Notification tracking
    current_radius_km = models.IntegerField(default=5)  # Current search radius
    ngos_notified_at = models.DateTimeField(null=True, blank=True)
    last_radius_expansion_at = models.DateTimeField(null=True, blank=True)
    notified_ngo_ids = models.JSONField(default=list)  # Track which NGOs were notified
    
    # Archival info
    expiry_reason = models.CharField(
        max_length=50,
        choices=(
            ('manual_delete', 'Manually Deleted by Restaurant'),
            ('auto_expired', 'Auto-Expired - No Acceptance'),
            ('picked_up', 'Successfully Picked Up'),
        ),
        null=True,
        blank=True
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ["-posted_at"]
        indexes = [
            models.Index(fields=['donation_status', 'expiry_at']),
            models.Index(fields=['restaurant', 'donation_status']),
        ]
    
    # ============ PROPERTIES & METHODS ============
    
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
        return self.urgency_level != "EXPIRED" and self.donation_status == 'posted'
    
    def mark_as_expired(self, reason='auto_expired'):
        """Archive donation as expired"""
        self.donation_status = 'archived'
        self.expiry_reason = reason
        self.archived_at = timezone.now()
        self.save(update_fields=['donation_status', 'expiry_reason', 'archived_at'])
    
    def __str__(self):
        return f"{self.restaurant.business_name} - {self.quantity} meals (Expires: {self.time_remaining_readable})"
```

### 2. Create DonationNotificationLog Model

```python
# In donations/models.py

class DonationNotificationLog(models.Model):
    """Tracks notification attempts for each donation"""
    
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
    
    NOTIFICATION_STATUS = (
        ('sent', 'SMS Sent'),
        ('pending', 'Awaiting Response'),
        ('accepted', 'Accepted'),
        ('rejected', 'Not Interested'),
        ('failed', 'Send Failed'),
    )
    
    status = models.CharField(
        max_length=20,
        choices=NOTIFICATION_STATUS,
        default='pending'
    )
    
    radius_km = models.IntegerField()  # Radius at which this NGO was notified
    notified_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    response_time_seconds = models.IntegerField(null=True, blank=True)
    
    sms_provider_response = models.JSONField(default=dict)  # Store raw SMS API response
    
    class Meta:
        ordering = ['-notified_at']
        indexes = [
            models.Index(fields=['donation', 'status']),
            models.Index(fields=['ngo', 'status']),
        ]
    
    def __str__(self):
        return f"Notification {self.donation.id} to {self.ngo.name} - {self.status}"
```

---

## Backend Logic - Services Layer

### 1. Create `donations/expiry_service.py`

```python
"""
Handles food expiry calculations, radius expansion, and deletion logic
"""

from datetime import timedelta
from django.utils import timezone
from django.contrib.gis.db.models import Q
from django.db.models import F
from math import radians, sin, cos, sqrt, atan2
import logging

from .models import (
    SurplusFoodRequest,
    NGOProfile,
    DonationNotificationLog,
    RestaurantProfile,
)
from .services import notify_specific_ngos_about_surplus

logger = logging.getLogger(__name__)

# Configuration (can move to settings.py)
RADIUS_EXPANSION_INTERVALS = [5, 10, 15, 20]  # km
TIME_BEFORE_RADIUS_EXPANSION = 15 * 60  # 15 mins in seconds
CRITICAL_EXPIRY_THRESHOLD = 30 * 60  # 30 mins


def calculate_distance(lat1, lng1, lat2, lng2):
    """Calculate distance between two coordinates in kilometers (Haversine)"""
    R = 6371  # Earth radius in km
    
    lat1_rad = radians(lat1)
    lng1_rad = radians(lng1)
    lat2_rad = radians(lat2)
    lng2_rad = radians(lng2)
    
    dlat = lat2_rad - lat1_rad
    dlng = lng2_rad - lng1_rad
    
    a = sin(dlat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlng / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    
    return R * c


def get_ngos_within_radius(restaurant_lat, restaurant_lng, radius_km, exclude_ngo_ids=None):
    """
    Get all NGOs within a given radius of restaurant.
    
    Args:
        restaurant_lat: Restaurant latitude
        restaurant_lng: Restaurant longitude
        radius_km: Search radius in kilometers
        exclude_ngo_ids: List of NGO IDs to exclude (already notified)
    
    Returns:
        List of NGOProfile objects
    """
    all_ngos = NGOProfile.objects.filter(
        phone__isnull=False
    ).exclude(phone__exact="")
    
    nearby_ngos = []
    
    for ngo in all_ngos:
        # Skip if no location data
        if not hasattr(ngo, 'current_lat') or not ngo.current_lat:
            # Fallback: check if in same city
            continue
        
        distance = calculate_distance(
            restaurant_lat, restaurant_lng,
            ngo.current_lat, ngo.current_lng
        )
        
        if distance <= radius_km:
            if exclude_ngo_ids and ngo.id in exclude_ngo_ids:
                continue
            nearby_ngos.append(ngo)
    
    return nearby_ngos


def get_new_ngos_in_expanded_radius(donation, new_radius_km):
    """Get NGOs in new radius that haven't been notified yet"""
    
    notified_ids = donation.notified_ngo_ids or []
    
    # Try distance-based first (if coordinates available)
    if donation.restaurant_lat and donation.restaurant_lng:
        new_ngos = get_ngos_within_radius(
            donation.restaurant_lat,
            donation.restaurant_lng,
            new_radius_km,
            exclude_ngo_ids=notified_ids
        )
    else:
        # Fallback to city-based if no coordinates
        new_ngos = list(NGOProfile.objects.filter(
            city__iexact=donation.restaurant.city,
            phone__isnull=False
        ).exclude(phone__exact="").exclude(id__in=notified_ids))
    
    return new_ngos


def expand_notification_radius(donation):
    """
    Expand the search radius and notify new NGOs.
    
    Returns:
        True if radius expanded and notifications sent
        False if max radius reached
    """
    current_radius_idx = RADIUS_EXPANSION_INTERVALS.index(donation.current_radius_km)
    
    if current_radius_idx >= len(RADIUS_EXPANSION_INTERVALS) - 1:
        # Already at max radius
        logger.info(f"Donation {donation.id} reached max radius. Will auto-delete if critical.")
        return False
    
    new_radius_km = RADIUS_EXPANSION_INTERVALS[current_radius_idx + 1]
    new_ngos = get_new_ngos_in_expanded_radius(donation, new_radius_km)
    
    if new_ngos:
        logger.info(f"Expanding donation {donation.id} from {donation.current_radius_km}km to {new_radius_km}km. Notifying {len(new_ngos)} new NGOs")
        
        # Notify new NGOs
        notify_specific_ngos_about_surplus(
            donation,
            new_ngos,
            radius_km=new_radius_km
        )
        
        # Update donation record
        donation.current_radius_km = new_radius_km
        donation.last_radius_expansion_at = timezone.now()
        
        # Add to notified list
        if donation.notified_ngo_ids is None:
            donation.notified_ngo_ids = []
        donation.notified_ngo_ids.extend([ngo.id for ngo in new_ngos])
        
        donation.save(update_fields=[
            'current_radius_km',
            'last_radius_expansion_at',
            'notified_ngo_ids'
        ])
        
        return True
    
    logger.info(f"No new NGOs found in expanded radius {new_radius_km}km for donation {donation.id}")
    return False


def should_delete_expired_donation(donation):
    """
    Determine if donation should be auto-deleted.
    
    Returns: True if should delete
    """
    # Only auto-delete if in critical state and not accepted
    if donation.donation_status in ['accepted', 'picked', 'archived']:
        return False
    
    if donation.urgency_level != 'CRITICAL':
        return False
    
    # Check if max radius was reached and enough time has passed
    if donation.current_radius_km >= RADIUS_EXPANSION_INTERVALS[-1]:
        # Max radius reached - delete if still critical
        return True
    
    return False


def process_donation_expiries():
    """
    Celery task / Scheduled task to:
    1. Check donations nearing expiry
    2. Expand radius if needed
    3. Auto-delete if expired and not accepted
    
    Should run every 5-10 minutes
    """
    now = timezone.now()
    
    # Find active donations
    active_donations = SurplusFoodRequest.objects.filter(
        donation_status__in=['posted', 'notifying']
    ).filter(
        expiry_at__isnull=False
    )
    
    for donation in active_donations:
        time_left = donation.time_remaining_seconds
        
        if time_left is None:
            continue
        
        # CASE 1: Donation expired
        if time_left <= 0:
            logger.info(f"Donation {donation.id} has expired. Auto-deleting.")
            donation.mark_as_expired(reason='auto_expired')
            _notify_restaurant_of_expiry(donation)
            continue
        
        # CASE 2: Critical time - consider deletion
        if donation.urgency_level == 'CRITICAL' and should_delete_expired_donation(donation):
            logger.warning(f"Donation {donation.id} critical and max radius reached. Auto-deleting.")
            donation.mark_as_expired(reason='auto_expired')
            _notify_restaurant_of_expiry(donation)
            continue
        
        # CASE 3: Time to expand radius?
        last_expansion = donation.last_radius_expansion_at
        if last_expansion is None:
            # First check - use ngos_notified_at
            check_time = donation.ngos_notified_at or donation.posted_at
        else:
            check_time = last_expansion
        
        time_since_last_action = (now - check_time).total_seconds()
        
        if (donation.urgency_level in ['EXPIRING_SOON', 'CRITICAL'] and
            time_since_last_action > TIME_BEFORE_RADIUS_EXPANSION and
            donation.current_radius_km < RADIUS_EXPANSION_INTERVALS[-1]):
            
            logger.info(f"Expanding radius for donation {donation.id}")
            expand_notification_radius(donation)


def _notify_restaurant_of_expiry(donation):
    """Send notification to restaurant about donation expiry"""
    # TODO: Implement SMS/Email notification to restaurant
    # Message: "Your {food_type} donation posted at {time} has expired. It was not accepted by any NGO in the area."
    logger.info(f"Would notify restaurant {donation.restaurant.id} about expiry of donation {donation.id}")


def get_donation_display_data(donation):
    """Prepare donation data for frontend display"""
    return {
        'id': donation.id,
        'food_type': donation.food_type,
        'quantity': donation.quantity,
        'cooked_at': donation.cooked_at.isoformat() if donation.cooked_at else None,
        'expiry_at': donation.expiry_at.isoformat() if donation.expiry_at else None,
        'time_remaining_seconds': donation.time_remaining_seconds,
        'time_remaining_readable': donation.time_remaining_readable,
        'percent_time_remaining': donation.percent_time_remaining,
        'urgency_level': donation.urgency_level,
        'urgency_color': donation.urgency_color,
        'can_accept': donation.can_be_accepted_now,
        'current_radius_km': donation.current_radius_km,
        'donation_status': donation.donation_status,
    }
```

### 2. Update `donations/services.py`

```python
# Add to existing services.py

def notify_specific_ngos_about_surplus(donation, ngos, radius_km=None):
    """
    Notify specific NGOs about a surplus donation.
    
    Args:
        donation: SurplusFoodRequest instance
        ngos: List of NGOProfile instances
        radius_km: Optional radius at which these NGOs are being notified
    
    Returns:
        List of notification results
    """
    if not ngos:
        return []
    
    message = build_surplus_sms_message(donation)
    template_data = build_surplus_sms_variables(donation)
    results = []
    
    for ngo in ngos:
        try:
            result = send_sms(ngo.phone, message, template_data=template_data)
            
            # Log notification
            from .models import DonationNotificationLog
            log_entry = DonationNotificationLog.objects.create(
                donation=donation,
                ngo=ngo,
                radius_km=radius_km or 5,
                status='sent' if result.get('status') == 'accepted' else 'failed',
                sms_provider_response=result,
            )
            
        except requests.RequestException:
            logger.exception(
                f"Failed to send SMS for donation {donation.id} to NGO {ngo.id}"
            )
            result = {"status": "failed", "reason": "request-error"}
        
        results.append({
            'ngo_id': ngo.id,
            'phone': ngo.phone,
            **result,
        })
    
    return results
```

---

## Celery Task Configuration

### Create `donations/tasks.py`

```python
"""
Celery tasks for background donation management
"""

from celery import shared_task
from django.utils import timezone
import logging

from .expiry_service import process_donation_expiries
from .models import SurplusFoodRequest

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def check_and_process_donation_expiries(self):
    """
    Run every 5 minutes to check for expired donations
    and expand search radius if needed.
    """
    try:
        process_donation_expiries()
        return "Expiry check completed"
    except Exception as exc:
        logger.error(f"Error in expiry check task: {exc}")
        # Retry after 1 minute
        raise self.retry(countdown=60, exc=exc)


@shared_task
def refresh_donation_urgency_badges():
    """
    Refresh urgency badges for all active donations.
    Useful for real-time frontend updates if using cached data.
    """
    donations = SurplusFoodRequest.objects.filter(
        donation_status__in=['posted', 'notifying']
    )
    count = 0
    for donation in donations:
        # This triggers property recalculation
        _ = donation.urgency_level
        count += 1
    return f"Refreshed {count} donations"
```

### Update `happytummy/settings.py`

```python
# Add Celery configuration
CELERY_BROKER_URL = 'redis://localhost:6379'
CELERY_RESULT_BACKEND = 'redis://localhost:6379'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'

# Celery Beat Schedule
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'check-donation-expiries': {
        'task': 'donations.tasks.check_and_process_donation_expiries',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
}
```

---

## Frontend Updates

### 1. Update NGO Dashboard Template

```html
<!-- templates/dashboard/ngo_dashboard.html - Add urgency indicators -->

{% for donation in new_donations %}
<div class="donation-card urgency-{{ donation.urgency_color }}">
    <div class="donation-header">
        <h5>{{ donation.food_type }} - {{ donation.quantity }} meals</h5>
        <span class="badge badge-{{ donation.urgency_color }}">
            {{ donation.urgency_level }}
        </span>
    </div>
    
    <div class="donation-time-info">
        <div class="time-bar">
            <!-- Progress bar showing time remaining -->
            <div class="time-progress" style="width: {{ donation.percent_time_remaining }}%">
            </div>
        </div>
        <p class="time-remaining">
            ⏱️ {{ donation.time_remaining_readable }} remaining
        </p>
        <small>
            Cooked: {{ donation.cooked_at|date:"H:i" }} | 
            Expires: {{ donation.expiry_at|date:"H:i" }}
        </small>
    </div>
    
    {% if donation.urgency_level == 'CRITICAL' %}
        <div class="alert alert-danger">
            ⚠️ This donation expires soon! Accept now if you can.
        </div>
    {% elif donation.urgency_level == 'EXPIRING_SOON' %}
        <div class="alert alert-warning">
            ⏰ Expiring soon! Accept within the next {{ donation.time_remaining_readable }}.
        </div>
    {% endif %}
    
    {% if donation.can_accept %}
        <form method="post">
            {% csrf_token %}
            <input type="hidden" name="action" value="accept_donation">
            <input type="hidden" name="donation_id" value="{{ donation.id }}">
            <button type="submit" class="btn btn-primary btn-sm">Accept This Donation</button>
        </form>
    {% else %}
        <button class="btn btn-secondary btn-sm" disabled>
            Cannot Accept - Expired
        </button>
    {% endif %}
</div>
{% endfor %}
```

### 2. Restaurant Dashboard - Notification Status

```html
<!-- templates/dashboard/restaurant_dashboard.html -->

{% for request in requests %}
<tr>
    <td>{{ request.food_type }}</td>
    <td>{{ request.quantity }}</td>
    <td>
        <span class="badge badge-{{ request.urgency_color }}">
            {{ request.urgency_level }}
        </span>
    </td>
    <td>{{ request.time_remaining_readable }}</td>
    <td>
        <div class="progress" style="height: 20px;">
            <div class="progress-bar" style="width: {{ request.percent_time_remaining }}%">
                {{ request.percent_time_remaining|floatformat:0 }}%
            </div>
        </div>
    </td>
    <td>
        <span class="badge badge-info">Radius: {{ request.current_radius_km }}km</span>
    </td>
    <td>{{ request.donation_status|upper }}</td>
    <td>
        {% if request.donation_status == 'archived' %}
            <span class="text-danger">
                {{ request.expiry_reason|title }}
            </span>
        {% elif request.can_delete %}
            <form method="post" style="display:inline;">
                {% csrf_token %}
                <input type="hidden" name="action" value="delete_donation">
                <input type="hidden" name="donation_id" value="{{ request.id }}">
                <button type="submit" class="btn btn-danger btn-sm">Delete</button>
            </form>
        {% endif %}
    </td>
</tr>
{% endfor %}
```

### 3. Add CSS for Urgency Indicators

```css
/* static/css/donation_urgency.css */

/* Urgency colors */
.urgency-green {
    border-left: 5px solid #28a745;
}

.urgency-orange {
    border-left: 5px solid #fd7e14;
}

.urgency-red {
    border-left: 5px solid #dc3545;
}

.urgency-dark-red {
    border-left: 5px solid #721c24;
}

/* Time progress bar */
.time-progress {
    height: 100%;
    background: linear-gradient(to right, #28a745, #fd7e14, #dc3545);
    border-radius: 4px;
    transition: width 1s linear;
}

.time-bar {
    height: 24px;
    background: #e9ecef;
    border-radius: 4px;
    overflow: hidden;
    margin-bottom: 10px;
}

.time-remaining {
    font-weight: bold;
    margin: 5px 0;
}

/* Badge variations */
.badge-green {
    background-color: #28a745;
    color: white;
}

.badge-orange {
    background-color: #fd7e14;
    color: white;
}

.badge-red {
    background-color: #dc3545;
    color: white;
}

.badge-dark-red {
    background-color: #721c24;
    color: white;
}
```

---

## View Updates

### Update `donations/dashboard_views.py`

```python
# Add to imports
from donations.expiry_service import (
    get_donation_display_data,
    process_donation_expiries,
)

# In restaurant_dashboard view
def restaurant_dashboard(request):
    # ... existing code ...
    
    for donation_request in recent_requests:
        # Use enhanced data
        donation_data = get_donation_display_data(donation_request)
        # Map properties to request object
        donation_request.time_remaining_readable = donation_data['time_remaining_readable']
        donation_request.percent_time_remaining = donation_data['percent_time_remaining']
        donation_request.urgency_level = donation_data['urgency_level']
        donation_request.urgency_color = donation_data['urgency_color']
        donation_request.current_radius_km = donation_data['current_radius_km']
        # ... rest of existing code ...
    
    return render(request, "dashboard/restaurant_dashboard.html", {...})


# In ngo_dashboard view
def ngo_dashboard(request):
    # ... existing code ...
    
    # Enhance new_donations with display data
    new_donations = SurplusFoodRequest.objects.filter(
        restaurant__city__iexact=profile.city,
        is_picked=False,
        donation_status='posted'
    ).select_related('restaurant')
    
    new_donations = [
        donation for donation in new_donations
        if donation.can_be_accepted_now
    ]
    
    # Add display data
    for donation in new_donations:
        donation_data = get_donation_display_data(donation)
        donation.time_remaining_readable = donation_data['time_remaining_readable']
        donation.percent_time_remaining = donation_data['percent_time_remaining']
        donation.urgency_level = donation_data['urgency_level']
        donation.urgency_color = donation_data['urgency_color']
    
    return render(request, "dashboard/ngo_dashboard.html", {...})
```

---

## API Endpoints for Real-Time Updates

### Create `donations/views_expiry_api.py`

```python
"""
Real-time donation status and expiry APIs
"""

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.contrib.auth.decorators import login_required
from donations.models import SurplusFoodRequest
from donations.expiry_service import get_donation_display_data

@login_required
@require_GET
def donation_urgency_status(request, donation_id):
    """Get current urgency status of a donation"""
    try:
        donation = SurplusFoodRequest.objects.get(id=donation_id)
        data = get_donation_display_data(donation)
        return JsonResponse({
            'success': True,
            'data': data
        })
    except SurplusFoodRequest.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Donation not found'
        }, status=404)

@login_required
@require_GET
def all_active_donations_status(request):
    """Get status of all active donations (for restaurant)"""
    try:
        from donations.models import RestaurantProfile
        profile = RestaurantProfile.objects.get(user=request.user)
    except:
        return JsonResponse({'success': False, 'error': 'Not a restaurant'}, status=403)
    
    donations = SurplusFoodRequest.objects.filter(
        restaurant=profile,
        donation_status__in=['posted', 'notifying']
    )
    
    return JsonResponse({
        'success': True,
        'donations': [
            get_donation_display_data(d) for d in donations
        ]
    })
```

### Update `donations/urls.py`

```python
# Add to existing URLs
from donations.views_expiry_api import (
    donation_urgency_status,
    all_active_donations_status,
)

urlpatterns += [
    path('api/donation/<int:donation_id>/urgency/', donation_urgency_status, name='donation_urgency_api'),
    path('api/restaurant/donations/status/', all_active_donations_status, name='restaurant_donations_status_api'),
]
```

---

## Database Migrations

```python
# donations/migrations/0002_add_expiry_system.py

from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):

    dependencies = [
        ('donations', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='surplusfoodrequest',
            name='restaurant_lat',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='surplusfoodrequest',
            name='restaurant_lng',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='surplusfoodrequest',
            name='donation_status',
            field=models.CharField(choices=[('posted', 'Posted'), ('notifying', 'Notifying NGOs'), ('accepted', 'Accepted by NGO'), ('picked', 'Picked Up'), ('expired', 'Expired'), ('archived', 'Archived')], default='posted', max_length=20),
        ),
        migrations.AddField(
            model_name='surplusfoodrequest',
            name='current_radius_km',
            field=models.IntegerField(default=5),
        ),
        migrations.AddField(
            model_name='surplusfoodrequest',
            name='ngos_notified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='surplusfoodrequest',
            name='last_radius_expansion_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='surplusfoodrequest',
            name='notified_ngo_ids',
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name='surplusfoodrequest',
            name='expiry_reason',
            field=models.CharField(blank=True, choices=[('manual_delete', 'Manually Deleted by Restaurant'), ('auto_expired', 'Auto-Expired - No Acceptance'), ('picked_up', 'Successfully Picked Up')], max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='surplusfoodrequest',
            name='archived_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='surplusfoodrequest',
            name='posted_at',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.CreateModel(
            name='DonationNotificationLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='django.db.models.BigAutoField')),
                ('status', models.CharField(choices=[('sent', 'SMS Sent'), ('pending', 'Awaiting Response'), ('accepted', 'Accepted'), ('rejected', 'Not Interested'), ('failed', 'Send Failed')], default='pending', max_length=20)),
                ('radius_km', models.IntegerField()),
                ('notified_at', models.DateTimeField(auto_now_add=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('response_time_seconds', models.IntegerField(blank=True, null=True)),
                ('sms_provider_response', models.JSONField(default=dict)),
                ('donation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notification_logs', to='donations.surplusfoodrequest')),
                ('ngo', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='donations.ngoprofile')),
            ],
        ),
        migrations.AddIndex(
            model_name='surplusfoodrequest',
            index=models.Index(fields=['donation_status', 'expiry_at'], name='donations_s_donat_idx'),
        ),
        migrations.AddIndex(
            model_name='donationnotificationlog',
            index=models.Index(fields=['donation', 'status'], name='donations_d_donat_idx'),
        ),
    ]
```

---

## Testing Strategy

### Unit Tests

```python
# donations/tests_expiry.py

from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from donations.models import (
    SurplusFoodRequest,
    RestaurantProfile,
    NGOProfile,
)
from donations.expiry_service import (
    should_delete_expired_donation,
    calculate_distance,
)

class ExpirySystemTest(TestCase):
    
    def setUp(self):
        # Create test restaurant and NGO
        pass
    
    def test_time_remaining_calculation(self):
        """Test time remaining property"""
        now = timezone.now()
        donation = SurplusFoodRequest(
            cooked_at=now,
            expiry_at=now + timedelta(hours=2)
        )
        self.assertAlmostEqual(donation.time_remaining_seconds, 7200, delta=2)
    
    def test_urgency_levels(self):
        """Test urgency level determination"""
        now = timezone.now()
        
        # SAFE
        donation = SurplusFoodRequest(
            expiry_at=now + timedelta(hours=3)
        )
        self.assertEqual(donation.urgency_level, 'SAFE')
        
        # EXPIRING_SOON
        donation.expiry_at = now + timedelta(minutes=45)
        self.assertEqual(donation.urgency_level, 'EXPIRING_SOON')
        
        # CRITICAL
        donation.expiry_at = now + timedelta(minutes=15)
        self.assertEqual(donation.urgency_level, 'CRITICAL')
        
        # EXPIRED
        donation.expiry_at = now - timedelta(minutes=5)
        self.assertEqual(donation.urgency_level, 'EXPIRED')
    
    def test_haversine_distance(self):
        """Test distance calculation"""
        # Kolkata (22.5726, 88.3639) to Delhi (28.7041, 77.1025)
        distance = calculate_distance(22.5726, 88.3639, 28.7041, 77.1025)
        # Should be ~1400 km
        self.assertGreater(distance, 1300)
        self.assertLess(distance, 1500)
```

---

## Deployment Checklist

- [ ] Run migrations: `python manage.py migrate`
- [ ] Create Celery worker: `celery -A happytummy worker -l info`
- [ ] Create Celery beat scheduler: `celery -A happytummy beat -l info`
- [ ] Set up Redis for Celery broker
- [ ] Configure SMS provider credentials in `.env`
- [ ] Test expiry calculation with sample donations
- [ ] Test radius expansion manually
- [ ] Test auto-deletion of critical donations
- [ ] Monitor Celery logs for task execution
- [ ] Add monitoring/alerting for failed expirations

---

## Future Enhancements

1. **WebSocket Real-Time Updates**: Push urgency status changes to connected clients
2. **Machine Learning**: Predict NGO acceptance rates based on food type/time
3. **Geo-Fencing**: Auto-accept based on proximity if configured
4. **Analytics Dashboard**: Track expiry rates, acceptance rates by time window
5. **Donation Recovery**: Suggest bulk purchases/consumption to restaurants
6. **Multi-Language SMS**: Support regional SMS templates
7. **Payment Integration**: Incentivize NGOs for faster acceptance

