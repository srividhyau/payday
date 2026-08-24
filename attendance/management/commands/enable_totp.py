import io

import qrcode
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice


class Command(BaseCommand):
    help = "Enroll a user for TOTP two-factor login on /admin/ (scan with Google Authenticator, Authy, etc.)"

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, username, **options):
        User = get_user_model()
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"No user named {username!r}")

        if TOTPDevice.objects.filter(user=user, confirmed=True).exists():
            raise CommandError(
                f"{username} already has a confirmed TOTP device. "
                f"Delete it in /admin/otp_totp/totpdevice/ first if you want to re-enroll."
            )

        TOTPDevice.objects.filter(user=user, confirmed=False).delete()
        device = TOTPDevice.objects.create(user=user, name="default", confirmed=False)

        qr = qrcode.QRCode(border=1)
        qr.add_data(device.config_url)
        qr.make()
        buf = io.StringIO()
        qr.print_ascii(out=buf)
        self.stdout.write(buf.getvalue())
        self.stdout.write(f"Or enter this key manually: {device.key}\n")
        self.stdout.write("Scan the QR code above with an authenticator app, then enter the 6-digit code it shows.\n")

        token = input("Code: ").strip()
        if not device.verify_token(token):
            device.delete()
            raise CommandError("That code didn't verify — nothing was saved, run the command again.")

        device.confirmed = True
        device.save()
        self.stdout.write(self.style.SUCCESS(f"TOTP enabled for {username}."))
