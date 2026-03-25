"""Second pass: fix remaining emoji in app.py."""
import re, sys

sys.stdout.reconfigure(encoding='utf-8')

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

replacements = {
    # Check/cross in HTML
    "✅": '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#22c55e">check_circle</span>',
    "❌": '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#ef4444">cancel</span>',
    "ℹ️": '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#3b82f6">info</span>',
    "🌍": '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">language</span>',
    "👁": '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">visibility</span>',
    "❤": '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">favorite</span>',
    "🚗": '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">directions_car</span>',
    "➕": '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">add</span>',
    "➖": '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">remove</span>',
    "💬": '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">chat</span>',
    "👴": 'D',
    "👵": 'B',
}

count = 0
for emoji, repl in replacements.items():
    if emoji in content:
        c = content.count(emoji)
        content = content.replace(emoji, repl)
        count += c
        print(f"  {repr(emoji)} -> replaced ({c}x)")

# Fix print statements that now have HTML spans - simplify them
# print("✅ ...") -> print("[OK] ...")
content = re.sub(
    r'print\((f?["\'])(<span class="material-symbols-outlined"[^>]*>check_circle</span>)',
    lambda m: f'print({m.group(1)}[OK]',
    content
)
content = re.sub(
    r'print\((f?["\'])(<span class="material-symbols-outlined"[^>]*>cancel</span>)',
    lambda m: f'print({m.group(1)}[ERR]',
    content
)
content = re.sub(
    r'print\((f?["\'])(<span class="material-symbols-outlined"[^>]*>warning</span>)',
    lambda m: f'print({m.group(1)}[WARN]',
    content
)
content = re.sub(
    r'print\((f?["\'])\s*(<span class="material-symbols-outlined"[^>]*>chat</span>)',
    lambda m: f'print({m.group(1)}  [TG]',
    content
)

# Fix comment lines with HTML spans
content = re.sub(
    r'# (<span class="material-symbols-outlined"[^>]*>check_circle</span>)',
    '# [OK]',
    content
)

# Fix JS textContent assignments that now have HTML
content = re.sub(
    r"tx\.textContent = '(<span[^']*>check_circle</span>) '",
    "tx.textContent = '[OK] '",
    content
)
content = re.sub(
    r"tx\.textContent = '(<span[^']*>cancel</span>) '",
    "tx.textContent = '[ERR] '",
    content
)
content = re.sub(
    r"msg\.textContent = '(<span[^']*>check_circle</span>) ",
    "msg.textContent = '[OK] ",
    content
)
content = re.sub(
    r"msg\.textContent = '(<span[^']*>cancel</span>) ",
    "msg.textContent = '[ERR] ",
    content
)

# Fix ASCII art box with HTML span
content = content.replace(
    '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#22c55e">check_circle</span> CORS ENABLED!',
    'CORS ENABLED!'
)

# Fix f-string with HTML inside JS alert context
# '✅ ON' -> 'ON', etc
content = re.sub(
    r"'<span[^']*>check_circle</span> ON'",
    "'ON'",
    content
)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\nReplaced {count} remaining emoji in app.py")
remaining = len(re.findall(r'[\U0001F300-\U0001F9FF\U00002600-\U000027BF]', content))
print(f"Remaining emoji: {remaining}")
