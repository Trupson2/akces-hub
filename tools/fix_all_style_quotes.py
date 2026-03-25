"""Fix ALL style='...' conflicts inside Python f-strings and single-quoted strings.
Strategy: Replace style='value' with style=value (unquoted - valid in HTML for simple values)
or for complex values use HTML entity approach."""
import re
import os

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
skip_dirs = {'.git', '__pycache__', 'node_modules', '.claude', 'tools'}

# Pattern: inside material-symbols-outlined spans, fix style='...'
# We match: style='anything_without_quotes'>  and replace quotes
pattern = re.compile(r"(<span class=material-symbols-outlined) style='([^']*?)'>")

def replacement(m):
    prefix = m.group(1)
    style_val = m.group(2)
    # Use no quotes - valid for simple CSS values without spaces that need quoting
    # But some values have semicolons/colons which are fine unquoted in HTML
    return f'{prefix} style={style_val}>'

total = 0
files_changed = 0

for dirpath, dirs, files in os.walk(root):
    dirs[:] = [d for d in dirs if d not in skip_dirs]
    for fname in files:
        if not fname.endswith('.py'):
            continue
        fpath = os.path.join(dirpath, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        count = len(pattern.findall(content))
        if count == 0:
            continue
        new_content = pattern.sub(replacement, content)
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        total += count
        files_changed += 1
        print(f"  {fname}: {count} fixes")

print(f"\nTotal: {total} fixes in {files_changed} files")
