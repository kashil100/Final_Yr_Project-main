HappyTummy – Food Donation & Distribution Platform

HappyTummy is a web-based platform designed to reduce food wastage by connecting restaurants, NGOs, and volunteers. It enables surplus food donations, efficient coordination, and transparent distribution to those in need.

Problem Statement

Large quantities of edible food are wasted every day while many people struggle with hunger. There is a lack of a centralized, structured system that connects food donors with organizations and volunteers who can distribute food efficiently.

HappyTummy bridges this gap.

Objectives

Minimize food wastage from restaurants

Enable NGOs to request and manage food donations

Allow volunteers to participate in food pickup and delivery

Provide role-based dashboards for smooth coordination

Ensure transparency and accountability in food distribution

User Roles
Restaurant

Register and log in

Submit surplus food details

Track donation status

NGO

Register and log in

Request food based on availability

Manage received donations

Volunteer

Register and log in

Accept delivery requests

Assist in food pickup and distribution

Tech Stack
Frontend

HTML5

CSS3

Bootstrap 5

JavaScript

Backend

Python

Django Framework

Database

SQLite (Development)

Easily extendable to PostgreSQL / MySQL

Authentication

Django Authentication System

Role-based access control

Key Features

Secure user authentication

Role-based dashboards (Restaurant / NGO / Volunteer)

Food surplus submission & confirmation

Real-time donation workflow

Organized database structure

Scalable and modular architecture

Installation & Setup

1️⃣ Clone the Repository
git clone https://github.com/your-username/HappyTummy.git
cd HappyTummy

2️⃣ Create Virtual Environment
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

3️⃣ Install Dependencies
pip install -r requirements.txt

4️⃣ Configure Environment Variables

Copy `.env.example` to `.env` and set your local values:

```bash
cp .env.example .env   # On Windows: copy .env.example .env
```

At minimum for local development, set `DB_PASSWORD` to match your PostgreSQL user. Other values can stay at their placeholders until you enable SMS or email delivery.

5️⃣ Run Migrations
python manage.py makemigrations
python manage.py migrate

6️⃣ Start the Server
python manage.py runserver

Production Deployment

### Deploy on Render (recommended)

This project includes a `render.yaml` blueprint for one-click deployment.

1. Push the repository to GitHub/GitLab/Bitbucket.
2. In the [Render Dashboard](https://dashboard.render.com/), go to **Blueprints** → **New Blueprint Instance**.
3. Connect your repository and apply the blueprint. Render will create:
   - A **PostgreSQL** database (`happytummy-db`)
   - A **Web Service** (`happytummy`) with `DATABASE_URL`, `SECRET_KEY`, and `DEBUG=false` configured automatically
4. Wait for the build to finish (`build.sh` runs migrations and collects static files).
5. Open the **Render Shell** for your web service and create an admin user:
   ```bash
   python manage.py createsuperuser
   ```
6. Visit your `https://<service-name>.onrender.com` URL.

**Render environment variables to add after deploy (optional):**

| Variable | Purpose |
|----------|---------|
| `SMS_BACKEND`, `MSG91_*` | Live SMS notifications |
| `SMTP_*`, `SENDER_EMAIL`, `EMAIL_BACKEND` | Live email notifications |
| `DONATION_CLAIM_BASE_URL` | Public site URL for donation links (auto-set from `RENDER_EXTERNAL_URL` if omitted) |

**Important:** Render's filesystem is ephemeral. Uploaded images (volunteer photos, gallery proofs) are lost on redeploy unless you attach a [Render Persistent Disk](https://render.com/docs/disks) and set `MEDIA_ROOT` to the mount path, or move media to cloud storage (S3, Cloudinary).

### Manual / other hosts

1. Copy `.env.example` to `.env` on the server and set all required values.
2. Set `DEBUG=false`.
3. Set a strong, unique `SECRET_KEY` (never commit it).
4. Set `ALLOWED_HOSTS` to your domain(s), comma-separated.
5. Set `CSRF_TRUSTED_ORIGINS` to your HTTPS origin(s), comma-separated.
6. Configure PostgreSQL via `DATABASE_URL` or `DB_*` variables.
7. Configure SMTP and SMS provider credentials if live notifications are required.
8. If running behind a reverse proxy, set `SECURE_PROXY_SSL_HEADER=true`.
9. Collect static files and run with gunicorn:

```bash
./build.sh
gunicorn happytummy.wsgi:application --bind 0.0.0.0:$PORT
```

LIVE DEPLOYMENT LINK:

SMS Setup

Recommended provider for Indian mobile numbers: MSG91

1. Copy `.env.example` to `.env`.
2. Fill in your MSG91 values in `.env`:
   SMS_BACKEND=msg91
   MSG91_AUTH_KEY=your_msg91_auth_key
   MSG91_FLOW_ID=your_msg91_flow_id
   MSG91_SENDER_ID=your_msg91_sender_id
3. Restart the Django server after updating `.env`.
4. Send a test SMS before testing from the restaurant dashboard:
   python manage.py send_test_sms +919876543210

Important:
End users such as restaurants and NGOs do not need MSG91 accounts. They only use their regular phone numbers saved in HappyTummy. MSG91 is the SMS gateway configured once by the platform owner so the app can send SMS to those normal mobile numbers.

For Indian SMS delivery, MSG91 uses approved templates / flows. The donation notification flow in this project passes these variables:
- `restaurant_name`
- `quantity`
- `food_type`
- `address`
- `city`

Your MSG91 Flow template should use those same variable names.

If MSG91 is configured correctly, NGOs in the same city as the restaurant will receive a real SMS when a donation is posted.

Email Notification Setup

When a restaurant posts a surplus donation, HappyTummy queues a background notification job after the donation is saved. Eligible NGOs receive one email per donation if their account is active, their email is marked verified, and donation notifications are enabled.

1. Run migrations after pulling this feature:
   python manage.py migrate

2. For local development, no SMTP account is required unless you want live delivery. Django uses the console email backend by default when `DEBUG=True`.

3. For production SMTP delivery, copy `.env.example` to `.env` and set:
   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
   SMTP_HOST=your_smtp_host
   SMTP_PORT=587
   SMTP_USERNAME=your_smtp_username
   SMTP_PASSWORD=your_smtp_password
   SMTP_USE_TLS=true
   SENDER_EMAIL=HappyTummy <no-reply@your-domain.com>
   DONATION_CLAIM_BASE_URL=https://your-happytummy-domain.com
   DONATION_NOTIFICATIONS_ASYNC=true

4. Restart Django after changing `.env`.

Email delivery failures are saved on the donation notification log and written to application logs. They do not block donation creation or dashboard notification creation.

Testing Credentials (Optional)

You can create test users using the registration pages for:

Restaurant

NGO

Volunteer

Or via Django Admin:

python manage.py createsuperuser

🚀 Future Enhancements

📍 Google Maps integration for live tracking

📱 Mobile app version

🔔 Notification system (SMS / Email)

☁️ Cloud database deployment

📈 Analytics dashboard for impact measurement

🤝 Contribution

Contributions are welcome!
Feel free to fork the repository and submit a pull request.

📜 License

This project is developed for educational purposes and is open for learning and improvement.

❤️ Acknowledgement

HappyTummy is inspired by the vision of creating a hunger-free society by leveraging technology for social good.
