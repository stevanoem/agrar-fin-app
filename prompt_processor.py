from jinja2 import Template

def render_prompt(path: str, variables: dict) -> str:
    with open(path, 'r', encoding='utf-8') as f:
        template = f.read()
    jinja_template = Template(template)

    return jinja_template.render(**variables)
