from dataclasses import dataclass


@dataclass(frozen=True)
class RadiusDecision:
    radius_km: int
    seconds_remaining: int | None
    debug_label: str


class RadiusService:
    RULES = (
        (3600, 2, "<=1h"),
        (7200, 5, "<=2h"),
        (14400, 10, "<=4h"),
        (21600, 20, "<=6h"),
        (None, 30, ">6h"),
    )

    @classmethod
    def get_radius_for_seconds(cls, seconds_remaining):
        if seconds_remaining is None:
            return RadiusDecision(radius_km=30, seconds_remaining=None, debug_label="unknown-expiry")

        seconds_remaining = max(0, int(seconds_remaining))
        for threshold, radius_km, label in cls.RULES:
            if threshold is None or seconds_remaining <= threshold:
                return RadiusDecision(
                    radius_km=radius_km,
                    seconds_remaining=seconds_remaining,
                    debug_label=label,
                )

        return RadiusDecision(radius_km=30, seconds_remaining=seconds_remaining, debug_label="fallback")

    @classmethod
    def get_radius_for_donation(cls, donation):
        return cls.get_radius_for_seconds(donation.time_remaining_seconds)
