from django import template

register = template.Library()


@register.filter
def dict_get(d, key):
    """Look up `key` in dict `d` — used on production.html for each
    operator's {day: quantity} map, where the key (day number) is a
    template loop variable rather than a literal."""
    return d.get(key)
