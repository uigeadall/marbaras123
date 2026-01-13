from django import template

register = template.Library()

@register.filter
def mul(value, arg):
    return value * arg

@register.filter
def get_item(dictionary, key):
    """Get item from dictionary by key."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, 0)
    return 0
