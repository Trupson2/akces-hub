"""Fix textContent assignments that contain HTML - replace with plain text or innerHTML."""
import re, os, sys

sys.stdout.reconfigure(encoding='utf-8')

SPAN_PAT = re.compile(r"<span[^>]*material-symbols[^>]*>\w+</span>")
MI_PAT = re.compile(r"<i class=mi>\w+</i>")

total = 0
files = ['app.py']
for root, dirs, fnames in os.walk('templates'):
    for f in fnames:
        if f.endswith('.html'):
            files.append(os.path.join(root, f))
for root, dirs, fnames in os.walk('modules'):
    for f in fnames:
        if f.endswith('.py'):
            files.append(os.path.join(root, f))

for filepath in files:
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    changed = False
    new_lines = []
    for line in lines:
        if 'textContent' in line and ('<span' in line or '<i class=mi>' in line):
            old = line
            # Only remove HTML tags, preserve indentation
            line = SPAN_PAT.sub('', line)
            line = MI_PAT.sub('', line)
            if line != old:
                changed = True
                total += 1
        new_lines.append(line)

    if changed:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        print(f"  Fixed: {filepath}")

print(f"\nTotal lines fixed: {total}")
