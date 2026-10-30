from jinja2 import DictLoader, Environment

env = Environment(loader=DictLoader({
    "base.html": "<h1>{% block title %}{% endblock %}</h1>{{ items|join(', ') }}",
    "page.html": "{% extends 'base.html' %}{% block title %}{{ name|upper }}{% endblock %}",
}), autoescape=True)
print(env.get_template("page.html").render(name="<x>", items=[1, 2, 3]))
print(env.from_string("{% for i in range(3) %}{{ loop.index }}{% endfor %}").render())
