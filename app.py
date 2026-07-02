import os
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                    session, flash, jsonify, Response)
from werkzeug.security import generate_password_hash, check_password_hash

import db
from seed import seed

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me-in-production')

# Sessions time out after 20 minutes of inactivity. Flask refreshes the
# cookie's expiry on every request by default, so this behaves as an idle
# timeout rather than a fixed 20-minute cutoff from login.
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=20)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True

seed()  # make sure default data exists

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            if session:
                flash('Your session has expired. Please log in again.', 'error')
                session.clear()
            return redirect(url_for('login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get('role') != 'admin':
            flash('Only admins can access that.', 'error')
            return redirect(url_for('dashboard'))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Role-based portal access
#
#   admin / manager -> full access, full read-write, everywhere
#   owner           -> can VIEW everything (dashboard, income, expenses,
#                       watchman, events, reports) but strictly read-only;
#                       no access to master data (Users / Income Types /
#                       Expense Types)
#   tenant          -> access is limited to the Reports section only (read-only)
# ---------------------------------------------------------------------------

TENANT_ALLOWED_ENDPOINTS = {
    'reports', 'export_report', 'event_report', 'export_event_report',
    'logout', 'static', 'manifest', 'service_worker',
}
OWNER_BLOCKED_ENDPOINTS = {
    'users', 'edit_user', 'delete_user', 'income_types', 'delete_income_type',
    'expense_types', 'delete_expense_type', 'settings_page',
}


@app.before_request
def enforce_role_access():
    if 'user_id' not in session:
        return  # login_required on individual views handles this
    role = session.get('role')
    endpoint = request.endpoint
    if endpoint is None:
        return

    if role == 'tenant' and endpoint not in TENANT_ALLOWED_ENDPOINTS:
        flash('Your account only has access to the Reports section.', 'error')
        return redirect(url_for('reports'))

    if role == 'owner':
        if endpoint in OWNER_BLOCKED_ENDPOINTS:
            flash("That section isn't available for your account.", 'error')
            return redirect(url_for('dashboard'))
        if request.method == 'POST':
            flash('Your account has view-only access.', 'error')
            return redirect(request.referrer or url_for('dashboard'))


def current_month():
    return date.today().strftime('%Y-%m')


def month_label(ym):
    try:
        return datetime.strptime(ym, '%Y-%m').strftime('%B %Y')
    except Exception:
        return ym


def lookup(records, rid, field='name', default='-'):
    for r in records:
        if r['id'] == rid:
            return r.get(field, default)
    return default


def to_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def get_settings():
    records = db.load('settings')
    if not records:
        records = [db.insert('settings', {
            'opening_balance': 0.0,
            'opening_balance_date': date.today().isoformat(),
            'opening_corpus_fund': 0.0,
            'opening_corpus_fund_date': date.today().isoformat(),
        })]
    return records[0]


def current_user_record():
    user_id = session.get('user_id')
    if not user_id:
        return None
    return db.get('users', user_id)


def current_user_flat():
    user = current_user_record()
    if not user or user.get('role') not in ('owner', 'tenant'):
        return None
    flat_id = user.get('flat_id')
    if not flat_id:
        return None
    return db.get('flats', flat_id)


def resident_due_summary(flat_id, ym):
    income_types = db.load('income_types')
    tx = [t for t in db.load('income_tx') if t.get('flat_id') == flat_id]
    for t in tx:
        t['type_name'] = lookup(income_types, t['income_type_id'])
    tx.sort(key=lambda x: (x.get('for_month') or '', x.get('paid_date') or ''), reverse=True)

    month_tx = [t for t in tx if t.get('for_month') == ym]
    unpaid_all = [t for t in tx if t.get('status') != 'paid']

    return {
        'month_total': sum(to_float(t['amount']) for t in month_tx),
        'month_paid': sum(to_float(t['amount']) for t in month_tx if t.get('status') == 'paid'),
        'month_unpaid': sum(to_float(t['amount']) for t in month_tx if t.get('status') != 'paid'),
        'outstanding_total': sum(to_float(t['amount']) for t in unpaid_all),
        'outstanding_count': len(unpaid_all),
        'recent_tx': tx[:6],
    }


# ---------------------------------------------------------------------------
# Core financial calculations
# ---------------------------------------------------------------------------

def overall_balance():
    """Cash-in-hand balance at this exact moment (all time), including the
    one-time opening balance recorded by the admin for pre-app history."""
    settings = get_settings()
    ob = to_float(settings.get('opening_balance'))
    ob_date = settings.get('opening_balance_date') or '0000-01-01'

    income_tx = db.load('income_tx')
    expense_tx = db.load('expense_tx')
    watchman = db.load('watchman_ledger')

    total_income = sum(to_float(t['amount']) for t in income_tx
                        if t.get('status') == 'paid' and (t.get('paid_date') or '') >= ob_date)
    total_expense = sum(to_float(t['amount']) for t in expense_tx if t['date'] >= ob_date)
    advances = sum(to_float(t['amount']) for t in watchman if t['type'] == 'advance' and t['date'] >= ob_date)
    recoveries = sum(to_float(t['amount']) for t in watchman if t['type'] == 'recovery' and t['date'] >= ob_date)
    watchman_outstanding = advances - recoveries

    return ob + total_income - total_expense - watchman_outstanding


def corpus_fund_balance():
    settings = get_settings()
    opening = to_float(settings.get('opening_corpus_fund'))
    log = db.load('corpus_fund_log')
    added = sum(to_float(e['amount']) for e in log if e['type'] == 'add')
    withdrawn = sum(to_float(e['amount']) for e in log if e['type'] == 'withdraw')
    return opening + added - withdrawn


def balance_before_month(ym):
    """Cash balance as of the last moment before the 1st of the given month,
    including the recorded opening balance."""
    settings = get_settings()
    ob = to_float(settings.get('opening_balance'))
    ob_date = settings.get('opening_balance_date') or '0000-01-01'

    income_tx = db.load('income_tx')
    expense_tx = db.load('expense_tx')
    watchman = db.load('watchman_ledger')

    total_income = sum(to_float(t['amount']) for t in income_tx
                        if t.get('status') == 'paid'
                        and ob_date <= (t.get('paid_date') or '9999-99') < f"{ym}-01")
    total_expense = sum(to_float(t['amount']) for t in expense_tx
                         if ob_date <= t['date'] < f"{ym}-01")
    advances = sum(to_float(t['amount']) for t in watchman
                   if t['type'] == 'advance' and ob_date <= t['date'] < f"{ym}-01")
    recoveries = sum(to_float(t['amount']) for t in watchman
                      if t['type'] == 'recovery' and ob_date <= t['date'] < f"{ym}-01")
    return ob + total_income - total_expense - (advances - recoveries)


def month_report(ym):
    income_tx = [t for t in db.load('income_tx')
                 if t.get('status') == 'paid' and str(t.get('paid_date', ''))[:7] == ym]
    expense_tx = [t for t in db.load('expense_tx') if t['date'][:7] == ym]
    income_types = db.load('income_types')
    expense_types = db.load('expense_types')
    flats = db.load('flats')

    income_by_type = {}
    for t in income_tx:
        name = lookup(income_types, t['income_type_id'])
        income_by_type[name] = income_by_type.get(name, 0) + to_float(t['amount'])

    expense_by_type = {}
    for t in expense_tx:
        name = lookup(expense_types, t['expense_type_id'])
        expense_by_type[name] = expense_by_type.get(name, 0) + to_float(t['amount'])

    total_income = sum(income_by_type.values())
    total_expense = sum(expense_by_type.values())
    opening = balance_before_month(ym)
    closing = opening + total_income - total_expense

    # unpaid dues for the month
    unpaid = [t for t in db.load('income_tx') if t.get('for_month') == ym and t.get('status') != 'paid']
    unpaid_by_flat = []
    for t in sorted(unpaid, key=lambda x: lookup(flats, x.get('flat_id'), 'flat_no', '')):
        unpaid_by_flat.append({
            'flat_id': t.get('flat_id'),
            'flat_no': lookup(flats, t.get('flat_id'), 'flat_no', '-'),
            'owner_name': lookup(flats, t.get('flat_id'), 'owner_name', ''),
            'amount': to_float(t.get('amount')),
            'type_name': lookup(income_types, t.get('income_type_id')),
            'remarks': t.get('remarks', ''),
        })

    return {
        'ym': ym, 'label': month_label(ym),
        'income_by_type': income_by_type, 'expense_by_type': expense_by_type,
        'total_income': total_income, 'total_expense': total_expense,
        'opening_balance': opening, 'closing_balance': closing,
        'net': total_income - total_expense,
        'income_tx': sorted(income_tx, key=lambda x: x.get('paid_date', '')),
        'expense_tx': sorted(expense_tx, key=lambda x: x['date']),
        'unpaid_dues': unpaid,
        'unpaid_by_flat': unpaid_by_flat,
        'unpaid_total': sum(to_float(t['amount']) for t in unpaid),
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        users = db.load('users')
        user = next((u for u in users if u['username'] == username), None)
        if user and check_password_hash(user['password_hash'], password):
            session.permanent = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['name'] = user.get('name', user['username'])
            flash(f"Welcome back, {user.get('name', user['username'])}!", 'success')
            if user['role'] == 'tenant':
                return redirect(url_for('reports'))
            return redirect(request.args.get('next') or url_for('dashboard'))
        flash('Invalid username or password.', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route('/')
@login_required
def dashboard():
    ym = current_month()
    report = month_report(ym)
    balance = overall_balance()
    flats = db.load('flats')
    active_flats = [f for f in flats if f.get('status', 'active') == 'active']
    watchman = db.load('watchman_ledger')
    advances = sum(to_float(t['amount']) for t in watchman if t['type'] == 'advance')
    recoveries = sum(to_float(t['amount']) for t in watchman if t['type'] == 'recovery')
    watchman_due = advances - recoveries
    recent_expenses = sorted(db.load('expense_tx'), key=lambda x: x['date'], reverse=True)[:5]
    expense_types = db.load('expense_types')
    for e in recent_expenses:
        e['type_name'] = lookup(expense_types, e['expense_type_id'])

    resident_flat = current_user_flat()
    resident_summary = resident_due_summary(resident_flat['id'], ym) if resident_flat else None

    return render_template('dashboard.html', balance=balance, report=report,
                            flat_count=len(active_flats), watchman_due=watchman_due,
                            recent_expenses=recent_expenses, ym=ym,
                            corpus=corpus_fund_balance(), resident_flat=resident_flat,
                            resident_summary=resident_summary)


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings_page():
    if request.method == 'POST':
        if session.get('role') not in ('admin', 'manager'):
            flash('Your account has view-only access.', 'error')
            return redirect(url_for('settings_page'))
        db.update('settings', get_settings()['id'], {
            'opening_balance': to_float(request.form.get('opening_balance')),
            'opening_balance_date': request.form.get('opening_balance_date') or date.today().isoformat(),
            'opening_corpus_fund': to_float(request.form.get('opening_corpus_fund')),
            'opening_corpus_fund_date': request.form.get('opening_corpus_fund_date') or date.today().isoformat(),
        })
        flash('Opening balance & corpus fund updated.', 'success')
        return redirect(url_for('settings_page'))
    return render_template('settings.html', settings=get_settings(),
                            balance=overall_balance(), corpus=corpus_fund_balance())


@app.route('/corpus-fund')
@login_required
def corpus_fund():
    log = sorted(db.load('corpus_fund_log'), key=lambda x: x['date'], reverse=True)
    return render_template('corpus_fund.html', log=log, balance=corpus_fund_balance(),
                            today=date.today().isoformat())


@app.route('/corpus-fund/add', methods=['POST'])
@login_required
def add_corpus_fund_entry():
    db.insert('corpus_fund_log', {
        'date': request.form.get('date', date.today().isoformat()),
        'type': request.form['type'],  # add | withdraw
        'amount': to_float(request.form.get('amount')),
        'remarks': request.form.get('remarks', '').strip(),
    })
    flash('Corpus fund entry saved.', 'success')
    return redirect(url_for('corpus_fund'))


@app.route('/corpus-fund/<int:entry_id>/delete', methods=['POST'])
@login_required
def delete_corpus_fund_entry(entry_id):
    db.delete('corpus_fund_log', entry_id)
    flash('Entry removed.', 'success')
    return redirect(url_for('corpus_fund'))


# ---------------------------------------------------------------------------
# Flats
# ---------------------------------------------------------------------------

@app.route('/flats', methods=['GET', 'POST'])
@login_required
def flats():
    if request.method == 'POST':
        db.insert('flats', {
            'flat_no': request.form['flat_no'].strip(),
            'owner_name': request.form.get('owner_name', '').strip(),
            'tenant_name': request.form.get('tenant_name', '').strip(),
            'contact': request.form.get('contact', '').strip(),
            'maintenance_amount': to_float(request.form.get('maintenance_amount')),
            'status': 'active',
            'created_at': datetime.now().isoformat(),
        })
        flash('Flat added.', 'success')
        return redirect(url_for('flats'))
    all_flats = sorted(db.load('flats'), key=lambda f: f['flat_no'])
    return render_template('flats.html', flats=all_flats)


@app.route('/flats/<int:flat_id>/edit', methods=['POST'])
@login_required
def edit_flat(flat_id):
    db.update('flats', flat_id, {
        'flat_no': request.form['flat_no'].strip(),
        'owner_name': request.form.get('owner_name', '').strip(),
        'tenant_name': request.form.get('tenant_name', '').strip(),
        'contact': request.form.get('contact', '').strip(),
        'maintenance_amount': to_float(request.form.get('maintenance_amount')),
        'status': request.form.get('status', 'active'),
    })
    flash('Flat updated.', 'success')
    return redirect(url_for('flats'))


@app.route('/flats/<int:flat_id>/delete', methods=['POST'])
@login_required
def delete_flat(flat_id):
    db.delete('flats', flat_id)
    flash('Flat removed.', 'success')
    return redirect(url_for('flats'))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@app.route('/users', methods=['GET', 'POST'])
@login_required
@admin_required
def users():
    if request.method == 'POST':
        username = request.form['username'].strip()
        if any(u['username'] == username for u in db.load('users')):
            flash('Username already exists.', 'error')
        else:
            role = request.form.get('role', 'manager')
            flat_id = request.form.get('flat_id')
            db.insert('users', {
                'username': username,
                'password_hash': generate_password_hash(request.form['password']),
                'name': request.form.get('name', '').strip() or username,
                'role': role,
                'flat_id': int(flat_id) if flat_id and role in ('owner', 'tenant') else None,
            })
            flash('User created.', 'success')
        return redirect(url_for('users'))
    flats_list = {f['id']: f['flat_no'] for f in db.load('flats')}
    all_users = db.load('users')
    for u in all_users:
        u['flat_no'] = flats_list.get(u.get('flat_id'))
    return render_template('users.html', users=all_users,
                            flats=sorted(db.load('flats'), key=lambda f: f['flat_no']))


@app.route('/users/<int:user_id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = db.get('users', user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('users'))

    username = request.form.get('username', '').strip()
    role = request.form.get('role', user.get('role', 'manager'))
    flat_id = request.form.get('flat_id')
    if not username:
        flash('Username is required.', 'error')
        return redirect(url_for('users'))

    existing = next((u for u in db.load('users') if u['username'] == username and u['id'] != user_id), None)
    if existing:
        flash('Username already exists.', 'error')
        return redirect(url_for('users'))

    updates = {
        'name': request.form.get('name', '').strip() or username,
        'username': username,
        'role': role,
        'flat_id': int(flat_id) if flat_id and role in ('owner', 'tenant') else None,
    }

    password = request.form.get('password', '')
    if password:
        updates['password_hash'] = generate_password_hash(password)

    db.update('users', user_id, updates)
    flash('User updated.', 'success')
    return redirect(url_for('users'))


@app.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    if user_id == session.get('user_id'):
        flash("You can't delete your own account while logged in.", 'error')
    else:
        db.delete('users', user_id)
        flash('User removed.', 'success')
    return redirect(url_for('users'))


# ---------------------------------------------------------------------------
# Income types / Expense types (master data)
# ---------------------------------------------------------------------------

@app.route('/income-types', methods=['GET', 'POST'])
@login_required
def income_types():
    if request.method == 'POST':
        db.insert('income_types', {
            'name': request.form['name'].strip(),
            'description': request.form.get('description', '').strip(),
        })
        flash('Income type added.', 'success')
        return redirect(url_for('income_types'))
    return render_template('income_types.html', income_types=db.load('income_types'))


@app.route('/income-types/<int:type_id>/delete', methods=['POST'])
@login_required
def delete_income_type(type_id):
    db.delete('income_types', type_id)
    flash('Income type removed.', 'success')
    return redirect(url_for('income_types'))


@app.route('/expense-types', methods=['GET', 'POST'])
@login_required
def expense_types():
    if request.method == 'POST':
        db.insert('expense_types', {
            'name': request.form['name'].strip(),
            'description': request.form.get('description', '').strip(),
            'recurring': request.form.get('recurring') == 'on',
            'default_amount': to_float(request.form.get('default_amount')),
        })
        flash('Expense type added.', 'success')
        return redirect(url_for('expense_types'))
    return render_template('expense_types.html', expense_types=db.load('expense_types'))


@app.route('/expense-types/<int:type_id>/delete', methods=['POST'])
@login_required
def delete_expense_type(type_id):
    db.delete('expense_types', type_id)
    flash('Expense type removed.', 'success')
    return redirect(url_for('expense_types'))


# ---------------------------------------------------------------------------
# Income (monthly maintenance credits)
# ---------------------------------------------------------------------------

@app.route('/income')
@login_required
def income():
    ym = request.args.get('month', current_month())
    flats_list = {f['id']: f for f in db.load('flats')}
    income_types_list = db.load('income_types')
    resident_flat = current_user_flat()
    tx = [t for t in db.load('income_tx') if t.get('for_month') == ym]
    if resident_flat:
        tx = [t for t in tx if t.get('flat_id') == resident_flat['id']]
    for t in tx:
        t['flat_no'] = flats_list.get(t['flat_id'], {}).get('flat_no', '-')
        t['type_name'] = lookup(income_types_list, t['income_type_id'])
    tx.sort(key=lambda x: x['flat_no'])
    total_paid = sum(to_float(t['amount']) for t in tx if t['status'] == 'paid')
    total_unpaid = sum(to_float(t['amount']) for t in tx if t['status'] != 'paid')
    visible_flats = [resident_flat] if resident_flat else sorted(db.load('flats'), key=lambda f: f['flat_no'])
    return render_template('income.html', tx=tx, ym=ym, month_label=month_label(ym),
                            flats=visible_flats,
                            income_types=income_types_list,
                            total_paid=total_paid, total_unpaid=total_unpaid,
                            today=date.today().isoformat(), resident_flat=resident_flat)


@app.route('/income/generate-month', methods=['POST'])
@login_required
def generate_month_income():
    ym = request.form['month']
    maintenance_type = next((t for t in db.load('income_types')
                              if t['name'] == 'Monthly Maintenance'), None)
    if not maintenance_type:
        maintenance_type = db.insert('income_types', {'name': 'Monthly Maintenance', 'description': ''})

    existing = {t['flat_id'] for t in db.load('income_tx')
                if t.get('for_month') == ym and t['income_type_id'] == maintenance_type['id']}
    created = 0
    for f in db.load('flats'):
        if f.get('status', 'active') != 'active' or f['id'] in existing:
            continue
        db.insert('income_tx', {
            'flat_id': f['id'],
            'income_type_id': maintenance_type['id'],
            'amount': to_float(f.get('maintenance_amount')),
            'for_month': ym,
            'status': 'unpaid',
            'paid_date': None,
            'remarks': 'Auto-generated monthly maintenance due',
        })
        created += 1
    flash(f'Generated {created} maintenance due entries for {month_label(ym)}.', 'success')
    return redirect(url_for('income', month=ym))


@app.route('/income/add', methods=['POST'])
@login_required
def add_income():
    ym = request.form.get('for_month', current_month())
    status = request.form.get('status', 'paid')
    db.insert('income_tx', {
        'flat_id': int(request.form['flat_id']),
        'income_type_id': int(request.form['income_type_id']),
        'amount': to_float(request.form.get('amount')),
        'for_month': ym,
        'status': status,
        'paid_date': request.form.get('paid_date') if status == 'paid' else None,
        'remarks': request.form.get('remarks', '').strip(),
    })
    flash('Income entry recorded.', 'success')
    return redirect(url_for('income', month=ym))


@app.route('/income/<int:tx_id>/mark-paid', methods=['POST'])
@login_required
def mark_income_paid(tx_id):
    db.update('income_tx', tx_id, {
        'status': 'paid',
        'paid_date': request.form.get('paid_date', date.today().isoformat()),
    })
    flash('Marked as paid.', 'success')
    return redirect(request.referrer or url_for('income'))


@app.route('/income/<int:tx_id>/mark-unpaid', methods=['POST'])
@login_required
def mark_income_unpaid(tx_id):
    db.update('income_tx', tx_id, {
        'status': 'unpaid',
        'paid_date': None,
    })
    flash('Marked as unpaid.', 'success')
    return redirect(request.referrer or url_for('income'))


@app.route('/income/<int:tx_id>/delete', methods=['POST'])
@login_required
def delete_income(tx_id):
    db.delete('income_tx', tx_id)
    flash('Income entry removed.', 'success')
    return redirect(request.referrer or url_for('income'))


# ---------------------------------------------------------------------------
# Expenses (daily tracking)
# ---------------------------------------------------------------------------

@app.route('/expenses')
@login_required
def expenses():
    ym = request.args.get('month', current_month())
    expense_types_list = db.load('expense_types')
    tx = [t for t in db.load('expense_tx') if t['date'][:7] == ym]
    for t in tx:
        t['type_name'] = lookup(expense_types_list, t['expense_type_id'])
    tx.sort(key=lambda x: x['date'], reverse=True)
    total = sum(to_float(t['amount']) for t in tx)
    return render_template('expenses.html', tx=tx, ym=ym, month_label=month_label(ym),
                            expense_types=expense_types_list, total=total,
                            today=date.today().isoformat())


@app.route('/expenses/add', methods=['POST'])
@login_required
def add_expense():
    tx_date = request.form.get('date', date.today().isoformat())
    db.insert('expense_tx', {
        'date': tx_date,
        'expense_type_id': int(request.form['expense_type_id']),
        'amount': to_float(request.form.get('amount')),
        'paid_to': request.form.get('paid_to', '').strip(),
        'remarks': request.form.get('remarks', '').strip(),
        'recorded_by': session.get('username'),
    })
    flash('Expense recorded.', 'success')
    return redirect(url_for('expenses', month=tx_date[:7]))


@app.route('/expenses/<int:tx_id>/delete', methods=['POST'])
@login_required
def delete_expense(tx_id):
    db.delete('expense_tx', tx_id)
    flash('Expense removed.', 'success')
    return redirect(request.referrer or url_for('expenses'))


# ---------------------------------------------------------------------------
# Watchman advances ledger
# ---------------------------------------------------------------------------

@app.route('/watchman')
@login_required
def watchman():
    ledger = sorted(db.load('watchman_ledger'), key=lambda x: x['date'], reverse=True)
    advances = sum(to_float(t['amount']) for t in ledger if t['type'] == 'advance')
    recoveries = sum(to_float(t['amount']) for t in ledger if t['type'] == 'recovery')
    return render_template('watchman.html', ledger=ledger, outstanding=advances - recoveries,
                            today=date.today().isoformat())


@app.route('/watchman/add', methods=['POST'])
@login_required
def add_watchman_entry():
    db.insert('watchman_ledger', {
        'date': request.form.get('date', date.today().isoformat()),
        'type': request.form['type'],  # advance | recovery
        'amount': to_float(request.form.get('amount')),
        'remarks': request.form.get('remarks', '').strip(),
    })
    flash('Watchman ledger entry saved.', 'success')
    return redirect(url_for('watchman'))


@app.route('/watchman/<int:entry_id>/delete', methods=['POST'])
@login_required
def delete_watchman_entry(entry_id):
    db.delete('watchman_ledger', entry_id)
    flash('Entry removed.', 'success')
    return redirect(url_for('watchman'))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@app.route('/reports')
@login_required
def reports():
    ym = request.args.get('month', current_month())
    report = month_report(ym)
    # last 6 months for quick trend
    months = []
    d = datetime.strptime(ym, '%Y-%m')
    for i in range(5, -1, -1):
        mm = (d.month - i - 1) % 12 + 1
        yy = d.year + (d.month - i - 1) // 12
        months.append(f"{yy:04d}-{mm:02d}")
    trend = []
    for m in months:
        r = month_report(m)
        trend.append({'ym': m, 'label': month_label(m), 'income': r['total_income'],
                       'expense': r['total_expense'], 'net': r['net']})
    resident_flat = current_user_flat()
    resident_summary = resident_due_summary(resident_flat['id'], ym) if resident_flat else None
    return render_template('reports.html', report=report, trend=trend, ym=ym,
                            current_balance=overall_balance(),
                            events=sorted(db.load('events'), key=lambda e: e.get('event_date', ''), reverse=True),
                            resident_flat=resident_flat, resident_summary=resident_summary)


@app.route('/reports/export/<ym>')
@login_required
def export_report(ym):
    report = month_report(ym)
    lines = [f"Apartment Maintenance - Income & Expense Report - {report['label']}", ""]
    lines.append(f"Opening Balance,{report['opening_balance']:.2f}")
    lines.append("")
    lines.append("INCOME")
    lines.append("Type,Amount")
    for k, v in report['income_by_type'].items():
        lines.append(f"{k},{v:.2f}")
    lines.append(f"Total Income,{report['total_income']:.2f}")
    lines.append("")
    lines.append("EXPENSES")
    lines.append("Type,Amount")
    for k, v in report['expense_by_type'].items():
        lines.append(f"{k},{v:.2f}")
    lines.append(f"Total Expense,{report['total_expense']:.2f}")
    lines.append("")
    lines.append(f"Net for Month,{report['net']:.2f}")
    lines.append(f"Closing Balance (Carried Forward),{report['closing_balance']:.2f}")
    lines.append("")
    lines.append(f"Unpaid Maintenance Dues,{report['unpaid_total']:.2f}")
    csv_data = "\n".join(lines)
    return Response(csv_data, mimetype='text/csv',
                     headers={'Content-Disposition': f'attachment; filename=report_{ym}.csv'})


# ---------------------------------------------------------------------------
# Events (separate module: contributions + expenses per event)
# ---------------------------------------------------------------------------

@app.route('/events', methods=['GET', 'POST'])
@login_required
def events():
    if request.method == 'POST':
        db.insert('events', {
            'name': request.form['name'].strip(),
            'event_date': request.form.get('event_date', date.today().isoformat()),
            'description': request.form.get('description', '').strip(),
            'status': 'active',
            'created_at': datetime.now().isoformat(),
        })
        flash('Event created.', 'success')
        return redirect(url_for('events'))
    all_events = sorted(db.load('events'), key=lambda e: e.get('event_date', ''), reverse=True)
    contributions = db.load('event_contributions')
    exp = db.load('event_expenses')
    for e in all_events:
        c = sum(to_float(x['amount']) for x in contributions if x['event_id'] == e['id'])
        x_total = sum(to_float(x['amount']) for x in exp if x['event_id'] == e['id'])
        e['total_contributions'] = c
        e['total_expenses'] = x_total
        e['balance'] = c - x_total
    return render_template('events.html', events=all_events)


@app.route('/events/<int:event_id>')
@login_required
def event_detail(event_id):
    event = db.get('events', event_id)
    if not event:
        flash('Event not found.', 'error')
        return redirect(url_for('events'))
    flats_list = db.load('flats')
    contributions = [c for c in db.load('event_contributions') if c['event_id'] == event_id]
    for c in contributions:
        c['flat_no'] = lookup(flats_list, c.get('flat_id'), 'flat_no', c.get('contributor_name', '-'))
    contributions.sort(key=lambda x: x['date'], reverse=True)
    exp = [x for x in db.load('event_expenses') if x['event_id'] == event_id]
    exp.sort(key=lambda x: x['date'], reverse=True)
    total_contrib = sum(to_float(c['amount']) for c in contributions)
    total_exp = sum(to_float(x['amount']) for x in exp)
    resident_flat = current_user_flat()
    resident_contributions = []
    if resident_flat:
        resident_contributions = [c for c in contributions if c.get('flat_id') == resident_flat['id']]
    return render_template('event_detail.html', event=event, contributions=contributions,
                            expenses=exp, total_contrib=total_contrib, total_exp=total_exp,
                            balance=total_contrib - total_exp, flats=sorted(flats_list, key=lambda f: f['flat_no']),
                            today=date.today().isoformat(), resident_flat=resident_flat,
                            resident_contributions=resident_contributions)


@app.route('/events/<int:event_id>/report')
@login_required
def event_report(event_id):
    event = db.get('events', event_id)
    if not event:
        flash('Event not found.', 'error')
        return redirect(url_for('events'))
    flats_list = db.load('flats')
    contributions = [c for c in db.load('event_contributions') if c['event_id'] == event_id]
    for c in contributions:
        c['flat_no'] = lookup(flats_list, c.get('flat_id'), 'flat_no', c.get('contributor_name') or '-')
    contributions.sort(key=lambda x: x['flat_no'])
    exp = [x for x in db.load('event_expenses') if x['event_id'] == event_id]
    exp.sort(key=lambda x: x['date'])
    total_contrib = sum(to_float(c['amount']) for c in contributions)
    total_exp = sum(to_float(x['amount']) for x in exp)
    resident_flat = current_user_flat()
    resident_contribution = None
    if resident_flat:
        resident_contribution = next((c for c in contributions if c.get('flat_id') == resident_flat['id']), None)

    # who has NOT contributed yet, for a complete collection picture
    contributed_flat_ids = {c.get('flat_id') for c in contributions}
    not_contributed = [f for f in flats_list
                        if f.get('status', 'active') == 'active' and f['id'] not in contributed_flat_ids]

    return render_template('event_report.html', event=event, contributions=contributions,
                            expenses=exp, total_contrib=total_contrib, total_exp=total_exp,
                            balance=total_contrib - total_exp,
                            not_contributed=sorted(not_contributed, key=lambda f: f['flat_no']),
                            resident_flat=resident_flat, resident_contribution=resident_contribution)


@app.route('/events/<int:event_id>/report/export')
@login_required
def export_event_report(event_id):
    event = db.get('events', event_id)
    if not event:
        flash('Event not found.', 'error')
        return redirect(url_for('events'))
    flats_list = db.load('flats')
    contributions = [c for c in db.load('event_contributions') if c['event_id'] == event_id]
    for c in contributions:
        c['flat_no'] = lookup(flats_list, c.get('flat_id'), 'flat_no', c.get('contributor_name') or '-')
    contributions.sort(key=lambda x: x['flat_no'])
    exp = [x for x in db.load('event_expenses') if x['event_id'] == event_id]
    exp.sort(key=lambda x: x['date'])
    total_contrib = sum(to_float(c['amount']) for c in contributions)
    total_exp = sum(to_float(x['amount']) for x in exp)

    lines = [f"Event Report - {event['name']} ({event.get('event_date', '')})", ""]
    lines.append("CONTRIBUTIONS")
    lines.append("Flat,Date,Amount,Remarks")
    for c in contributions:
        remarks = (c.get('remarks') or '').replace(',', ';')
        lines.append(f"{c['flat_no']},{c['date']},{c['amount']:.2f},{remarks}")
    lines.append(f"Total Collected,,{total_contrib:.2f},")
    lines.append("")
    lines.append("EXPENSES")
    lines.append("Date,Description,Amount,Paid To")
    for x in exp:
        desc = (x.get('description') or '').replace(',', ';')
        paid_to = (x.get('paid_to') or '').replace(',', ';')
        lines.append(f"{x['date']},{desc},{x['amount']:.2f},{paid_to}")
    lines.append(f"Total Spent,,{total_exp:.2f},")
    lines.append("")
    lines.append(f"Net Balance,,{(total_contrib - total_exp):.2f},")
    csv_data = "\n".join(lines)
    safe_name = event['name'].replace(' ', '_')
    return Response(csv_data, mimetype='text/csv',
                     headers={'Content-Disposition': f'attachment; filename=event_report_{safe_name}.csv'})


@app.route('/events/<int:event_id>/contribution', methods=['POST'])
@login_required
def add_event_contribution(event_id):
    flat_id = request.form.get('flat_id')
    if not flat_id:
        flash('Please select the flat contributing.', 'error')
        return redirect(url_for('event_detail', event_id=event_id))
    db.insert('event_contributions', {
        'event_id': event_id,
        'flat_id': int(flat_id),
        'amount': to_float(request.form.get('amount')),
        'date': request.form.get('date', date.today().isoformat()),
        'remarks': request.form.get('remarks', '').strip(),
    })
    flash('Contribution recorded.', 'success')
    return redirect(url_for('event_detail', event_id=event_id))


@app.route('/events/<int:event_id>/expense', methods=['POST'])
@login_required
def add_event_expense(event_id):
    db.insert('event_expenses', {
        'event_id': event_id,
        'date': request.form.get('date', date.today().isoformat()),
        'description': request.form.get('description', '').strip(),
        'amount': to_float(request.form.get('amount')),
        'paid_to': request.form.get('paid_to', '').strip(),
    })
    flash('Event expense recorded.', 'success')
    return redirect(url_for('event_detail', event_id=event_id))


@app.route('/events/<int:event_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_event(event_id):
    db.delete('events', event_id)
    for c in [c for c in db.load('event_contributions') if c['event_id'] == event_id]:
        db.delete('event_contributions', c['id'])
    for x in [x for x in db.load('event_expenses') if x['event_id'] == event_id]:
        db.delete('event_expenses', x['id'])
    flash('Event deleted.', 'success')
    return redirect(url_for('events'))


@app.route('/events/contribution/<int:c_id>/delete', methods=['POST'])
@login_required
def delete_event_contribution(c_id):
    c = db.get('event_contributions', c_id)
    db.delete('event_contributions', c_id)
    return redirect(url_for('event_detail', event_id=c['event_id']) if c else url_for('events'))


@app.route('/events/expense/<int:x_id>/delete', methods=['POST'])
@login_required
def delete_event_expense(x_id):
    x = db.get('event_expenses', x_id)
    db.delete('event_expenses', x_id)
    return redirect(url_for('event_detail', event_id=x['event_id']) if x else url_for('events'))


# ---------------------------------------------------------------------------
# PWA support
# ---------------------------------------------------------------------------

@app.route('/manifest.json')
def manifest():
    return jsonify({
        "name": "Sucasa Windgates - Apartment Manager",
        "short_name": "Sucasa Windgates",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#4f46e5",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    })


@app.route('/sw.js')
def service_worker():
    return Response("self.addEventListener('fetch', function(e){});",
                     mimetype='application/javascript')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
