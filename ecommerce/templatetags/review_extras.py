from django import template

register = template.Library()


@register.filter
def reviewer_initials(name: str) -> str:
    if not name or not str(name).strip():
        return "?"
    parts = str(name).split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    s = parts[0]
    return s[:2].upper() if len(s) >= 2 else s.upper()
