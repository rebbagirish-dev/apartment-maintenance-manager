"""Seeds initial data on first run: default admin user, standard income/expense types."""
from werkzeug.security import generate_password_hash
import db


def seed():
    db.init_db()

    users = db.load('users')
    if not users:
        db.insert('users', {
            'username': 'admin',
            'password_hash': generate_password_hash('admin123'),
            'name': 'Administrator',
            'role': 'admin',
        })

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
