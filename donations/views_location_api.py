# donations/views_location_api.py
from django.http import JsonResponse
from django.views.decorators.http import require_GET
import requests

BASE = "https://india-location-hub.in/api/locations"

def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return None

@require_GET
def states(request):
    url = f"{BASE}/states"
    r = requests.get(url, timeout=10)
    data = _safe_json(r)
    if not data:
        return JsonResponse({"success": False, "error": "Invalid response from location API"}, status=502)
    return JsonResponse(data, safe=False)

@require_GET
def districts(request):
    state_id = request.GET.get("state_id")
    if not state_id:
        return JsonResponse({"success": False, "error": "state_id required"}, status=400)

    url = f"{BASE}/districts"
    r = requests.get(url, params={"state_id": state_id}, timeout=10)
    data = _safe_json(r)
    if not data:
        return JsonResponse({"success": False, "error": "Invalid response from location API"}, status=502)
    return JsonResponse(data, safe=False)

@require_GET
def talukas(request):
    district_id = request.GET.get("district_id")
    if not district_id:
        return JsonResponse({"success": False, "error": "district_id required"}, status=400)

    url = f"{BASE}/talukas"
    r = requests.get(url, params={"district_id": district_id}, timeout=10)
    data = _safe_json(r)
    if not data:
        return JsonResponse({"success": False, "error": "Invalid response from location API"}, status=502)
    return JsonResponse(data, safe=False)

@require_GET
def villages(request):
    state = request.GET.get("state")
    district = request.GET.get("district")
    taluka = request.GET.get("taluka")

    if not (state and district and taluka):
        return JsonResponse({"success": False, "error": "state, district, taluka required"}, status=400)

    url = f"{BASE}/villages"
    r = requests.get(url, params={"state": state, "district": district, "taluka": taluka}, timeout=15)
    data = _safe_json(r)
    if not data:
        return JsonResponse({"success": False, "error": "Invalid response from location API"}, status=502)
    return JsonResponse(data, safe=False)
