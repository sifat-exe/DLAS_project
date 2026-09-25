from django.test import TestCase, Client
from django.urls import reverse

class DLASFoundationTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_landing_page(self):
        response = self.client.get(reverse('core:landing'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DLAS")
        self.assertContains(response, "Digital Legal Aid System")

    def test_language_switch_mechanism(self):
        # Switch to Bangla
        response = self.client.get(reverse('core:set_language', args=['bn']))
        self.assertEqual(response.status_code, 302)
        # Verify Bangla is active in session
        self.assertEqual(self.client.session.get('django_language'), 'bn')

        # Visit landing with Bangla active
        response = self.client.get(reverse('core:landing'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ডিজিটাল লিগ্যাল এইড সিস্টেম")

        # Switch back to English
        response = self.client.get(reverse('core:set_language', args=['en']))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get('django_language'), 'en')

    def test_auth_routes(self):
        # Login page
        response = self.client.get(reverse('accounts:login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DLAS Portal Access")

        # Demo role login
        response = self.client.post(reverse('accounts:login'), {'demo_role': 'citizen'})
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('dashboard:citizen'))

        # Logout
        response = self.client.get(reverse('accounts:logout'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('core:landing'))

    def test_all_dashboards(self):
        dashboards = [
            ('dashboard:index', "Stakeholder Dashboards Hub"),
            ('dashboard:citizen', "Citizen Dashboard"),
            ('dashboard:officer', "District Legal Aid Officer (DLAO) Workspace"),
            ('dashboard:support_staff', "DLAO Support Staff Workspace"),
            ('dashboard:udc_operator', "Union Digital Centre (UDC) Intake Portal"),
            ('dashboard:panel_lawyer', "Panel Lawyer Worklist"),
            ('dashboard:mediator', "Alternative Dispute Resolution (ADR) & Mediation"),
            ('dashboard:helpline_agent', "Helpline Agent Console (16699)"),
            ('dashboard:representative', "Legal Aid Representative Portal"),
            ('dashboard:admin', "DLAS System Administration"),
        ]
        for url_name, expected_text in dashboards:
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200, f"Failed for {url_name}")
            self.assertContains(response, expected_text)

    def test_app_index_routes(self):
        routes = [
            'cases:index',
            'documents:index',
            'referrals:index',
            'lawyers:index',
            'mediation:index',
        ]
        for url_name in routes:
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200, f"Failed for {url_name}")
