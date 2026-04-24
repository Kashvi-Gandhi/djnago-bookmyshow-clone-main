# Payment Gateway Integration Documentation

## Overview
This Django application integrates Stripe payment gateway with comprehensive security measures, idempotency handling, and fraud prevention.

## Payment Lifecycle

### 1. Seat Selection & Booking Creation
- User selects seats on `book_seats` page
- System validates seat availability with `select_for_update`
- Creates `Booking` objects with `status="pending_payment"`
- Temporarily reserves seats (`is_booked=True`)
- Creates Stripe PaymentIntent via `PaymentService.create_payment_intent()`
- Stores PaymentIntent ID and client_secret in database
- Redirects to payment page

### 2. Payment Processing
- User completes payment on Stripe Elements form
- Frontend uses Stripe.js to tokenize card details
- Payment confirmation happens client-side with `stripe.confirmCardPayment()`
- Stripe processes payment asynchronously

### 3. Webhook Processing
- Stripe sends webhook to `/movies/webhooks/stripe/`
- Webhook signature verified using `STRIPE_WEBHOOK_SECRET`
- Idempotency ensured via `PaymentAttempt` model (prevents duplicate processing)
- Payment status updated based on event type:
  - `payment_intent.succeeded` → Confirm booking, send email
  - `payment_intent.payment_failed` → Cancel booking, release seats
  - `payment_intent.canceled` → Cancel booking, release seats

### 4. Success/Failure Handling
- Successful payments: Booking status → "confirmed", email sent
- Failed payments: Booking status → "cancelled", seats released
- User redirected to appropriate success/failure page

### 5. Cleanup Process
- Background job runs `cleanup_expired_reservations` command
- Cancels expired PaymentIntents (>15 minutes old)
- Releases reserved seats for failed/expired payments

## Security Measures

### 1. Server-Side Verification
- **No frontend-only validation**: All payment confirmations verified server-side via webhooks
- **Webhook signature verification**: Ensures webhooks are from Stripe
- **Idempotency keys**: `PaymentAttempt` model prevents replay attacks
- **Database transactions**: Atomic operations prevent race conditions

### 2. Fraud Prevention
- **PaymentIntent metadata**: Includes booking details for Stripe dashboard verification
- **Amount validation**: Server validates payment amounts match booking
- **User authentication**: Only authenticated users can initiate payments
- **Seat reservation timeout**: Prevents long-term holds on seats

### 3. Data Protection
- **No card storage**: Stripe.js tokenizes cards, never touches server
- **PCI compliance**: Stripe handles all card data processing
- **Environment variables**: Sensitive keys stored securely
- **HTTPS required**: All payment operations require secure connections

## Configuration

### Environment Variables
```bash
# Stripe Configuration
STRIPE_PUBLIC_KEY=pk_test_your_publishable_key
STRIPE_SECRET_KEY=sk_test_your_secret_key
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret

# Payment Settings
TICKET_PRICE=250.00  # Price per ticket in INR
STRIPE_CURRENCY=INR
```

### Webhook Setup
1. In Stripe Dashboard → Webhooks
2. Add endpoint: `https://yourdomain.com/movies/webhooks/stripe/`
3. Select events:
   - `payment_intent.succeeded`
   - `payment_intent.payment_failed`
   - `payment_intent.canceled`
4. Copy webhook secret to `STRIPE_WEBHOOK_SECRET`

## Database Models

### Payment
- Links to Booking via OneToOneField
- Stores Stripe PaymentIntent ID and client_secret
- Tracks payment status and amounts

### PaymentAttempt
- Records webhook processing attempts
- Ensures idempotency with unique (payment, event_id) constraint
- Stores event payload and verification status

### Booking
- Extended with status field: pending_payment, confirmed, cancelled, refunded
- Links to payment for completed transactions

## API Endpoints

### POST /movies/theater/{id}/seats/book/
- Creates booking and payment intent
- Returns payment page URL

### GET /movies/payment/{id}/
- Displays Stripe Elements payment form
- Includes client_secret for payment confirmation

### POST /movies/webhooks/stripe/
- Processes Stripe webhooks
- Requires valid signature
- Idempotent processing

### GET /movies/booking/{id}/success|failed/
- Success/failure confirmation pages

## Error Handling

### Payment Failures
- Stripe errors displayed to user
- Automatic cleanup of failed payments
- Seat release and booking cancellation

### Network Issues
- Webhook retry mechanism (Stripe handles retries)
- Idempotent processing prevents duplicate actions
- Timeout handling for expired reservations

### Edge Cases
- Duplicate webhook events: Ignored via idempotency
- Payment intent not found: Logged and handled gracefully
- Database conflicts: Transaction rollback and retry

## Monitoring & Logging

### Application Logs
- Payment creation attempts
- Webhook processing results
- Failed payment cleanup operations

### Stripe Dashboard
- Payment intent status tracking
- Webhook delivery monitoring
- Dispute and refund management

## Testing

### Test Cards
Use Stripe test card numbers:
- Success: `4242 4242 4242 4242`
- Failure: `4000 0000 0000 0002`
- Requires authentication: `4000 0025 0000 3155`

### Webhook Testing
Use Stripe CLI for local webhook testing:
```bash
stripe listen --forward-to localhost:8000/movies/webhooks/stripe/
```

## Security Best Practices

1. **Never store card details** on your server
2. **Always verify webhook signatures**
3. **Use idempotency keys** for all operations
4. **Implement proper logging** without exposing sensitive data
5. **Regular security audits** of payment flows
6. **Monitor for suspicious activity** in Stripe dashboard
7. **Keep dependencies updated** for security patches

## Fraud Prevention Checklist

- [x] Server-side payment verification
- [x] Webhook signature validation
- [x] Idempotency implementation
- [x] Amount validation
- [x] User authentication required
- [x] Seat reservation timeouts
- [x] Comprehensive logging
- [x] PCI compliance (via Stripe)
- [x] HTTPS enforcement
- [x] Environment variable security