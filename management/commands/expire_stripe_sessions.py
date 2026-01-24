from django.core.management.base import BaseCommand
import time
import stripe
from django.conf import settings

stripe.api_key = settings.STRIPE_SECRET_KEY
MAX_AGE = 10 * 60  # 10 minutes

class Command(BaseCommand):
    help = "Expire stale RareHunterTCG checkout sessions"

    def handle(self, *args, **kwargs):
        now = int(time.time())
        expired = 0

        sessions = stripe.checkout.Session.list(
            status="open",
            limit=100
        )

        for session in sessions.auto_paging_iter():
            # 🔑 THIS is the important line
            if session.metadata.get("source") != "rarehunter_cart":
                continue

            if now - session.created > MAX_AGE:
                try:
                    stripe.checkout.Session.expire(session.id)
                    expired += 1
                except Exception as e:
                    self.stderr.write(str(e))

        self.stdout.write(f"Expired {expired} RareHunter checkout sessions")
