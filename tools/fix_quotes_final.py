"""Final fix: Replace ALL material-symbols spans in .py files with short <i class=mi>icon</i> tags.
Then add a CSS rule in base.html that styles .mi like material-symbols-outlined.
This avoids ALL quote conflicts because <i class=mi> has no quotes at all."""
import os, re, sys

sys.stdout.reconfigure(encoding='utf-8')

# Match any material-symbols span (both quote styles)
SPAN_RE = re.compile(
    r"""<span class=['"](material-symbols-outlined)['"] style=['"]font-size:inherit;vertical-align:middle(?:;color:#([0-9a-f]+))?['"]>(\w+)</span>""",
    re.IGNORECASE
)

def replace_span(match):
    color = match.group(2)
    icon = match.group(3)
    if color:
        return f'<i class=mi style=color:#{color}>{icon}</i>'
    return f'<i class=mi>{icon}</i>'

files = ['app.py']
for root, dirs, fnames in os.walk('modules'):
    for f in fnames:
        if f.endswith('.py'):
            files.append(os.path.join(root, f))

total = 0
for filepath in files:
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    original = content

    # Process line by line
    lines = content.split('\n')
    new_lines = []
    for line in lines:
        stripped = line.lstrip()
        # Print statements and comments -> use [ICON] text
        if stripped.startswith('print(') or stripped.startswith('print (') or stripped.startswith('#'):
            old = line
            line = SPAN_RE.sub(lambda m: f'[{m.group(3).upper()[:6]}]', line)
            if line != old:
                total += 1
        else:
            # HTML context -> use short <i class=mi> tag (no quotes needed!)
            old = line
            line = SPAN_RE.sub(replace_span, line)
            if line != old:
                total += 1
        new_lines.append(line)

    new_content = '\n'.join(new_lines)
    if new_content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"  {filepath}")

print(f"\nTotal lines fixed: {total}")
