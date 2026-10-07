"""Run inside the fresh NetBox installation using manage.py shell.

Provision the app's restricted API identity, never campus inventory or baselines.
The token is consumed from a protected mount and never printed.
"""

from pathlib import Path

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from users.models import ObjectPermission, Token, User

with transaction.atomic():
    user, created = User.objects.get_or_create(username="campus_inventory_writer")
    if created:
        user.set_unusable_password()
        user.save()
    if user.is_superuser or getattr(user, "is_staff", False) or user.has_usable_password():
        raise RuntimeError("Inventory service identity has unexpected interactive privileges")
    models = ["site", "manufacturer", "devicetype", "devicerole", "device", "interface", "cable"]
    permission, _ = ObjectPermission.objects.get_or_create(
        name="Campus reviewed inventory writer",
        defaults={"actions": ["view", "add", "change"], "enabled": True},
    )
    permission.object_types.set(ContentType.objects.filter(app_label="dcim", model__in=models))
    permission.users.add(user)
    raw = Path("/run/secrets/campus_token").read_text().strip()
    key, secret = raw.removeprefix("nbt_").split(".", 1)
    token = Token.objects.filter(key=key).first()
    if token:
        if token.user_id != user.pk or not token.validate(secret) or not token.is_active:
            raise RuntimeError("Inventory API token state differs from protected installation state")
    else:
        token = Token(
            user=user,
            key=key,
            token=secret,
            version=2,
            write_enabled=True,
            description="Reviewed inventory publication",
        )
        token.full_clean()
        token.save()
print("Inventory API identity configured; no devices or topology imported.")
