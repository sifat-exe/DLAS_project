from django.core.management.base import BaseCommand
from cases.services import process_overdue_referrals

class Command(BaseCommand):
    help = "Scans for referrals with missed acknowledgement deadlines and reassigns them to eligible officers."

    def handle(self, *args, **options):
        self.stdout.write("Checking for overdue referrals awaiting acknowledgement...")
        processed = process_overdue_referrals()
        self.stdout.write(self.style.SUCCESS(f"Successfully processed {len(processed)} overdue referral(s)."))
