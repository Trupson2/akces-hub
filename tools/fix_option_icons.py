"""Remove Material Symbols HTML from <option> tags where they show as text."""
import re, os, sys

sys.stdout.reconfigure(encoding='utf-8')

# Patterns for material symbols spans and mi shorthand
SPAN_PAT = re.compile(r'<span[^>]*material-symbols[^>]*>\w+</span>')
MI_PAT = re.compile(r'<i class=mi>\w+</i>')

total = 0
files = []
for root, dirs, fnames in os.walk('templates'):
    for f in fnames:
        if f.endswith('.html'):
            files.append(os.path.join(root, f))
for root, dirs, fnames in os.walk('modules'):
    for f in fnames:
        if f.endswith('.py'):
            files.append(os.path.join(root, f))
files.append('app.py')

for filepath in files:
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    orig = content

    # Find all <option...>...</option> and remove icon HTML inside
    def clean_option(match):
        opt = match.group(0)
        cleaned = SPAN_PAT.sub('', opt)
        cleaned = MI_PAT.sub('', cleaned)
        cleaned = re.sub(r'  +', ' ', cleaned)
        return cleaned

    content = re.sub(r'<option[^>]*>.*?</option>', clean_option, content, flags=re.DOTALL)

    if content != orig:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        total += 1
        print(f"  Fixed: {filepath}")

print(f"\nTotal files: {total}")
