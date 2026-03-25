"""Fix Telegram messages: replace <i class=mi>icon</i> with unicode emoji.
Telegram only supports <b>, <i>, <code>, <a> HTML tags.
<i class=mi> renders as literal text in Telegram."""
import re, sys

sys.stdout.reconfigure(encoding='utf-8')

# Map material symbol names back to emoji for Telegram context
ICON_TO_EMOJI = {
    'notifications': '\U0001F514',  # 🔔
    'payments': '\U0001F4B0',       # 💰
    'inventory_2': '\U0001F4E6',    # 📦
    'paid': '\U0001F4B5',           # 💵
    'person': '\U0001F464',         # 👤
    'location_on': '\U0001F4CD',    # 📍
    'warning': '\u26A0\uFE0F',     # ⚠️
    'bar_chart': '\U0001F4CA',      # 📊
    'sell': '\U0001F3F7\uFE0F',    # 🏷️
    'edit_note': '\U0001F4DD',      # 📝
    'today': '\U0001F4C5',          # 📅
    'emoji_events': '\U0001F3C6',   # 🏆
    'trending_up': '\U0001F4C8',    # 📈
    'auto_awesome': '\u2728',       # ✨
    'celebration': '\U0001F389',    # 🎉
    'science': '\U0001F9EA',        # 🧪
    'search': '\U0001F50D',         # 🔍
    'save': '\U0001F4BE',           # 💾
    'sync': '\U0001F504',           # 🔄
    'mop': '\U0001F9F9',            # 🧹
    'link': '\U0001F517',           # 🔗
    'upload': '\U0001F4E4',         # 📤
    'check_circle': '\u2705',       # ✅
    'satellite_alt': '\U0001F4E1',  # 📡
    'settings': '\u2699\uFE0F',    # ⚙️
    'assignment': '\U0001F4CB',     # 📋
    'smart_toy': '\U0001F916',      # 🤖
    'smartphone': '\U0001F4F1',     # 📱
    'download': '\U0001F4E5',       # 📥
    'markunread_mailbox': '\U0001F4EC', # 📬
    'local_shipping': '\U0001F69A', # 🚚
    'pin_drop': '\U0001F4CD',       # 📍
    'check': '\u2705',              # ✅
    'cancel': '\u274C',             # ❌
}

# Pattern to match <i class=mi>icon_name</i>
MI_PATTERN = re.compile(r'<i class=mi>(\w+)</i>')
# Also match <span class=material-symbols-outlined style=font-size:1rem>icon</span>
SPAN_PATTERN = re.compile(r'<span class=material-symbols-outlined[^>]*>(\w+)</span>')
# Also match <span class="material-symbols-outlined" ...>icon</span>
SPAN2_PATTERN = re.compile(r'<span class=["\']material-symbols-outlined["\'][^>]*>(\w+)</span>')

def replace_icon(match):
    icon = match.group(1)
    return ICON_TO_EMOJI.get(icon, '')

# Fix telegram_bot.py - only message-building lines (not HTML templates)
with open('modules/telegram_bot.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
count = 0

# Lines that build Telegram messages (msg += or msg = in notification functions)
# vs lines that build HTML for web pages
# We identify message lines by: they contain msg += or msg = and have <i class=mi>

for i, line in enumerate(lines):
    # Skip HTML template sections (inside triple-quoted strings for web pages)
    stripped = line.strip()

    # These are Telegram message builders - fix them
    is_msg_line = ('msg +=' in line or 'msg = f"' in line or 'msg = f\'' in line or
                   "msg += f'" in line or 'msg += f"' in line or
                   "'text':" in line)

    # Also fix console.log and statusEl lines that shouldn't have HTML
    is_js_line = ('console.log' in line or 'textContent' in line)

    if (is_msg_line or is_js_line) and ('<i class=mi>' in line or '<span class=material' in line):
        old = line
        line = MI_PATTERN.sub(replace_icon, line)
        line = SPAN_PATTERN.sub(replace_icon, line)
        line = SPAN2_PATTERN.sub(replace_icon, line)
        if line != old:
            count += 1

    new_lines.append(line)

with open('modules/telegram_bot.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"Fixed {count} Telegram message lines")

# Also fix allegro_api.py sync messages
with open('modules/allegro_api.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix Telegram sync messages (search for msg = and msg += with <i class=mi>)
lines2 = content.split('\n')
new_lines2 = []
count2 = 0
for line in lines2:
    if ('msg +=' in line or 'msg = f"' in line or "msg = f'" in line or 'msg += f"' in line or "msg += f'" in line):
        if '<i class=mi>' in line or '<span class=material' in line:
            old = line
            line = MI_PATTERN.sub(replace_icon, line)
            line = SPAN_PATTERN.sub(replace_icon, line)
            if line != old:
                count2 += 1
    new_lines2.append(line)

with open('modules/allegro_api.py', 'w', encoding='utf-8') as f:
    f.write('\n'.join(new_lines2))
print(f"Fixed {count2} Allegro sync message lines")

# Fix wysylki.py pack_hint
with open('modules/wysylki.py', 'r', encoding='utf-8') as f:
    content = f.read()
old = content
content = content.replace(
    "<span class=material-symbols-outlined style=font-size:1rem>markunread_mailbox</span>",
    "\U0001F4EC"  # 📬
)
if content != old:
    with open('modules/wysylki.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Fixed wysylki.py pack_hint icons")
