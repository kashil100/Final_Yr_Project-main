import os
import time
from django.conf import settings
from django.contrib.auth import logout

BOOT_FILE = os.path.join(settings.BASE_DIR, '.server_boot')

def get_server_boot_time():
    if not os.path.exists(BOOT_FILE):
        with open(BOOT_FILE, 'w') as f:
            f.write(str(time.time()))
    with open(BOOT_FILE, 'r') as f:
        return f.read().strip()

class ForceLogoutOnServerRestartMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.boot_time = get_server_boot_time()

    def __call__(self, request):
        session_boot = request.session.get('server_boot')
        if session_boot != self.boot_time:
            if request.user.is_authenticated:
                logout(request)
            request.session.flush()
            request.session['server_boot'] = self.boot_time
        response = self.get_response(request)
        return response
