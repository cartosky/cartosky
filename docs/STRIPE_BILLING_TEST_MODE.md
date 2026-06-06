# Stripe Billing Test Mode

Use this runbook to validate the CartoSky Stripe billing flow while Clerk remains the auth provider.

## Required setup

1. Create a Stripe Product named `CartoSky Pro` in Stripe test mode.
2. Create a recurring monthly Price for that product at `$7.50/month`.
3. Set `STRIPE_PRO_PRICE_ID` to that Stripe test price id.
4. Set `STRIPE_SECRET_KEY` to your Stripe test secret key.
5. Configure a Stripe webhook endpoint that points to `/api/v4/billing/webhook` on the CartoSky backend.
6. Set `STRIPE_WEBHOOK_SECRET` to the signing secret from that Stripe webhook configuration.
7. Enable billing flags only where needed:
   - backend: `CARTOSKY_BILLING_ENABLED=true`
   - backend: `CARTOSKY_PRO_GATING_ENABLED=true`
   - frontend: `VITE_BILLING_ENABLED=true`
   - frontend: `VITE_PRO_GATING_ENABLED=true`
   - frontend: `VITE_PRICING_PREVIEW_ENABLED=true`

## Test checkout flow

1. Sign in to CartoSky with Clerk.
2. Open `/pricing`.
3. Start the Pro checkout flow.
4. Use Stripe test card `4242 4242 4242 4242` with any valid future expiration, CVC, and ZIP.
5. Complete checkout.

## Expected results

1. Stripe sends webhook events to `/api/v4/billing/webhook`.
2. CartoSky resolves the Clerk user id from Stripe metadata.
3. Clerk public metadata updates to:

```json
{
  "plan": "pro"
}
```

4. Clerk private metadata stores Stripe linkage values such as:

```json
{
  "stripe_customer_id": "cus_xxx",
  "stripe_subscription_id": "sub_xxx",
  "stripe_subscription_status": "active"
}
```

5. After a session refresh or re-login, the Clerk JWT metadata includes `plan=pro`.
6. Protected product requests begin authorizing and backend entitlement checks allow access.

## Test portal flow

1. Open `/account#/subscription` or the Pro state on `/pricing`.
2. Choose `Manage Subscription`.
3. Confirm Stripe Customer Portal opens for the linked customer.
4. Cancel or update the subscription in test mode.
5. Verify the next Stripe subscription webhook updates Clerk metadata and the plan returns to `free` when the status maps to a free state.

## Rollback verification

Set these flags back to `false` without code changes:

- `CARTOSKY_BILLING_ENABLED`
- `CARTOSKY_PRO_GATING_ENABLED`
- `VITE_BILLING_ENABLED`
- `VITE_PRO_GATING_ENABLED`
- `VITE_PRICING_PREVIEW_ENABLED`

Expected rollback behavior:

1. Pricing navigation disappears.
2. Pricing becomes unavailable.
3. Protected products stay accessible because gating is off.
4. Public model requests stay unauthenticated unless another code path already requires auth.