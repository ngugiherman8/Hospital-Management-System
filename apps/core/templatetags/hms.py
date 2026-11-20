from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """Look up a dictionary entry in a template: {{ chart|get_item:tooth }}."""
    return (mapping or {}).get(key, [])
