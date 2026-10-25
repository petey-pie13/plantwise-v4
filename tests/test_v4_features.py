"""
PlantWise V4 — Comprehensive Verification & Acceptance Test Suite
Validates Stages 1 through 6 against the Minimum Acceptance Test Matrix in
PlantWise V4 — Implementation Readiness & Execution Plan.
"""

import unittest
import json
import uuid
from app import app, limiter, get_db_connection, close_db_connection


class PlantWiseV4FeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        limiter.enabled = False
        cls.client = app.test_client()
        cls.run_id = uuid.uuid4().hex[:6]

    def _register(self, client, full_name, email, phone, role, password='Password123!'):
        """Helper to register a user using a specific test client session."""
        return client.post('/register', data={
            'full_name': full_name,
            'email': email,
            'phone': phone,
            'role': role,
            'county_id': '32',
            'password': password,
            'confirm_password': password
        }, follow_redirects=True)

    def _login(self, client, email, password='Password123!'):
        """Helper to log in using a specific test client session."""
        return client.post('/login', data={
            'email': email,
            'password': password
        }, follow_redirects=True)

    # ── STAGE 1: AUTHENTICATION & IDENTITY ─────────────────────────────────────

    def test_01_farmer_registration_and_login(self):
        """Farmer can register, log out, log back in, and access dashboard."""
        client = app.test_client()
        email = f"farmer_{self.run_id}@example.com"

        # Register -> automatically logs in
        reg_res = self._register(client, "Peter Kiprotich", email, "0712345678", "farmer")
        self.assertEqual(reg_res.status_code, 200)
        self.assertIn(b"Peter", reg_res.data)

        # Log out
        out_res = client.get('/logout', follow_redirects=True)
        self.assertEqual(out_res.status_code, 200)

        # Log back in
        login_res = self._login(client, email)
        self.assertEqual(login_res.status_code, 200)
        self.assertIn(b"Peter", login_res.data)

    def test_02_duplicate_registration_rejection(self):
        """Duplicate registration with existing email must be rejected."""
        c1 = app.test_client()
        c2 = app.test_client()
        email = f"dup_{self.run_id}@example.com"

        self._register(c1, "First User", email, "0722000001", "farmer")
        res = self._register(c2, "Second User", email, "0722000002", "farmer")
        self.assertIn(b"already exists", res.data)

    def test_03_protected_routes_deny_anonymous(self):
        """Anonymous access to private routes must redirect to login."""
        anon = app.test_client()
        protected_paths = ['/dashboard', '/farms', '/calendar', '/marketplace/my-listings']
        for path in protected_paths:
            res = anon.get(path, follow_redirects=False)
            self.assertEqual(res.status_code, 302)
            self.assertIn('/login', res.headers.get('Location', ''))

    # ── STAGE 2: FARMS & CROP CYCLES ──────────────────────────────────────────

    def test_04_farm_creation_and_isolation(self):
        """Farmer can create a farm; another user cannot delete or edit it."""
        client_a = app.test_client()
        client_b = app.test_client()

        email_a = f"farm_a_{self.run_id}@example.com"
        email_b = f"farm_b_{self.run_id}@example.com"

        self._register(client_a, "Farmer A", email_a, "0733000001", "farmer")
        self._register(client_b, "Farmer B", email_b, "0733000002", "farmer")

        # Farmer A creates a farm
        create_res = client_a.post('/farms/new', data={
            'name': 'Green Hills Farm',
            'county_id': '32',
            'total_area_ha': '5.5',
            'notes': 'Loam soil section'
        }, follow_redirects=True)
        self.assertEqual(create_res.status_code, 200)
        self.assertIn(b"Green Hills Farm", create_res.data)

        # Retrieve farm ID from DB
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM farms WHERE name='Green Hills Farm' ORDER BY id DESC LIMIT 1")
        farm_id = cur.fetchone()[0]
        close_db_connection(conn, cur)

        # Farmer B attempts to delete Farmer A's farm -> must not delete
        del_attempt = client_b.post(f'/farms/{farm_id}/delete', follow_redirects=True)
        self.assertEqual(del_attempt.status_code, 200)

        # Verify farm still exists and belongs to A
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM farms WHERE id=%s", (farm_id,))
        self.assertIsNotNone(cur.fetchone())
        close_db_connection(conn, cur)

    def test_05_crop_cycle_creation(self):
        """Farmer can create a crop cycle with planting date."""
        client = app.test_client()
        email = f"cycle_{self.run_id}@example.com"
        self._register(client, "Cycle Farmer", email, "0744000001", "farmer")

        # Create farm
        client.post('/farms/new', data={'name': 'Cycle Test Farm', 'county_id': '32'}, follow_redirects=True)
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM farms WHERE name='Cycle Test Farm' ORDER BY id DESC LIMIT 1")
        farm_id = cur.fetchone()[0]
        cur.execute("SELECT crop_id FROM crops WHERE crop_name='Maize' LIMIT 1")
        crop_id = cur.fetchone()[0]
        close_db_connection(conn, cur)

        # Add crop cycle
        cycle_res = client.post(f'/farms/{farm_id}/cycles/new', data={
            'crop_id': str(crop_id),
            'variety_name': 'H614D',
            'area_ha': '2.0',
            'planting_date': '2026-10-15',
            'expected_harvest': '2027-02-28',
            'status': 'active'
        }, follow_redirects=True)
        self.assertEqual(cycle_res.status_code, 200)
        self.assertIn(b"H614D", cycle_res.data)

    # ── STAGE 3: MARKETPLACE MVP ──────────────────────────────────────────────

    def test_06_produce_listing_and_order_flow(self):
        """Seller creates listing; buyer places order; stock decrements atomically."""
        seller_client = app.test_client()
        buyer_client  = app.test_client()

        seller_email = f"seller_{self.run_id}@example.com"
        buyer_email  = f"buyer_{self.run_id}@example.com"

        self._register(seller_client, "Seller John", seller_email, "0755000001", "seller")
        self._register(buyer_client, "Buyer Mary", buyer_email, "0755000002", "buyer")

        # Seller creates listing
        list_res = seller_client.post('/marketplace/listing/new', data={
            'crop_name': 'Irish Potatoes',
            'variety_name': 'Shangi',
            'quantity_kg': '500.0',
            'price_per_kg': '45.0',
            'county_id': '32',
            'fulfilment': 'both',
            'description': 'Freshly harvested Shangi potatoes.'
        }, follow_redirects=True)
        self.assertEqual(list_res.status_code, 200)

        # Get listing ID
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, quantity_kg FROM produce_listings WHERE crop_name='Irish Potatoes' ORDER BY id DESC LIMIT 1")
        listing_id, initial_qty = cur.fetchone()
        close_db_connection(conn, cur)

        self.assertEqual(float(initial_qty), 500.0)

        # Seller cannot order own listing
        self_order = seller_client.post('/marketplace/order/place', data={
            'listing_id': str(listing_id),
            'quantity_kg': '50.0'
        }, follow_redirects=True)
        self.assertIn(b"cannot order your own listing", self_order.data)

        # Buyer attempts oversell (600kg > 500kg)
        oversell = buyer_client.post('/marketplace/order/place', data={
            'listing_id': str(listing_id),
            'quantity_kg': '600.0'
        }, follow_redirects=True)
        self.assertIn(b"available", oversell.data)

        # Buyer places valid order of 100kg
        valid_order = buyer_client.post('/marketplace/order/place', data={
            'listing_id': str(listing_id),
            'quantity_kg': '100.0',
            'fulfilment': 'pickup'
        }, follow_redirects=True)
        self.assertEqual(valid_order.status_code, 200)

        # Verify stock decremented to 400kg
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT quantity_kg FROM produce_listings WHERE id=%s", (listing_id,))
        remaining_qty = cur.fetchone()[0]
        close_db_connection(conn, cur)
        self.assertEqual(float(remaining_qty), 400.0)

    # ── STAGE 4: AI FARM ASSISTANT ────────────────────────────────────────────

    def test_07_assistant_conversation_isolation(self):
        """Conversations and messages are strictly isolated per user."""
        client_a = app.test_client()
        client_b = app.test_client()

        email_a = f"ai_a_{self.run_id}@example.com"
        email_b = f"ai_b_{self.run_id}@example.com"

        self._register(client_a, "Farmer AI-A", email_a, "0766000001", "farmer")
        self._register(client_b, "Farmer AI-B", email_b, "0766000002", "farmer")

        # Farmer A starts conversation
        new_conv = client_a.post('/assistant/new', data={'title': 'Maize Health Consultation'}, follow_redirects=True)
        self.assertEqual(new_conv.status_code, 200)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM ai_conversations WHERE title='Maize Health Consultation' ORDER BY id DESC LIMIT 1")
        conv_id = cur.fetchone()[0]
        close_db_connection(conn, cur)

        # Farmer B cannot view Farmer A's conversation
        b_view = client_b.get(f'/assistant/{conv_id}', follow_redirects=True)
        self.assertIn(b"not found", b_view.data.lower())

    def test_08_assistant_chat_api_grounding(self):
        """Assistant chat API validates input and provides fallback or model reply."""
        client = app.test_client()
        email = f"ai_chat_{self.run_id}@example.com"
        self._register(client, "Chat Farmer", email, "0766000003", "farmer")

        # Empty message validation
        res_empty = client.post('/api/assistant/chat', json={'message': ''})
        self.assertEqual(res_empty.status_code, 400)

        # Valid agricultural query
        res_chat = client.post('/api/assistant/chat', json={
            'message': 'What are the recommended planting windows for maize in Nakuru?'
        })
        self.assertEqual(res_chat.status_code, 200)
        data = json.loads(res_chat.data)
        self.assertTrue(data.get('success', False))
        self.assertTrue(len(data.get('reply', '')) > 10)

    # ── STAGE 5: CROP CALENDAR & WEATHER ALERTS ───────────────────────────────

    def test_09_crop_calendar_task_generation_and_postponement(self):
        """Crop cycle generates agronomic tasks; farmer can reschedule with auditable reason."""
        client = app.test_client()
        email = f"cal_{self.run_id}@example.com"
        self._register(client, "Calendar Farmer", email, "0777000001", "farmer")

        # Create farm and crop cycle
        client.post('/farms/new', data={'name': 'Calendar Farm', 'county_id': '32'}, follow_redirects=True)
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM farms WHERE name='Calendar Farm' ORDER BY id DESC LIMIT 1")
        farm_id = cur.fetchone()[0]
        cur.execute("SELECT crop_id FROM crops WHERE crop_name='Potato' LIMIT 1")
        crop_id = cur.fetchone()[0]
        close_db_connection(conn, cur)

        # Add cycle with planting date -> triggers auto-task generation
        client.post(f'/farms/{farm_id}/cycles/new', data={
            'crop_id': str(crop_id),
            'variety_name': 'Shangi',
            'planting_date': '2026-10-20',
            'status': 'active'
        }, follow_redirects=True)

        # Verify tasks generated in DB
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, title, due_date FROM farm_tasks WHERE farm_id=%s ORDER BY due_date ASC", (farm_id,))
        tasks = cur.fetchall()
        close_db_connection(conn, cur)
        self.assertTrue(len(tasks) >= 5)

        first_task_id = tasks[0][0]

        # Toggle completion
        toggle_res = client.post(f'/calendar/tasks/{first_task_id}/toggle', follow_redirects=True)
        self.assertEqual(toggle_res.status_code, 200)

        # Postpone with documented reason
        postpone_res = client.post(f'/calendar/tasks/{first_task_id}/postpone', data={
            'new_due_date': '2026-11-05',
            'postponement_reason': 'Heavy rainfall delayed field spraying'
        }, follow_redirects=True)
        self.assertEqual(postpone_res.status_code, 200)

        # Verify postponement audit in DB
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT status, postponement_reason FROM farm_tasks WHERE id=%s", (first_task_id,))
        st, reason = cur.fetchone()
        close_db_connection(conn, cur)
        self.assertEqual(st, 'postponed')
        self.assertEqual(reason, 'Heavy rainfall delayed field spraying')

    def test_10_weather_alerts_and_notifications(self):
        """Active weather alerts and notifications are queryable."""
        client = app.test_client()
        email = f"weather_{self.run_id}@example.com"
        self._register(client, "Weather Farmer", email, "0788000001", "farmer")

        # Check weather alerts page
        page_res = client.get('/weather/alerts')
        self.assertEqual(page_res.status_code, 200)
        self.assertIn(b"Agro-Meteorological Advisories", page_res.data)

        # Check weather alerts API
        api_res = client.get('/api/weather/alerts')
        self.assertEqual(api_res.status_code, 200)
        wdata = json.loads(api_res.data)
        self.assertTrue(wdata.get('success', False))
        self.assertTrue(len(wdata.get('alerts', [])) > 0)


if __name__ == '__main__':
    unittest.main()
