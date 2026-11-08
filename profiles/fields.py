from rest_framework import serializers

from profiles.services import (
    InvalidTerm,
    resolve_discipline,
    resolve_skills,
    split_skill_input,
)


class DisciplineField(serializers.CharField):
    """Accept a discipline name and resolve it to a canonical Discipline."""

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            return resolve_discipline(value)
        except InvalidTerm as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def to_representation(self, value):
        if value is None:
            return None
        return getattr(value, "name", value)


class SkillNamesField(serializers.ListField):
    """Accept skill names (or a delimited string) and resolve canonical rows."""

    child = serializers.CharField(max_length=120)

    def to_internal_value(self, data):
        try:
            data = split_skill_input(data)
        except InvalidTerm as exc:
            raise serializers.ValidationError(str(exc)) from exc
        values = super().to_internal_value(data)
        try:
            return resolve_skills(values)
        except InvalidTerm as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def to_representation(self, value):
        if value is None:
            return []
        try:
            skills = value.all()
        except AttributeError:
            skills = value
        return [getattr(skill, "name", str(skill)) for skill in skills]
