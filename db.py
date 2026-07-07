"""
Simple text-based (JSON file) database layer.
Every 'collection' is a .json file inside /data containing a list of dict records.
No external DB server needed - fully file based, human-readable, easy to back up.
"""
import json
import os
import threading

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
_lock = threading.Lock()

COLLECTIONS = [
    'users', 'flats', 'income_types', 'expense_types',
    'income_tx', 'expense_tx', 'watchman_ledger',
    'monthly_summary', 'events', 'event_contributions', 'event_expenses',
    'settings', 'corpus_fund_log', 'tasks'
]


def _path(name):
    return os.path.join(DATA_DIR, f"{name}.json")


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    for name in COLLECTIONS:
        p = _path(name)
        if not os.path.exists(p):
            with open(p, 'w') as f:
                json.dump([], f)


def load(name):
    p = _path(name)
    if not os.path.exists(p):
        return []
    with _lock:
        with open(p, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []


def save(name, records):
    p = _path(name)
    with _lock:
        with open(p, 'w') as f:
            json.dump(records, f, indent=2, default=str)


def next_id(records):
    if not records:
        return 1
    return max(r['id'] for r in records) + 1


def insert(name, record):
    records = load(name)
    record['id'] = next_id(records)
    records.append(record)
    save(name, records)
    return record


def update(name, record_id, updates):
    records = load(name)
    updated = None
    for r in records:
        if r['id'] == record_id:
            r.update(updates)
            updated = r
    save(name, records)
    return updated


def delete(name, record_id):
    records = load(name)
    records = [r for r in records if r['id'] != record_id]
    save(name, records)


def get(name, record_id):
    for r in load(name):
        if r['id'] == record_id:
            return r
    return None
