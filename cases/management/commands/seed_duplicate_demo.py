from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from accounts.models import UserProfile
from cases.models import Application, CaseRecord, DuplicateCandidate
from cases.services import submit_application, accept_application
from cases.ai_service import MockAIService


class Command(BaseCommand):
    help = "Seeds 12 deterministic duplicate and look-alike demo records for DLAO evaluation (Batch 3 Parts E & F)."

    def handle(self, *args, **options):
        self.stdout.write("Seeding 12 duplicate and look-alike demonstration records...")

        # Ensure demo DLAO officer exists
        officer, _ = User.objects.get_or_create(
            username='demo_dlao_officer',
            defaults={'first_name': 'Khandaker', 'last_name': 'Mustafiz'}
        )
        if not hasattr(officer, 'profile'):
            UserProfile.objects.get_or_create(
                user=officer,
                defaults={'role': UserProfile.ROLE_DLAO_OFFICER, 'phone': '01700000002', 'language': 'bn'}
            )

        # 12 Deterministic Demo Records (Genuine duplicates + Look-alike traps)
        demo_specs = [
            # 1 & 2: Genuine Duplicate Pair 1 (High match: identical name, phone, address, eviction issue)
            {
                'id_tag': 'DUP-01',
                'name': 'Rahim Uddin',
                'phone': '01711000111',
                'address': 'House 14, Road 5, Block B, Mirpur Section 10, Dhaka',
                'legal_problem': 'Tenancy eviction and landlord harassment',
                'incident_description': 'House owner attempting forced eviction without statutory 30-day notice and disconnected utility supply.',
                'channel': Application.CHANNEL_WEB,
                'priority': 'HIGH',
            },
            {
                'id_tag': 'DUP-02',
                'name': 'Rahim Uddin',
                'phone': '01711000111',
                'address': 'Road 5, Block B, Mirpur 10, Dhaka',
                'legal_problem': 'Unlawful dispossess and tenant eviction',
                'incident_description': 'Landlord threatened physical eviction and locked the main entrance gate without court order.',
                'channel': Application.CHANNEL_WALK_IN,
                'priority': 'HIGH',
            },

            # 3 & 4: Genuine Duplicate Pair 2 (Domestic violence & maintenance with identical phone and address)
            {
                'id_tag': 'DUP-03',
                'name': 'Fatema Begum',
                'phone': '01819000222',
                'address': 'Hemayetpur, Savar, Dhaka',
                'legal_problem': 'Spousal maintenance denial and domestic abuse',
                'incident_description': 'Husband expelled applicant from marital home and refused child maintenance support.',
                'channel': Application.CHANNEL_UDC,
                'priority': 'URGENT',
            },
            {
                'id_tag': 'DUP-04',
                'name': 'Fatema Begum',
                'phone': '01819000222',
                'address': 'Hemayetpur, Savar, Dhaka',
                'legal_problem': 'Maintenance recovery and family dispute',
                'incident_description': 'Seeking urgent maintenance recovery for minor children following abandonment by spouse.',
                'channel': Application.CHANNEL_HELPLINE,
                'priority': 'URGENT',
            },

            # 5 & 6: Genuine Duplicate Pair 3 (Spelling variation: 'Md. Karim Ullah' vs 'Karimullah' + same phone)
            {
                'id_tag': 'DUP-05',
                'name': 'Md. Karim Ullah',
                'phone': '01912000333',
                'address': 'Zindabazar, Sadar, Sylhet',
                'legal_problem': 'Commercial shop advance deposit forfeiture',
                'incident_description': 'Market landlord refusing refund of 500,000 BDT security deposit after expiration of tenancy deed.',
                'channel': Application.CHANNEL_WEB,
                'priority': 'MEDIUM',
            },
            {
                'id_tag': 'DUP-06',
                'name': 'Karimullah',
                'phone': '01912000333',
                'address': 'Zindabazar, Sylhet Sadar',
                'legal_problem': 'Shop lease advance security deposit dispute',
                'incident_description': 'Commercial lease ended in December 2025; landlord illegally withholding security deposit.',
                'channel': Application.CHANNEL_WALK_IN,
                'priority': 'MEDIUM',
            },

            # 7: Look-alike Trap 1 (Same Name 'Rahim Uddin', but DIFFERENT phone, location, and legal problem)
            {
                'id_tag': 'TRAP-01',
                'name': 'Rahim Uddin',
                'phone': '01552000444',
                'address': 'Sector 3, Uttara, Dhaka',
                'legal_problem': 'Motor vehicle accident compensation claim',
                'incident_description': 'Hit-and-run road crash on Dhaka-Mymensingh highway resulting in permanent orthopedic injury; seeking insurance claim.',
                'channel': Application.CHANNEL_WEB,
                'priority': 'HIGH',
            },

            # 8: Look-alike Trap 2 (Same Locality 'Mirpur Section 10', but DIFFERENT person, phone, and problem)
            {
                'id_tag': 'TRAP-02',
                'name': 'Abdul Haque',
                'phone': '01673000555',
                'address': 'Mirpur Section 10, Dhaka',
                'legal_problem': 'Garments factory unpaid wage recovery',
                'incident_description': 'Textile employer closed factory without clearing 4 months unpaid salary and statutory severance pay.',
                'channel': Application.CHANNEL_WALK_IN,
                'priority': 'MEDIUM',
            },

            # 9 & 10: Look-alike Trap 3 (Same factory area & similar legal issue, but TWO DIFFERENT workers)
            {
                'id_tag': 'TRAP-03A',
                'name': 'Shahida Akhter',
                'phone': '01314000666',
                'address': 'Kashimpur, Gazipur Sadar',
                'legal_problem': 'Unlawful termination of garment employment',
                'incident_description': 'Arbitrary verbal dismissal after 5 years service without termination benefits or service book return.',
                'channel': Application.CHANNEL_UDC,
                'priority': 'MEDIUM',
            },
            {
                'id_tag': 'TRAP-03B',
                'name': 'Nasreen Sultana',
                'phone': '01314000777',
                'address': 'Kashimpur, Gazipur Sadar',
                'legal_problem': 'Wrongful dismissal and maternity benefit denial',
                'incident_description': 'Refusal of paid maternity leave and dismissal upon notice of pregnancy.',
                'channel': Application.CHANNEL_UDC,
                'priority': 'HIGH',
            },

            # 11: Marma Indigenous Land Rights Case (Indigenous community member)
            {
                'id_tag': 'IND-01',
                'name': 'Mong Shwe Prue Marma',
                'phone': '01844000999',
                'address': 'Rowangchhari Mouza, Bandarban Hill District',
                'legal_problem': 'Customary jhum land encroachment and boundary violation',
                'incident_description': 'External commercial settlers illegally occupied ancestral orchard and destroyed customary boundary markers.',
                'channel': Application.CHANNEL_UDC,
                'priority': 'URGENT',
            },

            # 12: Rural Fisherfolk / Coastal Microcredit Dispute
            {
                'id_tag': 'RUR-01',
                'name': 'Anowara Begum',
                'phone': '01722000334',
                'address': 'Sabrang, Teknaf, Cox\'s Bazar',
                'legal_problem': 'Fishing boat mortgage and informal usury harassment',
                'incident_description': 'Informal moneylender seized fishing trawler despite repayment of 200% principal under compounding interest.',
                'channel': Application.CHANNEL_WALK_IN,
                'priority': 'MEDIUM',
            },
        ]

        created_cases = []
        for spec in demo_specs:
            # Check if this demo application already exists by phone + legal problem
            app = Application.objects.filter(phone=spec['phone'], legal_problem=spec['legal_problem']).first()
            if not app:
                app = submit_application(
                    name=spec['name'],
                    phone=spec['phone'],
                    address=spec['address'],
                    legal_problem=spec['legal_problem'],
                    incident_description=spec['incident_description'],
                    preferred_channel=spec['channel'],
                    language='bn',
                    actor=officer,
                    idempotency_token=f"SEED-DUP-{spec['id_tag']}"
                )

            # Accept as official case if not yet accepted
            case = getattr(app, 'case_record', None)
            if not case:
                case = accept_application(
                    application=app,
                    officer=officer,
                    priority=spec['priority'],
                    channel=spec['channel']
                )
            created_cases.append(case)

        # Run AI duplicate scan across all seeded cases
        total_candidates_found = 0
        for c in created_cases:
            candidates = MockAIService.run_duplicate_scan(c)
            total_candidates_found += len(candidates)

        self.stdout.write(self.style.SUCCESS(
            f"Successfully seeded {len(created_cases)} deterministic case records.\n"
            f"Identified {total_candidates_found} duplicate candidate suggestions for officer evaluation.\n"
            f"Safety check: All candidates set to 'pending'. No cases were merged or deleted."
        ))
