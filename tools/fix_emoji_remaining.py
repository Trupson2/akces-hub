"""Fix remaining emoji in templates with Material Symbols."""
import os, re

TEMPLATES_DIR = 'templates'

# Map emoji to material symbol HTML
EMOJI_MAP = {
    '📦': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">inventory_2</span>',
    '📷': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">photo_camera</span>',
    '🏷️': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">sell</span>',
    '🔄': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">sync</span>',
    '📁': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">folder</span>',
    '🚗': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">directions_car</span>',
    '📄': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">description</span>',
    '✏️': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">edit</span>',
    '🔧': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">build</span>',
    '⚠️': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">warning</span>',
    '📊': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">bar_chart</span>',
    '📝': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">edit_note</span>',
    '💾': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">save</span>',
    '🚪': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">logout</span>',
    '🔒': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">lock</span>',
    '🖥️': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">desktop_windows</span>',
    '🎨': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">palette</span>',
    '🎮': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">sports_esports</span>',
    '✔': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">check</span>',
    '☰': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">menu</span>',
}

# Theme icons need special treatment (JS context)
THEME_REPLACEMENTS = {
    "☀️": "light_mode",
    "🌙": "dark_mode",
}

total_replaced = 0

for root, dirs, files in os.walk(TEMPLATES_DIR):
    for fname in files:
        if not fname.endswith('.html'):
            continue
        filepath = os.path.join(root, fname)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        original = content

        for emoji, replacement in EMOJI_MAP.items():
            if emoji in content:
                count = content.count(emoji)
                content = content.replace(emoji, replacement)
                total_replaced += count
                print(f"  {filepath}: emoji -> material symbol ({count}x)")

        # Special: favicon SVG - replace emoji with simple icon
        content = content.replace(
            """<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'><span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">inventory_2</span></text></svg>">""",
            """<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='%2313131c'/><text x='50' y='55' text-anchor='middle' dominant-baseline='middle' font-size='50' fill='%238ff5ff'>A</text></svg>">"""
        )

        # Theme toggle: replace emoji text with material symbol elements
        # In JS: textContent = '🌙' -> innerHTML with material symbol
        content = content.replace(
            """<span id="theme-icon">☀️</span>""",
            """<span id="theme-icon" class="material-symbols-outlined" style="font-size:1.1rem">light_mode</span>"""
        )
        content = content.replace(
            """document.getElementById('theme-icon').textContent = next === 'dark' ? '🌙' : '☀️';""",
            """document.getElementById('theme-icon').textContent = next === 'dark' ? 'dark_mode' : 'light_mode';"""
        )
        content = content.replace(
            """document.getElementById('theme-icon').textContent = _t === 'dark' ? '🌙' : '☀️';""",
            """document.getElementById('theme-icon').textContent = _t === 'dark' ? 'dark_mode' : 'light_mode';"""
        )

        if content != original:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"  Updated: {filepath}")

print(f"\nTotal emoji replaced: {total_replaced}")
