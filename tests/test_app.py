import unittest
import uuid
from pathlib import Path
import shutil

from werkzeug.security import generate_password_hash

import db
import app as app_module
from seed import seed


class ResidentAccessTests(unittest.TestCase):
    def setUp(self):
        self.original_data_dir = db.DATA_DIR
        self.temp_dir = Path(__file__).resolve().parent.parent / '.tmp_tests' / str(uuid.uuid4())
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        db.DATA_DIR = str(self.temp_dir)
        seed()

        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

        flat_one = db.insert('flats', {
            'flat_no': 'A-101',
            'owner_name': 'Owner One',
            'tenant_name': 'Tenant One',
            'contact': '1111111111',
            'maintenance_amount': 1500.0,
            'status': 'active',
            'created_at': '2026-07-01T09:00:00',
        })
        flat_two = db.insert('flats', {
            'flat_no': 'B-202',
            'owner_name': 'Owner Two',
            'tenant_name': 'Tenant Two',
            'contact': '2222222222',
            'maintenance_amount': 2000.0,
            'status': 'active',
            'created_at': '2026-07-01T09:00:00',
        })
        self.flat_one_id = flat_one['id']
        self.flat_two_id = flat_two['id']

        db.insert('users', {
            'username': 'owner1',
            'password_hash': generate_password_hash('secret123'),
            'name': 'Owner One',
            'role': 'owner',
            'flat_id': self.flat_one_id,
        })
        db.insert('users', {
            'username': 'tenant1',
            'password_hash': generate_password_hash('secret123'),
            'name': 'Tenant One',
            'role': 'tenant',
            'flat_id': self.flat_one_id,
        })
        self.manager_user = db.insert('users', {
            'username': 'manager1',
            'password_hash': generate_password_hash('secret123'),
            'name': 'Manager One',
            'role': 'manager',
            'flat_id': None,
        })

        maintenance_type = next(t for t in db.load('income_types') if t['name'] == 'Monthly Maintenance')
        db.insert('income_tx', {
            'flat_id': self.flat_one_id,
            'income_type_id': maintenance_type['id'],
            'amount': 1500.0,
            'for_month': '2026-07',
            'status': 'unpaid',
            'paid_date': None,
            'remarks': 'Flat one due',
        })
        db.insert('income_tx', {
            'flat_id': self.flat_two_id,
            'income_type_id': maintenance_type['id'],
            'amount': 2000.0,
            'for_month': '2026-07',
            'status': 'paid',
            'paid_date': '2026-07-02',
            'remarks': 'Flat two paid',
        })
        self.event = db.insert('events', {
            'name': 'Ganesh Festival',
            'event_date': '2026-07-05',
            'description': 'Community event',
            'status': 'active',
            'created_at': '2026-07-01T10:00:00',
        })
        db.insert('event_contributions', {
            'event_id': self.event['id'],
            'flat_id': self.flat_one_id,
            'amount': 500.0,
            'date': '2026-07-04',
            'remarks': 'Advance contribution',
        })
        db.insert('event_expenses', {
            'event_id': self.event['id'],
            'description': 'Decoration',
            'amount': 200.0,
            'date': '2026-07-05',
            'paid_to': 'Vendor',
        })

    def tearDown(self):
        db.DATA_DIR = self.original_data_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def login(self, username, password):
        return self.client.post('/login', data={
            'username': username,
            'password': password,
        }, follow_redirects=True)

    def test_owner_income_page_is_limited_to_assigned_flat(self):
        response = self.login('owner1', 'secret123')
        self.assertEqual(response.status_code, 200)

        response = self.client.get('/income?month=2026-07')
        text = response.get_data(as_text=True)

        self.assertIn('Showing Flat A-101 Only', text)
        self.assertIn('Flat A-101', text)
        self.assertNotIn('Flat B-202', text)

    def test_admin_income_page_still_shows_all_flats(self):
        self.login('admin', 'admin123')
        response = self.client.get('/income?month=2026-07')
        text = response.get_data(as_text=True)

        self.assertIn('Flat A-101', text)
        self.assertIn('Flat B-202', text)

    def test_tenant_is_redirected_away_from_income(self):
        self.login('tenant1', 'secret123')
        response = self.client.get('/income', follow_redirects=True)
        text = response.get_data(as_text=True)

        self.assertIn('Your account only has access to the Reports section.', text)
        self.assertIn('Your Flat - A-101', text)

    def test_reports_page_shows_flat_summary_for_resident(self):
        self.login('tenant1', 'secret123')
        response = self.client.get('/reports?month=2026-07')
        text = response.get_data(as_text=True)

        self.assertIn('Your Flat - A-101', text)
        self.assertIn('Recent Flat Entries', text)
        self.assertIn('Total outstanding dues', text)

    def test_first_login_page_load_has_no_expired_session_message(self):
        response = self.client.get('/login')
        text = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Your session has expired. Please log in again.', text)

    def test_reports_page_lists_flats_yet_to_pay_maintenance(self):
        self.login('admin', 'admin123')
        response = self.client.get('/reports?month=2026-07')
        text = response.get_data(as_text=True)

        self.assertIn('Flats Yet to Pay Maintenance', text)
        self.assertIn('Flat A-101', text)
        self.assertNotIn('Flat B-202</div>', text)

    def test_admin_can_edit_user(self):
        self.login('admin', 'admin123')
        response = self.client.post(f'/users/{self.manager_user["id"]}/edit', data={
            'name': 'Manager Updated',
            'username': 'manager_renamed',
            'role': 'tenant',
            'flat_id': str(self.flat_two_id),
            'password': '',
        }, follow_redirects=True)
        text = response.get_data(as_text=True)
        updated = db.get('users', self.manager_user['id'])

        self.assertIn('User updated.', text)
        self.assertEqual(updated['name'], 'Manager Updated')
        self.assertEqual(updated['username'], 'manager_renamed')
        self.assertEqual(updated['role'], 'tenant')
        self.assertEqual(updated['flat_id'], self.flat_two_id)

    def test_monthly_report_pdf_download(self):
        self.login('admin', 'admin123')
        response = self.client.get('/reports/export/2026-07/pdf')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertIn('report_2026-07.pdf', response.headers.get('Content-Disposition', ''))
        self.assertTrue(response.data.startswith(b'%PDF-1.4'))

    def test_event_report_pdf_download(self):
        self.login('admin', 'admin123')
        response = self.client.get(f'/events/{self.event["id"]}/report/export/pdf')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertIn('event_report_Ganesh_Festival.pdf', response.headers.get('Content-Disposition', ''))
        self.assertTrue(response.data.startswith(b'%PDF-1.4'))


if __name__ == '__main__':
    unittest.main()
