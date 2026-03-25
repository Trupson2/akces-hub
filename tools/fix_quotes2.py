"""Broader fix: replace ALL double-quoted material-symbols spans with single-quoted.
This handles ALL contexts: f-strings, print(), plain strings, etc."""
import os, sys

sys.stdout.reconfigure(encoding='utf-8')

# All span variants to fix
REPLACEMENTS = [
    ('<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">',
     "<span class='material-symbols-outlined' style='font-size:inherit;vertical-align:middle'>"),
    ('<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#22c55e">',
     "<span class='material-symbols-outlined' style='font-size:inherit;vertical-align:middle;color:#22c55e'>"),
    ('<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#ef4444">',
     "<span class='material-symbols-outlined' style='font-size:inherit;vertical-align:middle;color:#ef4444'>"),
    ('<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#3b82f6">',
     "<span class='material-symbols-outlined' style='font-size:inherit;vertical-align:middle;color:#3b82f6'>"),
]

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

    for old, new in REPLACEMENTS:
        if old in content:
            c = content.count(old)
            content = content.replace(old, new)
            total += c

    if content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"  Fixed: {filepath}")

print(f"\nTotal replacements: {total}")
