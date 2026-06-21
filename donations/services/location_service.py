import logging
import math

import requests
from django.db import connection
from django.db.models import F, FloatField, ExpressionWrapper, Value
from django.db.models.functions import ACos, Cast, Cos, Greatest, Least, Radians, Sin

from donations.models import NGOProfile


logger = logging.getLogger(__name__)


class LocationService:
    EARTH_RADIUS_KM = 6371.0

    @classmethod
    def haversine_distance_km(cls, lat1, lng1, lat2, lng2):
        lat1_rad = math.radians(lat1)
        lng1_rad = math.radians(lng1)
        lat2_rad = math.radians(lat2)
        lng2_rad = math.radians(lng2)
        dlat = lat2_rad - lat1_rad
        dlng = lng2_rad - lng1_rad
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlng / 2) ** 2
        )
        return cls.EARTH_RADIUS_KM * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

    @classmethod
    def bounding_box(cls, lat, lng, radius_km):
        lat_delta = radius_km / 111.0
        cos_lat = max(math.cos(math.radians(lat)), 0.01)
        lng_delta = radius_km / (111.0 * cos_lat)
        return (
            lat - lat_delta,
            lat + lat_delta,
            lng - lng_delta,
            lng + lng_delta,
        )

    @classmethod
    def geocode_address(cls, *, address="", city="", state="", pincode="", country="India"):
        params = {
            "street": address,
            "city": city,
            "state": state,
            "postalcode": pincode,
            "country": country,
            "format": "json",
            "limit": 1,
        }
        try:
            response = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params=params,
                headers={"User-Agent": "HappyTummy-App"},
                timeout=8,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException:
            logger.exception("Geocoding failed for %s, %s", address, city)
            return None, None

        if not data:
            return None, None

        try:
            return float(data[0]["lat"]), float(data[0]["lon"])
        except (KeyError, TypeError, ValueError, IndexError):
            logger.warning("Unexpected geocoder payload for %s, %s: %s", address, city, data)
            return None, None

    @classmethod
    def sync_restaurant_coordinates(cls, restaurant_profile):
        lat, lng = cls.geocode_address(
            address=restaurant_profile.address,
            city=restaurant_profile.city,
            state=restaurant_profile.state or "",
            pincode=restaurant_profile.pincode or "",
        )
        return lat, lng

    @classmethod
    def sync_ngo_coordinates(cls, ngo_profile, persist=True):
        lat, lng = cls.geocode_address(
            address=ngo_profile.address,
            city=ngo_profile.city,
        )
        if persist and lat is not None and lng is not None:
            ngo_profile.current_lat = lat
            ngo_profile.current_lng = lng
            ngo_profile.save(update_fields=["current_lat", "current_lng"])
        return lat, lng

    @classmethod
    def _base_ngo_queryset(cls):
        return NGOProfile.objects.filter(
            phone__isnull=False,
            current_lat__isnull=False,
            current_lng__isnull=False,
        ).exclude(phone__exact="")

    @classmethod
    def _annotate_distance_postgres(cls, queryset, origin_lat, origin_lng):
        origin_lat_value = Value(float(origin_lat), output_field=FloatField())
        origin_lng_value = Value(float(origin_lng), output_field=FloatField())
        cosine_arg = (
            Cos(Radians(origin_lat_value)) * Cos(Radians(F("current_lat"))) *
            Cos(Radians(F("current_lng")) - Radians(origin_lng_value)) +
            Sin(Radians(origin_lat_value)) * Sin(Radians(F("current_lat")))
        )
        clamped = Least(Value(1.0), Greatest(Value(-1.0), Cast(cosine_arg, FloatField())))
        return queryset.annotate(
            distance_km=ExpressionWrapper(
                Value(cls.EARTH_RADIUS_KM) * ACos(clamped),
                output_field=FloatField(),
            )
        )

    @classmethod
    def get_nearby_ngos(cls, origin_lat, origin_lng, radius_km, *, exclude_ids=None, city=None):
        if origin_lat is None or origin_lng is None:
            queryset = NGOProfile.objects.filter(phone__isnull=False).exclude(phone__exact="")
            if city:
                queryset = queryset.filter(city__iexact=city)
            if exclude_ids:
                queryset = queryset.exclude(id__in=exclude_ids)
            return list(queryset), False

        min_lat, max_lat, min_lng, max_lng = cls.bounding_box(origin_lat, origin_lng, radius_km)
        queryset = cls._base_ngo_queryset().filter(
            current_lat__gte=min_lat,
            current_lat__lte=max_lat,
            current_lng__gte=min_lng,
            current_lng__lte=max_lng,
        )
        if city:
            queryset = queryset.filter(city__iexact=city)
        if exclude_ids:
            queryset = queryset.exclude(id__in=exclude_ids)

        if connection.vendor == "postgresql":
            queryset = cls._annotate_distance_postgres(queryset, origin_lat, origin_lng).filter(
                distance_km__lte=radius_km
            ).order_by("distance_km", "id")
            return list(queryset), True

        nearby = []
        for ngo in queryset:
            distance_km = cls.haversine_distance_km(origin_lat, origin_lng, ngo.current_lat, ngo.current_lng)
            if distance_km <= radius_km:
                ngo.distance_km = distance_km
                nearby.append(ngo)
        nearby.sort(key=lambda ngo: (getattr(ngo, "distance_km", cls.EARTH_RADIUS_KM), ngo.id))
        return nearby, False
