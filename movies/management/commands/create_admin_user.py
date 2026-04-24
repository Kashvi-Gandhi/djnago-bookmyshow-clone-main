from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from movies.models import AdminUser
import os
import getpass
import secrets


class Command(BaseCommand):
    help = 'Create a superadmin admin user for analytics and write credentials to docs/admin_credentials_report.txt'

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, help='Username for admin user (or set ADMIN_USERNAME env)')
        parser.add_argument('--email', type=str, help='Email for admin user (or set ADMIN_EMAIL env)')
        parser.add_argument('--password', type=str, help='Password for admin user (or set ADMIN_PASSWORD env). If not provided, you will be prompted.')
        parser.add_argument('--write-report', action='store_true', help='Write a credentials report to docs/ (disabled by default)')

    def handle(self, *args, **options):
        # Resolve credentials from args -> env -> interactive prompt
        username = options.get('username') or os.environ.get('ADMIN_USERNAME')
        email = options.get('email') or os.environ.get('ADMIN_EMAIL')
        password = options.get('password') or os.environ.get('ADMIN_PASSWORD')

        if not username:
            username = input('Admin username: ').strip()
        if not email:
            email = input('Admin email: ').strip()
        if not password:
            # Prompt securely for password
            password = getpass.getpass('Admin password (input will be hidden): ').strip()
            if not password:
                # Generate a secure password if user leaves blank
                password = secrets.token_urlsafe(16)

        if User.objects.filter(username=username).exists():
            user = User.objects.get(username=username)
            self.stdout.write(self.style.WARNING(f'User {username} already exists. Updating password and ensuring admin profile.'))
            user.set_password(password)
            user.email = email
            user.is_staff = True
            user.is_superuser = True
            user.save()
        else:
            user = User.objects.create_user(username=username, email=email, password=password)
            user.is_staff = True
            user.is_superuser = True
            user.save()

        admin_profile, created = AdminUser.objects.get_or_create(user=user, defaults={'role': 'superadmin', 'is_active': True})
        if not created:
            admin_profile.role = 'superadmin'
            admin_profile.is_active = True
            admin_profile.save()

        self.stdout.write(self.style.SUCCESS(f'Superadmin created/updated: {username}'))

        # Optionally write a report. Writing credentials to disk is disabled by default.
        if options.get('write_report'):
            docs_dir = os.path.join(os.getcwd(), 'docs')
            os.makedirs(docs_dir, exist_ok=True)
            report_path = os.path.join(docs_dir, 'admin_credentials_report.txt')
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write('ADMIN CREDENTIALS (FOR TESTING ONLY)\n')
                f.write(f'Username: {username}\n')
                f.write(f'Email: {email}\n')
                f.write(f'Password: {password}\n')
                f.write('\nNOTE: These credentials are stored here for development/testing only. Rotate immediately in production.\n')
            self.stdout.write(self.style.WARNING(f'Credentials written to {report_path}'))
        else:
            self.stdout.write(self.style.NOTICE('Credential file not written. To write report, re-run with --write-report or set ADMIN_* env vars.'))
