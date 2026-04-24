# Stripe Payment Integration Setup Guide

## Quick Start (For Testing)

### Option 1: Using Stripe Test Keys (Recommended for Development)

1. **Create a Stripe Account**
   - Go to https://stripe.com
   - Sign up for a free account
   - Complete the setup wizard

2. **Get Your Test Keys**
   - Log in to Stripe Dashboard
   - Go to **Developers** → **API keys**
   - You'll see two sets of keys: "Publishable key" and "Secret key"
   - **IMPORTANT**: Use the "Test" versions (they start with `pk_test_` and `sk_test_`)

3. **Set Environment Variables**

   On Windows (PowerShell):
   ```powershell
   $env:STRIPE_PUBLIC_KEY = "pk_test_YOUR_ACTUAL_KEY_HERE"
   $env:STRIPE_SECRET_KEY = "sk_test_YOUR_ACTUAL_KEY_HERE"
   $env:STRIPE_WEBHOOK_SECRET = "whsec_YOUR_WEBHOOK_SECRET_HERE"
   ```

   Or create a `.env` file in your project root:
   ```
   STRIPE_PUBLIC_KEY=pk_test_YOUR_ACTUAL_KEY_HERE
   STRIPE_SECRET_KEY=sk_test_YOUR_ACTUAL_KEY_HERE
   STRIPE_WEBHOOK_SECRET=whsec_YOUR_WEBHOOK_SECRET_HERE
   ```

4. **Restart Your Django Server**
   ```bash
   python manage.py runserver
   ```

---

## Using Test Card Numbers

Once configured, use these Stripe test card numbers:

| Card Number | CVC | Date | Result |
|---|---|---|---|
| 4242 4242 4242 4242 | Any | Future | **SUCCESS** ✓ |
| 4000 0000 0000 0002 | Any | Future | **DECLINE** ✗ |
| 4000 0025 0000 3155 | Any | Future | **Require Authentication** |
| 3782 822463 10005 | Any | Future | Amex (Requires 4-digit CVC) |

**Example:**
- Card: 4242 4242 4242 4242
- Expiry: 12/25
- CVC: 123

---

## Webhook Setup (Optional, For Production-Like Testing)

1. **Get Webhook Endpoint Secret**
   - In Stripe Dashboard → Developers → Webhooks
   - Add an endpoint: `https://yourdomain.com/stripe-webhook/`
   - Copy the "Signing Secret" 
   - Set as `STRIPE_WEBHOOK_SECRET` environment variable

2. **Use Stripe CLI for Local Testing**
   - Download Stripe CLI: https://stripe.com/docs/stripe-cli
   - Run: `stripe listen --forward-to localhost:8000/stripe-webhook/`
   - Copy the webhook signing secret shown
   - Set it as environment variable

---

## Verification Checklist

- [ ] Stripe account created
- [ ] API keys copied from Dashboard
- [ ] Environment variables set (STRIPE_PUBLIC_KEY, STRIPE_SECRET_KEY)
- [ ] Django server restarted
- [ ] Payment page loads without "Payment Configuration Error"
- [ ] Card element appears with placeholder text
- [ ] Test payment with 4242 4242 4242 4242
- [ ] Payment succeeds and shows booking confirmation

---

## Troubleshooting

### "Your card number is incomplete" Error
**Cause:** Stripe public key is empty or invalid
**Fix:** 
1. Check environment variables are set correctly
2. Verify you're using **test keys** (pk_test_...)
3. Restart Django server
4. Clear browser cache

### "Invalid API Key" Error
**Cause:** Secret key is wrong or expired
**Fix:**
1. Generate new API keys from Stripe Dashboard
2. Update environment variables
3. Restart server

### Payment Form Not Loading
**Cause:** Stripe.js library failed to load
**Fix:**
1. Check internet connection
2. Verify HTTPS in production (Stripe requires it)
3. Check browser console for JavaScript errors

### Webhook Not Firing
**Cause:** Webhook URL not accessible or signing secret mismatched
**Fix:**
1. Ensure server is publicly accessible
2. Verify webhook signing secret in environment
3. Use Stripe CLI: `stripe trigger payment_intent.succeeded`

---


## Payment Flow Diagram

```
1. User selects seats → Payment page
   ↓
2. Stripe form loads with public key
   ↓
3. User enters card details → Client-side validation
   ↓
4. Form submitted → Stripe.confirmCardPayment() 
   ↓
5. Stripe processes payment (with secret key)
   ↓
6. Webhook fires (payment_intent.succeeded/failed)
   ↓
7. Backend updates booking status
   ↓
8. User redirected to success/failure page
```

---

## Files to Check

- `bookmyseat/settings.py` - Stripe configuration
- `movies/views.py` - payment_page view
- `movies/payment_service.py` - Stripe API calls
- `templates/movies/payment.html` - Payment form
- `movies/urls.py` - Payment webhook endpoint

---

## Support

For help:
- Stripe Documentation: https://stripe.com/docs
- Contact Stripe Support: https://support.stripe.com
- Check your Django logs: `python manage.py runserver --verbosity 3`
