# Environment Setup Guide - Step by Step

This guide provides detailed instructions for obtaining all required API keys and configuring your `.env` file.

## 📋 Required Keys & Configuration

| Variable | Purpose | How to Get |
|----------|---------|-----------|
| `SECRET_KEY` | Django security key | Generate yourself |
| `EMAIL_HOST_PASSWORD` | SendGrid API key | SendGrid dashboard |
| `STRIPE_PUBLIC_KEY` | Stripe public key | Stripe dashboard |
| `STRIPE_SECRET_KEY` | Stripe secret key | Stripe dashboard |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook secret | Stripe webhook setup |
| `DEFAULT_FROM_EMAIL` | Sender email | Your email address |

---

## 1️⃣ Django SECRET_KEY

### Why: Protects session data and CSRF tokens

### How to Generate:

**Option A: Using Django Shell**
```bash
python manage.py shell
```

Then paste this code:
```python
from django.core.management.utils import get_random_secret_key
print(get_random_secret_key())
```

**Option B: Online Generator**
Visit: https://djecrety.ir/

**Option C: Manual Generation**
```python
import secrets
import string
key = ''.join(secrets.choice(string.ascii_letters + string.digits + string.punctuation) for i in range(50))
print(key)
```

### Add to `.env`:
```
SECRET_KEY=your_generated_key_here
```

⚠️ **Important**: Never commit this to GitHub! Add `.env` to `.gitignore`

---

## 2️⃣ SendGrid Email Configuration

### Why: Send booking confirmation emails

### Step-by-Step:

#### Step 1: Create SendGrid Account
1. Go to https://sendgrid.com/
2. Click "Create Free Account"
3. Fill in your details and verify email
4. Complete the onboarding questionnaire

#### Step 2: Generate API Key
1. Log in to SendGrid dashboard
2. Navigate to **Settings** → **API Keys** (left sidebar)
3. Click **+ Create API Key**
4. Choose "Restricted Access"
5. Under **Mail Send**, select:
   - ✅ Mail Send (Full Access)
   - ✅ Mail Send Tracking (Read Only)
6. Click **Create & View**
7. Copy the API key

#### Step 3: Verify Sender Email
1. Go to **Settings** → **Sender Authentication**
2. Click **Create Connection** (Domain Authentication recommended for production)
3. For testing, use **Single Sender Verification**:
   - Enter your email
   - Click verification link in email
   - Done!

#### Step 4: Update `.env`
```
EMAIL_HOST=smtp.sendgrid.net
EMAIL_PORT=2525
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=SG.your_api_key_here
EMAIL_USE_TLS=false
EMAIL_USE_SSL=false
DEFAULT_FROM_EMAIL=your_verified_email@example.com
```

### Test SendGrid:
```bash
python manage.py shell
```

```python
from django.core.mail import send_mail
send_mail(
    'Test Email',
    'This is a test email',
    'your_email@example.com',
    ['recipient@example.com'],
)
```

---

## 3️⃣ Stripe Payment Gateway

### Why: Process credit card payments securely

### Step-by-Step:

#### Step 1: Create Stripe Account
1. Go to https://dashboard.stripe.com/register
2. Sign up with your email
3. Verify your email
4. Complete your account details

#### Step 2: Get API Keys (Publishable & Secret)
1. Log in to Stripe Dashboard
2. Navigate to **Developers** → **API keys** (left sidebar)
3. You'll see two test keys by default:
   - **Publishable key**: starts with `pk_test_`
   - **Secret key**: starts with `sk_test_`
4. Copy both keys to your `.env`:

```
STRIPE_PUBLIC_KEY=pk_test_your_key_here
STRIPE_SECRET_KEY=sk_test_your_key_here
```

#### Step 3: Setup Webhook for Payment Confirmation
Webhooks allow Stripe to notify your server when payments succeed/fail.

**Local Development Setup:**

1. **Install Stripe CLI**
   - Download from: https://github.com/stripe/stripe-cli/releases
   - Extract and add to PATH

2. **Login to Stripe CLI**
   ```bash
   stripe login
   ```
   - Opens browser authentication
   - Click "Allow" to authorize

3. **Forward Webhooks to Local Server**
   ```bash
   stripe listen --forward-to localhost:8000/movies/webhooks/stripe/
   ```
   - This will display:
   ```
   Ready! Your webhook signing secret is: whsec_test_________
   ```
   - Copy this secret to your `.env`:
   ```
   STRIPE_WEBHOOK_SECRET=whsec_test_your_secret_here
   ```

**Production Setup:**

1. In Stripe Dashboard → **Developers** → **Webhooks**
2. Click **Add endpoint**
3. Enter URL: `https://yourdomain.com/movies/webhooks/stripe/`
4. Select events:
   - ✅ `payment_intent.succeeded`
   - ✅ `payment_intent.payment_failed`
   - ✅ `payment_intent.canceled`
5. Click **Add endpoint**
6. Click on the endpoint → reveal **Signing secret**
7. Copy to `STRIPE_WEBHOOK_SECRET`

#### Step 4: Test Stripe Integration
```bash
python manage.py shell
```

```python
import stripe
from django.conf import settings

stripe.api_key = settings.STRIPE_SECRET_KEY

# Create a test PaymentIntent
intent = stripe.PaymentIntent.create(
    amount=25000,  # ₹250.00 in paise
    currency="inr",
    metadata={"booking_id": 1}
)

print("Payment Intent Created:")
print(f"ID: {intent.id}")
print(f"Status: {intent.status}")
print(f"Client Secret: {intent.client_secret}")
```

### Test Payments:

Use these test card numbers:

| Card | Number | Use |
|------|--------|-----|
| Success | `4242 4242 4242 4242` | Test successful payment |
| Failure | `4000 0000 0000 0002` | Test failed payment |
| Requires 3D Secure | `4000 0025 0000 3155` | Test authentication |

Future & CVV: Use any future date and any 3-digit number

---

## 📝 Complete `.env` Template

Copy and fill in your values:

```bash
# Django Settings
SECRET_KEY=your_django_secret_key_here

# Email Configuration (SendGrid)
EMAIL_HOST=smtp.sendgrid.net
EMAIL_PORT=2525
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=SG.your_sendgrid_api_key_here
EMAIL_USE_TLS=false
EMAIL_USE_SSL=false
DEFAULT_FROM_EMAIL=your_verified_email@example.com

# Stripe Payment Gateway
STRIPE_PUBLIC_KEY=pk_test_your_stripe_publishable_key_here
STRIPE_SECRET_KEY=sk_test_your_stripe_secret_key_here
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret_here
TICKET_PRICE=250.00
STRIPE_CURRENCY=INR

# Database (Optional - defaults to SQLite)
# DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require
```

---

## ✅ Verification Checklist

After adding all keys to `.env`, verify everything works:

```bash
# 1. Check Django settings
python manage.py check

# 2. Test email configuration
python manage.py shell
from django.test.utils import get_runner
from django.conf import settings

# 3. Test Stripe connection
import stripe
stripe.api_key = settings.STRIPE_SECRET_KEY
print("Stripe connected!" if stripe.api_key else "Stripe not configured")

# 4. Run development server
python manage.py runserver
```

---

## 🔒 Security Best Practices

1. **Never commit `.env` to Git**
   ```bash
   # Add to .gitignore
   echo ".env" >> .gitignore
   git rm --cached .env
   git commit -m "Remove .env from tracking"
   ```

2. **Rotate Keys Regularly**
   - Change SECRET_KEY quarterly
   - Rotate API keys if leaked

3. **Use Different Keys per Environment**
   - Development: Use test keys (pk_test_, sk_test_)
   - Production: Use live keys (pk_live_, sk_live_)

4. **Restrict API Key Permissions**
   - SendGrid: Use restricted keys for mail only
   - Stripe: Enable IP whitelisting if available

5. **Monitor API Usage**
   - Check SendGrid dashboard for suspicious activity
   - Review Stripe transaction logs regularly

---

## 🚀 Quick Reference Commands

```bash
# Generate Django secret key
python manage.py shell
from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())

# Start Stripe webhook listener (local development)
stripe listen --forward-to localhost:8000/movies/webhooks/stripe/

# Send test email
python manage.py shell
from django.core.mail import send_mail; send_mail('Test', 'Test body', 'from@example.com', ['to@example.com'])

# Test payment
python manage.py shell
import stripe; from django.conf import settings; stripe.api_key = settings.STRIPE_SECRET_KEY; print(stripe.PaymentIntent.create(amount=25000, currency="inr"))
```

---

## 💡 Troubleshooting

### Email Not Sending
- ✅ Verify sender email in SendGrid
- ✅ Check API key is correct
- ✅ Ensure `DEFAULT_FROM_EMAIL` is verified
- ✅ Check `EMAIL_HOST_PASSWORD` isn't truncated

### Stripe Webhook Not Working
- ✅ Verify endpoint URL is correct
- ✅ Check webhook secret in `.env`
- ✅ Ensure `STRIPE_SECRET_KEY` is secret (not public)
- ✅ Use Stripe CLI to test locally

### Payment Not Processing
- ✅ Verify `STRIPE_PUBLIC_KEY` is correct (starts with pk_)
- ✅ Check `STRIPE_SECRET_KEY` is correct (starts with sk_)
- ✅ Ensure amount is in correct currency (INR)
- ✅ Use test card numbers from table above

---

## 📚 Additional Resources

- Django Secret Key: https://djecrety.ir/
- SendGrid Documentation: https://docs.sendgrid.com/
- Stripe Documentation: https://stripe.com/docs
- Stripe CLI: https://stripe.com/docs/stripe-cli
- Stripe Test Cards: https://stripe.com/docs/testing

