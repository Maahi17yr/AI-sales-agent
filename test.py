"""Naija Bites checkout diagnostic. Run with backend on port 8000."""
import json
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8000'

def request(method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode())
    except urllib.error.URLError as error:
        return None, {'error': str(error.reason)}

def show(title, status, data):
    print(f'\n--- {title} (HTTP {status}) ---')
    print(json.dumps(data, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    status, customer = request('GET', '/checkout/dev-customer')
    show('Development customer', status, customer)
    status, checkout = request('GET', '/checkout')
    show('Pending checkout', status, checkout)
    if status != 200 or not checkout.get('pending'):
        print('\nNo saved checkout. In chat, add a meal, give delivery details, and ask the agent to prepare checkout. Then rerun this script.')
    elif not checkout.get('fingerprint') or not checkout.get('summary'):
        print('\nCheckout is incomplete. Do not confirm.')
    else:
        print('\nReview the full summary above carefully.')
        if input('Type CONFIRM to create this test order, or Enter to stop: ').strip() == 'CONFIRM':
            status, result = request('POST', '/checkout/confirm', {
                'customer_approved': True,
                'reviewed_fingerprint': checkout['fingerprint'],
            })
            show('Confirmation', status, result)
        else:
            print('No order created.')
