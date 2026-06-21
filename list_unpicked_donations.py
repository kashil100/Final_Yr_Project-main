from donations.models import SurplusFoodRequest

# List all unpicked donations and their restaurant city
for req in SurplusFoodRequest.objects.filter(is_picked=False):
    print(f"ID: {req.id}, Food: {req.food_type}, Qty: {req.quantity}, City: {req.restaurant.city}, Restaurant: {req.restaurant.business_name}")
