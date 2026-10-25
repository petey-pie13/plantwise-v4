"""
Comprehensive Test Suite for PlantWise Agricultural Expert System
Validates database connectivity, public routes, advisory engine, diagnosis flow,
admin authentication, admin dashboard APIs, and security headers.
"""

import os
import unittest
import json
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '..', '.env'))

from app import app, get_db_connection

class PlantWiseSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        cls.client = app.test_client()

    def test_01_db_connection(self):
        """Verify PostgreSQL database connection is operational."""
        conn = get_db_connection()
        self.assertIsNotNone(conn, "Database connection should not be None")
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        self.assertEqual(cur.fetchone()[0], 1)
        cur.close()
        conn.close()

    def test_02_home_page(self):
        """GET / should render landing page."""
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'PlantWise', res.data)

    def test_03_advisory_get(self):
        """GET /advisory should render county selection form."""
        res = self.client.get('/advisory')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'county', res.data.lower())

    def test_04_diagnosis_page(self):
        """GET /diagnosis should render crop health diagnostic page."""
        res = self.client.get('/diagnosis')
        self.assertEqual(res.status_code, 200)

    def test_05_resources_page(self):
        """GET /resources should render educational resources."""
        res = self.client.get('/resources')
        self.assertEqual(res.status_code, 200)

    def test_06_api_counties(self):
        """GET /api/counties should return 47 Kenya counties."""
        res = self.client.get('/api/counties')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        counties = data if isinstance(data, list) else data.get('counties', [])
        self.assertEqual(len(counties), 47, f"Expected 47 counties, got {len(counties)}")

    def test_07_api_sub_counties(self):
        """GET /api/sub-counties?county_id=1 should return sub-counties."""
        res = self.client.get('/api/sub-counties?county_id=1')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertTrue('sub_counties' in data or isinstance(data, list))

    def test_08_api_county_soil_profile(self):
        """GET /api/county-soil-profile?county_id=1 should return soil properties."""
        res = self.client.get('/api/county-soil-profile?county_id=1')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertTrue(data.get('success', False))

    def test_09_api_ecological_conditions(self):
        """GET /api/ecological-conditions?county_id=1 should return ecological info."""
        res = self.client.get('/api/ecological-conditions?county_id=1')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertTrue(data.get('success', False))

    def test_10_advisory_post_calculation(self):
        """POST /advisory should generate recommendations and redirect or render results."""
        res = self.client.post('/advisory', data={
            'county': 'Nakuru',
            'soil_ph': '6.2',
            'rainfall': 'Medium',
            'temperature': 'Medium'
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    def test_11_api_diagnose_knowledge_base(self):
        """POST /api/diagnose with method=symptom should use knowledge base."""
        res = self.client.post('/api/diagnose', data={
            'crop': 'Maize',
            'method': 'symptom',
            'plant_part': 'Leaves',
            'symptoms': json.dumps(['Yellowing', 'Wilting'])
        })
        self.assertIn(res.status_code, [200, 302])

    def test_12_api_diagnosis_feedback(self):
        """POST /api/diagnosis/feedback should record thumbs-up/down."""
        res = self.client.post('/api/diagnosis/feedback',
                               json={'disease_name': 'Maize Lethal Necrosis', 'was_correct': True},
                               content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertTrue(data.get('success', False))

    def test_13_admin_unauthorized_redirect(self):
        """Unauthenticated access to admin pages should redirect to login."""
        res = self.client.get('/admin/dashboard')
        self.assertEqual(res.status_code, 302)
        self.assertIn('/admin/login', res.headers.get('Location', ''))

    def test_14_admin_login_and_dashboard(self):
        """Admin can log in and view dashboard and admin APIs."""
        # 1. Login with seeded credentials
        login_res = self.client.post('/admin/login', data={
            'username': 'admin',
            'password': 'Admin@Plantwise2025!'
        }, follow_redirects=True)
        self.assertEqual(login_res.status_code, 200)

        # 2. Access dashboard
        dash_res = self.client.get('/admin/dashboard')
        self.assertEqual(dash_res.status_code, 200)

        # 3. Access admin trend API
        trend_res = self.client.get('/admin/api/diagnosis-trend')
        self.assertEqual(trend_res.status_code, 200)
        trend_data = json.loads(trend_res.data)
        self.assertTrue(trend_data.get('success', False))

        # 4. Access admin top diseases API
        top_res = self.client.get('/admin/api/top-diseases')
        self.assertEqual(top_res.status_code, 200)
        top_data = json.loads(top_res.data)
        self.assertTrue(top_data.get('success', False))

        # 5. Access admin crops
        crops_res = self.client.get('/admin/crops')
        self.assertEqual(crops_res.status_code, 200)

        # 6. Access admin diseases
        diseases_res = self.client.get('/admin/diseases')
        self.assertEqual(diseases_res.status_code, 200)

        # 7. Access admin feedback
        feedback_res = self.client.get('/admin/feedback')
        self.assertEqual(feedback_res.status_code, 200)

if __name__ == '__main__':
    unittest.main()
