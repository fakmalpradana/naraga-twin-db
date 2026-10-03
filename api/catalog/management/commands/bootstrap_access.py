"""Create groups viewer/editor/admin and (optionally) the first admin user from env. Idempotent."""
import os
from django.contrib.auth.models import Group, Permission, User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    def handle(self, *args, **opts):
        perms = Permission.objects.filter(content_type__app_label="catalog")
        readonly = ("inventory", "changelog", "reflodlevel")
        spec = {
            "viewer": perms.filter(codename__startswith="view_"),
            "editor": perms.filter(codename__regex=r"^(view|add|change)_").exclude(codename__regex="^(add|change)_(" + "|".join(readonly) + ")$"),
            "admin": perms.exclude(codename__regex="^(add|change|delete)_(" + "|".join(readonly) + ")$"),
        }
        for name, qs in spec.items():
            g, _ = Group.objects.get_or_create(name=name)
            g.permissions.set(qs)
        if name := os.environ.get("ADMIN_USERNAME"):
            pw = os.environ.get("ADMIN_PASSWORD")
            u, created = User.objects.get_or_create(username=name, defaults={"is_staff": True, "is_superuser": True})
            if created and pw:
                u.set_password(pw); u.save()
            u.groups.add(Group.objects.get(name="admin"))
            self.stdout.write(f"admin user {name}: {'created' if created else 'exists'}")
