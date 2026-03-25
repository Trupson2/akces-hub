"""Fix emoji in app.py HTML templates (inline) - replace with Material Symbols."""
import re

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

original = content

# HTML context replacements (inline templates in f-strings)
html_emoji_map = {
    '📦': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">inventory_2</span>',
    '🗄️': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">dns</span>',
    '📋': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">assignment</span>',
    '🔄': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">sync</span>',
    '🔍': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">search</span>',
    '🏪': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">store</span>',
    '🎪': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">storefront</span>',
    '📊': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">bar_chart</span>',
    '📅': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">today</span>',
    '📆': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">calendar_month</span>',
    '✨': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">auto_awesome</span>',
    '🤖': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">smart_toy</span>',
    '📈': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">trending_up</span>',
    '📉': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">trending_down</span>',
    '💰': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">payments</span>',
    '💵': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">paid</span>',
    '📁': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">folder</span>',
    '🏆': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">emoji_events</span>',
    '🎯': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">adjust</span>',
    '⚙️': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">settings</span>',
    '🔒': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">lock</span>',
    '🔓': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">lock_open</span>',
    '📢': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">campaign</span>',
    '📝': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">edit_note</span>',
    '💡': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">lightbulb</span>',
    '🕐': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">schedule</span>',
    '🔧': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">build</span>',
    '⚡': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">bolt</span>',
    '🚀': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">rocket_launch</span>',
    '🛒': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">shopping_cart</span>',
    '🏷️': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">sell</span>',
    '📷': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">photo_camera</span>',
    '🎉': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">celebration</span>',
    '💳': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">credit_card</span>',
    '📌': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">push_pin</span>',
    '🖼️': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">image</span>',
    '🗑️': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">delete</span>',
    '💾': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">save</span>',
    '📂': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">folder_open</span>',
    '🔗': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">link</span>',
    '📄': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">description</span>',
    '📸': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">photo_camera</span>',
    '🔖': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">bookmark</span>',
    '🔃': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">autorenew</span>',
    '⏱️': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">timer</span>',
    '📃': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">article</span>',
    '📐': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">straighten</span>',
    '🧠': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">psychology</span>',
    '⚠️': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">warning</span>',
    '✏️': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">edit</span>',
    '🖨️': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">print</span>',
    '🔑': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">key</span>',
    '🌐': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">language</span>',
    '📥': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">download</span>',
    '📤': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">upload</span>',
}

# For print() statements, use simple text markers instead of material symbols
print_emoji_map = {
    '❌': '[ERR]',
    '✅': '[OK]',
    '⚠️': '[WARN]',
    '📁': '[FILE]',
    '📥': '[DL]',
    '📐': '[IMG]',
    '🏷️': '[TAG]',
    '✨': '[NEW]',
    '🔍': '[SCAN]',
    '📊': '[STAT]',
    '🤖': '[AI]',
    '📢': '[MSG]',
    '📦': '[PKG]',
    '📝': '[NOTE]',
    '💡': '[TIP]',
    '🔧': '[FIX]',
    '💰': '[$$]',
    '💵': '[$$]',
    '🚀': '[>>]',
    '📈': '[UP]',
    '🔗': '[LINK]',
    '📸': '[IMG]',
    '🧠': '[AI]',
    '📆': '[DATE]',
    '📅': '[DATE]',
}

# Replace in HTML contexts (inline templates in f-strings and heredocs)
count = 0
for emoji, replacement in html_emoji_map.items():
    if emoji in content:
        c = content.count(emoji)
        content = content.replace(emoji, replacement)
        count += c

# Handle special cases:
# Status emoji that should be simple dots
content = content.replace("'status': '\U0001f7e2 Online'", "'status': 'Online'")
content = content.replace("'\U0001f7e1 Skonfiguruj'", "'Skonfiguruj'")
content = content.replace("'\u26aa Offline'", "'Offline'")

# Icon for user (dziadek/babcia) - replace with initials
content = content.replace("icon = '\U0001f474' if user == 'dziadek' else '\U0001f475'", "icon = 'D' if user == 'dziadek' else 'B'")

# Print statements - these go to console, use text markers
for emoji, txt in print_emoji_map.items():
    # Only replace in print() contexts - use regex
    content = content.replace(f'print(f"{emoji}', f'print(f"{txt}')
    content = content.replace(f'print(f"    {emoji}', f'print(f"    {txt}')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"Replaced {count} emoji in HTML contexts in app.py")

# Verify remaining
remaining = len(re.findall(r'[\U0001F300-\U0001F9FF\U00002600-\U000027BF]', content))
print(f"Remaining emoji in app.py: {remaining}")
