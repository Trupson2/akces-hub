"""Fix nested quote issues where <span class="material-symbols-outlined"> appears inside double-quoted Python strings."""
import re
import os

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# We need to replace the problematic pattern in ALL .py files
# The issue: "....<span class="material-symbols-outlined">icon</span>...."
# Should be: '....<span class="material-symbols-outlined">icon</span>....'
# But that's complex. Instead, let's use unquoted class attribute:
# <span class=material-symbols-outlined>icon</span>
# This is valid HTML and avoids all quote conflicts.

pattern = re.compile(r'<span class="material-symbols-outlined"(.*?)>(.*?)</span>')

def replacement(m):
    extra = m.group(1)  # might have style= etc
    icon = m.group(2)
    if extra:
        return f'<span class=material-symbols-outlined{extra}>{icon}</span>'
    return f'<span class=material-symbols-outlined>{icon}</span>'

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
