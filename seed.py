"""Seeds initial data on first run: default admin user, standard income/expense types."""
from werkzeug.security import generate_password_hash
import db

DEFAULT_ADMIN_USERNAME = 'admin'
DEFAULT_ADMIN_PASSWORD = 'Milli0nBilli0n$'
DEFAULT_ADMIN_NAME = 'Administrator'
DEFAULT_DOCUMENT_CATEGORIES = [
    'Lift Servicing Receipts',
    'Generator Servicing Receipts',
    'AMC Invoices',
    'Utility Bills',
    'Repair Invoices',
    'Compliance Documents',
    'Insurance Documents',
    'Other Documents',
]


def ensure_admin_user():
    username = DEFAULT_ADMIN_USERNAME
    password = DEFAULT_ADMIN_PASSWORD
    name = DEFAULT_ADMIN_NAME

    users = db.load('users')
    admin_user = next((u for u in users if u.get('username') == username), None)

    if admin_user:
        updates = {}
        if admin_user.get('role') != 'admin':
            updates['role'] = 'admin'
        if admin_user.get('name') != name:
            updates['name'] = name
        if password:
            updates['password_hash'] = generate_password_hash(password)
        if updates:
            db.update('users', admin_user['id'], updates)
        return

    db.insert('users', {
        'username': username,
        'password_hash': generate_password_hash(password),
        'name': name,
        'role': 'admin',
    })


def seed():
    db.init_db()
    ensure_admin_user()

    document_categories = db.load('document_categories')
    existing_categories = {c.get('name', '').strip().lower() for c in document_categories}
    for name in DEFAULT_DOCUMENT_CATEGORIES:
        if name.lower() not in existing_categories:
            db.insert('document_categories', {'name': name})

    income_types = db.load('income_types')
    if not income_types:
        for name in ['Monthly Maintenance', 'Late Fee / Penalty', 'Parking Charges',
                     'Interest Income', 'Other Income']:
            db.insert('income_types', {'name': name, 'description': ''})

    expense_types = db.load('expense_types')
    if not expense_types:
        recurring_defaults = [
            ('Watchman Salary', True, 0),
            ('Housekeeping / Cleaning Staff', True, 0),
            ('Electricity Bill (Common Area)', True, 0),
            ('Water Bill / Water Tanker', True, 0),
            ('Lift Maintenance (AMC)', True, 0),
            ('Garden / Landscaping', True, 0),
            ('Pest Control', True, 0),
            ('Diesel for Generator', True, 0),
            ('Security Services', True, 0),
            ('Internet / DTH (Common Area)', True, 0),
            ('Repairs & Maintenance', False, 0),
            ('Plumbing Work', False, 0),
            ('Electrical Work', False, 0),
            ('Painting', False, 0),
            ('Office / Stationery', False, 0),
            ('Legal / Audit Fees', False, 0),
            ('Festival / Function Expenses', False, 0),
            ('Miscellaneous', False, 0),
        ]
        for name, recurring, amt in recurring_defaults:
            db.insert('expense_types', {
                'name': name, 'description': '', 'recurring': recurring,
                'default_amount': amt
            })


    settings = db.load('settings')
    if not settings:
        db.insert('settings', {
            'opening_balance': 0.0,
            'opening_balance_date': '2026-07-01',
            'opening_corpus_fund': 0.0,
            'opening_corpus_fund_date': '2026-07-01',
        })


if __name__ == '__main__':
    seed()
    print("Seed data created.")
