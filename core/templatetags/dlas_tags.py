from django import template

register = template.Library()

@register.simple_tag(takes_context=True)
def tr(context, en_text, bn_text):
    """
    Template tag to render English or Bangla text based on the active language.
    Usage: {% tr "English text" "বাংলা টেক্সট" %}
    """
    is_bangla = context.get('is_bangla', False)
    return bn_text if is_bangla else en_text
