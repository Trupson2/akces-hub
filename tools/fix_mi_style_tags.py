"""Replace <i class=mi style=...>icon</i> with <span class="material-symbols-outlined" style=...>icon</span>."""
import re
import os

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pattern: <i class=mi style=ANYTHING>icon</i>
pattern = re.compile(r'<i class=mi style=([^>]+)>(.*?)</i>')

def replacement(m):
    style = m.group(1).strip()
    icon = m.group(2)
    # Ensure style is properly quoted
    if not style.startswith('"') and not style.startswith("'"):
        style_val = style.strip('"').strip("'")
    else:
        style_val = style.strip('"').strip("'")
    return f'<span class="material-symbols-outlined" style="{style_val}">{icon}</span>'

skip_files = {'fix_mi_tags.py', 'fix_mi_style_tags.py', 'fix_insights.py', 'fix_textcontent.py',
              'fix_option_icons.py', 'fix_telegram_icons.py', 'fix_quotes_final.py'}

total = 0
files_changed = 0

for dirpath, dirs, files in os.walk(root):
    dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', 'node_modules', '.claude')]
    for fname in files:
        if not fname.endswith('.py'):
            continue
        if fname in skip_files:
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
        print(f"  {fname}: {count} replacements")

print(f"\nTotal: {total} replacements in {files_changed} files")
