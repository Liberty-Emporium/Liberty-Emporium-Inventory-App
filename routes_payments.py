from flask import Blueprint, request, redirect, jsonify, url_for, session
from flask import current_app as app
import stripe
import os
import json
import datetime

payments = Blueprint('payments', __name__)

# Stripe config — use test keys until real account is active
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', 'sk_test_placeholder_replace_me')
STRIPE_PUBLIC_KEY = os.environ.get('STRIPE_PUBLIC_KEY', 'pk_test_placeholder_replace_me')

# Pricing tiers
PLANS = {
    'starter': {
        'name': 'Starter',
        'price': 29900,  # cents: $299
        'currency': 'usd',
        'description': 'One-store inventory management',
        'features': [
            'Single store setup',
            'Full inventory management',
            'Basic listing generation',
            'Standard support',
        ],
    },
    'pro': {
        'name': 'Pro',
        'price': 49900,  # cents: $499
        'currency': 'usd',
        'description': 'Inventory + AI features',
        'features': [
            'Everything in Starter',
            'AI photo analysis',
            'Ad generator (images + video)',
            '24/7 uptime monitoring',
            'Email support',
        ],
    },
    'enterprise': {
        'name': 'Enterprise',
        'price': 79900,  # cents: $799
        'currency': 'usd',
        'description': 'Complete retail platform',
        'features': [
            'Everything in Pro',
            'Custom white-label branding',
            'Seasonal sales engine',
            'Square integration',
            'Priority support',
        ],
    },
}


def get_stripe_client():
    """Get a configured Stripe client."""
    return stripe


@payments.route('/pay/<plan>')
def checkout(plan):
    """Redirect to Stripe Checkout for the selected plan."""
    if plan not in PLANS:
        return redirect(url_for('wizard'))
    
    plan_data = PLANS[plan]
    
    # In production, create a Stripe Checkout Session
    try:
        # For now, return a payment page with Stripe Elements
        return redirect(url_for('payment_page', plan=plan))
    except Exception as e:
        return redirect(url_for('wizard'))


@payments.route('/pay')
def payment_page():
    """Payment page with Stripe checkout."""
    plan = request.args.get('plan', 'pro')
    if plan not in PLANS:
        plan = 'pro'
    
    plan_data = PLANS[plan]
    
    # Render payment page template
    return app.template_global('render_template')(
        'payment_page.html',
        plan=plan,
        plan_data=plan_data,
        stripe_public_key=STRIPE_PUBLIC_KEY,
    )


@payments.route('/webhook/stripe', methods=['POST'])
def stripe_webhook():
    """Handle Stripe payment events."""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('stripe-signature')
    
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400
    
    if event['type'] == 'checkout.session.completed':
        session_data = event['data']['object']
        # Extract customer info
        customer_email = session_data.get('customer_email')
        metadata = session_data.get('metadata', {})
        
        # Store the payment record
        leads_file = os.path.join(app.config.get('CUSTOMERS_DIR', 'customers'), 'payments.json')
        os.makedirs(os.path.dirname(leads_file), exist_ok=True)
        
        payments_list = []
        if os.path.exists(leads_file):
            with open(leads_file) as f:
                payments_list = json.load(f)
        
        payments_list.append({
            'plan': metadata.get('plan', 'unknown'),
            'email': customer_email,
            'amount': session_data.get('amount_total', 0),
            'currency': session_data.get('currency', 'usd'),
            'stripe_session_id': session_data.get('id'),
            'created_at': datetime.datetime.now().isoformat(),
        })
        
        with open(leads_file, 'w') as f:
            json.dump(payments_list, f, indent=2)
        
        # TODO: Send Jay a notification email about the payment
    
    return jsonify({'status': 'success'}), 200
