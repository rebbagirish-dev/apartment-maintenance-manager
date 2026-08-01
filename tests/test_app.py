import unittest
import uuid
from io import BytesIO
from pathlib import Path
import shutil

from werkzeug.security import generate_password_hash, check_password_hash

import db
import app as app_module
from seed import (
    seed,
    ensure_admin_user,
    DEFAULT_ADMIN_USERNAME,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_NAME,
)


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
        self.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
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

    def test_login_page_prefills_remembered_credentials_from_cookies(self):
        self.client.set_cookie('remember_username', DEFAULT_ADMIN_USERNAME)
        self.client.set_cookie('remember_password', DEFAULT_ADMIN_PASSWORD)
        response = self.client.get('/login')
        text = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'value="{DEFAULT_ADMIN_USERNAME}"', text)
        self.assertIn(f'value="{DEFAULT_ADMIN_PASSWORD}"', text)
        self.assertIn('Remember credentials on this device', text)
        self.assertIn('checked', text)

    def test_reports_page_lists_flats_yet_to_pay_maintenance(self):
        self.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        response = self.client.get('/reports?month=2026-07')
        text = response.get_data(as_text=True)

        self.assertIn('Income and Expenditure Statement', text)
        self.assertIn('Excess of Income over Expenditure', text)
        self.assertIn('Flats Yet to Pay Maintenance', text)
        self.assertIn('Flat A-101', text)
        self.assertNotIn('Flat B-202</div>', text)

    def test_admin_can_edit_user(self):
        self.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
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

    def test_admin_can_create_task(self):
        self.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        response = self.client.post('/tasks', data={
            'name': 'Lift inspection',
            'description': 'Coordinate vendor visit',
            'owner': 'Manager One',
            'deadline': '2026-07-20',
            'progress': 'In Progress',
            'priority': 'High',
            'created_date': '2026-07-07',
            'completion_notes': '',
        }, follow_redirects=True)
        text = response.get_data(as_text=True)
        tasks = db.load('tasks')

        self.assertIn('Task created.', text)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['name'], 'Lift inspection')

    def test_login_sets_remembered_credential_cookies(self):
        response = self.client.post('/login', data={
            'username': DEFAULT_ADMIN_USERNAME,
            'password': DEFAULT_ADMIN_PASSWORD,
            'remember_credentials': 'on',
        })
        set_cookie_headers = response.headers.getlist('Set-Cookie')
        cookie_text = ' '.join(set_cookie_headers)

        self.assertEqual(response.status_code, 302)
        self.assertIn('remember_username=', cookie_text)
        self.assertIn('remember_password=', cookie_text)

    def test_reports_page_shows_task_progress_report(self):
        db.insert('tasks', {
            'name': 'Water tank cleaning',
            'description': 'Schedule monthly cleaning',
            'owner': 'Committee',
            'deadline': '2026-07-12',
            'progress': 'Completed',
            'priority': 'Medium',
            'created_date': '2026-07-01',
            'completion_notes': 'Completed successfully',
            'created_by': 'admin',
            'updated_at': '2026-07-07T10:00:00',
        })
        self.login('tenant1', 'secret123')
        response = self.client.get('/reports?month=2026-07')
        text = response.get_data(as_text=True)

        self.assertIn('Task Progress Report', text)
        self.assertIn('Water tank cleaning', text)
        self.assertIn('Completed', text)

    def test_tenant_can_create_forum_thread(self):
        self.login('tenant1', 'secret123')
        response = self.client.post('/forum', data={
            'title': 'Water timing',
            'body': 'Can we discuss morning water timings?',
        }, follow_redirects=True)
        text = response.get_data(as_text=True)
        threads = db.load('forum_threads')

        self.assertIn('Discussion posted.', text)
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]['title'], 'Water timing')
        self.assertEqual(threads[0]['author_username'], 'tenant1')

    def test_owner_can_reply_in_forum_thread(self):
        thread = db.insert('forum_threads', {
            'title': 'Lift vibration',
            'body': 'Noticed vibration yesterday.',
            'author_username': 'tenant1',
            'author_name': 'Tenant One',
            'created_at': '2026-08-01T08:00:00',
            'last_activity_at': '2026-08-01T08:00:00',
        })
        self.login('owner1', 'secret123')
        response = self.client.post(f'/forum/{thread["id"]}/reply', data={
            'body': 'I also observed this near 7 PM.',
        }, follow_redirects=True)
        text = response.get_data(as_text=True)
        replies = db.load('forum_replies')
        updated_thread = db.get('forum_threads', thread['id'])

        self.assertIn('Reply added.', text)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]['thread_id'], thread['id'])
        self.assertEqual(replies[0]['author_username'], 'owner1')
        self.assertNotEqual(updated_thread['last_activity_at'], '2026-08-01T08:00:00')

    def test_tasks_page_filters_by_status(self):
        db.insert('tasks', {
            'name': 'Lift repair',
            'description': '',
            'owner': 'Manager',
            'deadline': '2026-07-12',
            'progress': 'In Progress',
            'priority': 'High',
            'created_date': '2026-07-01',
            'completion_notes': '',
            'created_by': 'admin',
            'updated_at': '2026-07-07T10:00:00',
        })
        db.insert('tasks', {
            'name': 'Paint touch-up',
            'description': '',
            'owner': 'Vendor',
            'deadline': '',
            'progress': 'Completed',
            'priority': 'Low',
            'created_date': '2026-07-02',
            'completion_notes': '',
            'created_by': 'admin',
            'updated_at': '2026-07-07T10:00:00',
        })
        self.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        response = self.client.get('/tasks?status=In+Progress')
        text = response.get_data(as_text=True)

        self.assertIn('Lift repair', text)
        self.assertNotIn('Paint touch-up', text)

    def test_task_report_csv_export(self):
        db.insert('tasks', {
            'name': 'Generator service',
            'description': 'Monthly schedule',
            'owner': 'Technician',
            'deadline': '2026-07-15',
            'progress': 'Not Started',
            'priority': 'Medium',
            'created_date': '2026-07-03',
            'completion_notes': '',
            'created_by': 'admin',
            'updated_at': '2026-07-07T10:00:00',
        })
        self.login('tenant1', 'secret123')
        response = self.client.get('/reports/tasks/export/csv')
        text = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'text/csv')
        self.assertIn('Task Progress Report', text)
        self.assertIn('Generator service', text)

    def test_dashboard_shows_task_summary(self):
        db.insert('tasks', {
            'name': 'Intercom follow-up',
            'description': '',
            'owner': 'Committee',
            'deadline': '2026-07-14',
            'progress': 'At Completion',
            'priority': 'Medium',
            'created_date': '2026-07-04',
            'completion_notes': '',
            'created_by': 'admin',
            'updated_at': '2026-07-07T10:00:00',
        })
        self.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        response = self.client.get('/')
        text = response.get_data(as_text=True)

        self.assertIn('Task Summary', text)
        self.assertIn('Intercom follow-up', text)

    def test_dashboard_shows_forum_glance(self):
        db.insert('forum_threads', {
            'title': 'Generator noise',
            'body': 'Has anyone noticed increased generator noise?',
            'author_username': 'tenant1',
            'author_name': 'Tenant One',
            'created_at': '2026-08-01T09:00:00',
            'last_activity_at': '2026-08-01T09:00:00',
        })
        self.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        response = self.client.get('/')
        text = response.get_data(as_text=True)

        self.assertIn('Forum Glance', text)
        self.assertIn('Generator noise', text)
        self.assertIn('Awaiting Replies', text)

    def test_dashboard_recent_expenses_only_shows_current_month(self):
        expense_type = db.load('expense_types')[0]
        db.insert('expense_tx', {
            'date': '2026-08-01',
            'expense_type_id': expense_type['id'],
            'amount': 900.0,
            'paid_to': 'August Vendor',
            'remarks': '',
            'recorded_by': 'admin',
        })
        db.insert('expense_tx', {
            'date': '2026-07-31',
            'expense_type_id': expense_type['id'],
            'amount': 700.0,
            'paid_to': 'July Vendor',
            'remarks': '',
            'recorded_by': 'admin',
        })

        self.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        response = self.client.get('/')
        text = response.get_data(as_text=True)

        self.assertIn('August Vendor', text)
        self.assertNotIn('July Vendor', text)

    def test_dashboard_month_filter_shows_selected_month_data(self):
        expense_type = db.load('expense_types')[0]
        maintenance_type = next(t for t in db.load('income_types') if t['name'] == 'Monthly Maintenance')
        db.insert('income_tx', {
            'flat_id': self.flat_one_id,
            'income_type_id': maintenance_type['id'],
            'amount': 1500.0,
            'for_month': '2026-08',
            'status': 'paid',
            'paid_date': '2026-08-01',
            'remarks': 'August payment',
        })
        db.insert('expense_tx', {
            'date': '2026-08-01',
            'expense_type_id': expense_type['id'],
            'amount': 300.0,
            'paid_to': 'August Vendor',
            'remarks': '',
            'recorded_by': 'admin',
        })

        self.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        response = self.client.get('/?month=2026-07')
        text = response.get_data(as_text=True)

        self.assertIn('July 2026', text)
        self.assertIn('Flat B-202', self.client.get('/income?month=2026-07').get_data(as_text=True))
        self.assertNotIn('August Vendor', text)

    def test_admin_can_upload_document(self):
        self.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        response = self.client.post('/documents', data={
            'category': 'Lift Servicing Receipts',
            'custom_category': '',
            'title': 'July Lift AMC',
            'description': 'Vendor receipt for July service',
            'document': (BytesIO(b'fake pdf data'), 'lift_receipt.pdf'),
        }, content_type='multipart/form-data', follow_redirects=True)
        text = response.get_data(as_text=True)
        documents = db.load('documents')

        self.assertIn('Document uploaded.', text)
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]['category'], 'Lift Servicing Receipts')
        self.assertEqual(documents[0]['title'], 'July Lift AMC')
        stored_path = self.temp_dir / 'documents_store' / documents[0]['stored_filename']
        self.assertTrue(stored_path.exists())

    def test_admin_can_add_document_category(self):
        self.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        response = self.client.post('/document-categories', data={
            'name': 'Fire Safety AMC',
        }, follow_redirects=True)
        text = response.get_data(as_text=True)
        categories = db.load('document_categories')

        self.assertIn('Document category added.', text)
        self.assertTrue(any(c['name'] == 'Fire Safety AMC' for c in categories))

    def test_tenant_can_view_document_library(self):
        db.insert('documents', {
            'category': 'Generator Servicing Receipts',
            'title': 'Generator AMC Invoice',
            'description': 'Quarterly inspection invoice',
            'original_filename': 'generator_invoice.pdf',
            'stored_filename': 'generator_invoice.pdf',
            'content_type': 'application/pdf',
            'size_bytes': 1024,
            'uploaded_by': 'admin',
            'uploaded_at': '2026-07-20T10:30:00',
        })
        documents_dir = self.temp_dir / 'documents_store'
        documents_dir.mkdir(exist_ok=True)
        (documents_dir / 'generator_invoice.pdf').write_bytes(b'pdf data')

        self.login('tenant1', 'secret123')
        response = self.client.get('/documents')
        text = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Document Library', text)
        self.assertIn('Generator Servicing Receipts', text)
        self.assertIn('Generator AMC Invoice', text)

    def test_tenant_can_view_forum(self):
        db.insert('forum_threads', {
            'title': 'Parking slots',
            'body': 'Can we repaint slot numbers?',
            'author_username': 'admin',
            'author_name': 'Administrator',
            'created_at': '2026-08-01T09:00:00',
            'last_activity_at': '2026-08-01T09:00:00',
        })
        self.login('tenant1', 'secret123')
        response = self.client.get('/forum')
        text = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Community Forum', text)
        self.assertIn('Parking slots', text)

    def test_manager_cannot_add_document_category(self):
        self.login('manager1', 'secret123')
        response = self.client.post('/document-categories', data={
            'name': 'Restricted Category',
        }, follow_redirects=True)
        text = response.get_data(as_text=True)

        self.assertIn('Only admins can manage document categories.', text)
        self.assertFalse(any(c['name'] == 'Restricted Category' for c in db.load('document_categories')))

    def test_seed_recreates_admin_when_missing(self):
        db.save('users', [])
        seed()
        users = db.load('users')

        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]['username'], DEFAULT_ADMIN_USERNAME)
        self.assertEqual(users[0]['role'], 'admin')
        self.assertEqual(users[0]['name'], DEFAULT_ADMIN_NAME)
        self.assertTrue(check_password_hash(users[0]['password_hash'], DEFAULT_ADMIN_PASSWORD))

    def test_seed_syncs_hardcoded_admin_credentials(self):
        db.save('users', [{
            'id': 1,
            'username': DEFAULT_ADMIN_USERNAME,
            'password_hash': generate_password_hash('wrong-password'),
            'name': 'Wrong Name',
            'role': 'manager',
        }])
        ensure_admin_user()
        user = next((u for u in db.load('users') if u['username'] == DEFAULT_ADMIN_USERNAME), None)

        self.assertIsNotNone(user)
        self.assertEqual(user['role'], 'admin')
        self.assertEqual(user['name'], DEFAULT_ADMIN_NAME)
        self.assertTrue(check_password_hash(user['password_hash'], DEFAULT_ADMIN_PASSWORD))

    def test_only_admin_can_delete_document(self):
        document = db.insert('documents', {
            'category': 'AMC Invoices',
            'title': 'Fire Safety AMC',
            'description': '',
            'original_filename': 'fire_amc.pdf',
            'stored_filename': 'fire_amc.pdf',
            'content_type': 'application/pdf',
            'size_bytes': 2048,
            'uploaded_by': 'admin',
            'uploaded_at': '2026-07-21T09:00:00',
        })
        documents_dir = self.temp_dir / 'documents_store'
        documents_dir.mkdir(exist_ok=True)
        (documents_dir / 'fire_amc.pdf').write_bytes(b'pdf data')

        self.login('manager1', 'secret123')
        response = self.client.post(f'/documents/{document["id"]}/delete', follow_redirects=True)
        text = response.get_data(as_text=True)

        self.assertIn('Only admins can access that.', text)
        self.assertIsNotNone(db.get('documents', document['id']))
        self.assertTrue((documents_dir / 'fire_amc.pdf').exists())

    def test_monthly_report_pdf_download(self):
        self.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        response = self.client.get('/reports/export/2026-07/pdf')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertIn('report_2026-07.pdf', response.headers.get('Content-Disposition', ''))
        self.assertTrue(response.data.startswith(b'%PDF-1.4'))
        self.assertIn(b'SUCASA WINDGATES', response.data)
        self.assertIn(b'Income and Expenditure Statement', response.data)

    def test_event_report_pdf_download(self):
        self.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        response = self.client.get(f'/events/{self.event["id"]}/report/export/pdf')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertIn('event_report_Ganesh_Festival.pdf', response.headers.get('Content-Disposition', ''))
        self.assertTrue(response.data.startswith(b'%PDF-1.4'))
        self.assertIn(b'SUCASA WINDGATES', response.data)


if __name__ == '__main__':
    unittest.main()
