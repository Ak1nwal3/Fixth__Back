import re
import unicodedata
import uuid

import django.db.models.deletion
from django.db import migrations, models

_WHITESPACE_RE = re.compile(r"\s+")
_SEPARATOR_RE = re.compile(r"[_\-/\\,;:]+")
_APOSTROPHE_RE = re.compile(r"['\u2019`]")
_EDGE_PUNCTUATION = " \t\r\n.,;:!?()[]{}\"'"


def _normalize(value):
    value = unicodedata.normalize("NFKC", value or "")
    value = _WHITESPACE_RE.sub(" ", value).strip().casefold()
    value = _SEPARATOR_RE.sub(" ", value)
    value = _APOSTROPHE_RE.sub("", value)
    value = value.strip(_EDGE_PUNCTUATION)
    return _WHITESPACE_RE.sub(" ", value).strip()


def populate_normalized_names(apps, schema_editor):
    for model_name in ("Discipline", "Skill"):
        model = apps.get_model("profiles", model_name)
        seen = set()
        for obj in model.objects.all().order_by("created_at", "pk"):
            key = _normalize(obj.name)
            if not key:
                key = f"term-{obj.pk.hex[:8]}"
            if key in seen:
                key = f"{key}-{obj.pk.hex[:8]}"
            seen.add(key)
            obj.normalized_name = key
            obj.save(update_fields=["normalized_name"])


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="discipline",
            name="normalized_name",
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=255,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="skill",
            name="normalized_name",
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=255,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="discipline",
            name="name",
            field=models.CharField(max_length=120),
        ),
        migrations.AlterField(
            model_name="skill",
            name="name",
            field=models.CharField(max_length=120),
        ),
        migrations.RunPython(
            populate_normalized_names,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="discipline",
            name="normalized_name",
            field=models.CharField(
                editable=False,
                max_length=255,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="skill",
            name="normalized_name",
            field=models.CharField(
                editable=False,
                max_length=255,
                unique=True,
            ),
        ),
        migrations.CreateModel(
            name="DisciplineAlias",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=120)),
                (
                    "alias_key",
                    models.CharField(
                        editable=False,
                        max_length=255,
                        unique=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "discipline",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="aliases",
                        to="profiles.discipline",
                    ),
                ),
            ],
            options={
                "ordering": ["alias_key"],
            },
        ),
        migrations.CreateModel(
            name="SkillAlias",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=120)),
                (
                    "alias_key",
                    models.CharField(
                        editable=False,
                        max_length=255,
                        unique=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "skill",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="aliases",
                        to="profiles.skill",
                    ),
                ),
            ],
            options={
                "ordering": ["alias_key"],
            },
        ),
    ]
