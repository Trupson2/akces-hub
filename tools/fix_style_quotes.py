"""Fix style="..." inside material-symbols-outlined spans in Python strings.
Changes style="value" to style='value' to avoid quote conflicts."""
import re
import os

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pattern: <span class=material-symbols-outlined style="anything">
# Replace with: <span class=material-symbols-outlined style='anything'>
pattern = re.compile(r'(<span class=material-symbols-outlined) style="([^"]*?)">')

def replacement(m):
    prefix = m.group(1)
    style_val = m.group(2)
    return f"{prefix} style='{style_val}'>"

skip_dirs = {'.git', '__pycache__', 'node_modules', '.claude', 'tools'}

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
