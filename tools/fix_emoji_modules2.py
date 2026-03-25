"""Second pass: fix remaining emoji in modules."""
import re, os, sys

sys.stdout.reconfigure(encoding='utf-8')

# Additional emoji map
EXTRA_HTML = {
    '\U0001F50C': 'power',           # 🔌
    '\U0001F6E1': 'shield',          # 🛡
    '\U0001F4E1': 'satellite_alt',   # 📡
    '\U0001F319': 'dark_mode',       # 🌙
    '\U0001F514': 'notifications',   # 🔔
    '\U0001F3ED': 'factory',         # 🏭
    '\U0001F52C': 'biotech',         # 🔬
    '\U0001F4F9': 'videocam',        # 📹
    '\U0001F3A8': 'palette',         # 🎨
    '\U0001F4B1': 'currency_exchange',# 💱
    '\U0001F4CF': 'straighten',      # 📏
    '\U0001F947': 'emoji_events',    # 🥇
    '\U0001F4FA': 'tv',              # 📺
    '\U0001F457': 'checkroom',       # 👗
    '\U0001F4E7': 'email',           # 📧
    '\U0001F45F': 'steps',           # 👟
    '\U0001F948': 'emoji_events',    # 🥈
    '\U0001F949': 'emoji_events',    # 🥉
    '\u2601': 'cloud',               # ☁
    '\U0001F6AB': 'block',           # 🚫
    '\U0001F50B': 'battery_full',    # 🔋
    '\U0001F455': 'checkroom',       # 👕
    '\U0001F460': 'steps',           # 👠
    '\U0001F3AE': 'sports_esports',  # 🎮
    '\U0001F393': 'school',          # 🎓
    '\U0001F4BF': 'album',           # 💿
    '\U0001F3A5': 'movie',           # 🎥
    '\U0001F50A': 'volume_up',       # 🔊
    '\U0001F4F2': 'smartphone',      # 📲
    '\U0001F3A7': 'headphones',      # 🎧
    '\U0001F4A4': 'bedtime',         # 💤
    '\U0001F4A8': 'air',             # 💨
    '\U0001F9F4': 'sanitizer',       # 🧴
    '\U0001F9F9': 'mop',             # 🧹
    '\U0001F9F2': 'attractions',     # 🧲
    '\U0001F9F0': 'handyman',        # 🧰
    '\U0001F9EA': 'science',         # 🧪
    '\U0001F4A0': 'diamond',         # 💠
    '\U0001F4C7': 'contact_page',    # 📇
    '\U0001F6CD': 'shopping_bag',    # 🛍
    '\U0001F45C': 'shopping_bag',    # 👜
    '\U0001F4F0': 'newspaper',       # 📰
    '\U0001F9F1': 'view_module',     # 🧱
}

# Colored circles -> CSS dots
CIRCLE_MAP = {
    '\U0001F534': '<span style="color:#ef4444">\\u25CF</span>',  # 🔴
    '\U0001F7E2': '<span style="color:#22c55e">\\u25CF</span>',  # 🟢
    '\U0001F7E1': '<span style="color:#eab308">\\u25CF</span>',  # 🟡
    '\U0001F535': '<span style="color:#3b82f6">\\u25CF</span>',  # 🔵
    '\U0001F7E0': '<span style="color:#f97316">\\u25CF</span>',  # 🟠
    '\U0001F7E3': '<span style="color:#a855f7">\\u25CF</span>',  # 🟣
    '\u26AA': '<span style="color:#6b7280">\\u25CF</span>',      # ⚪
    '\u26AB': '<span style="color:#1f2937">\\u25CF</span>',      # ⚫
}

def make_span(icon_name):
    return f'<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">{icon_name}</span>'

total = 0

for root, dirs, files in os.walk('modules'):
    for fname in files:
        if not fname.endswith('.py'): continue
        filepath = os.path.join(root, fname)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        original = content

        lines = content.split('\n')
        new_lines = []
        file_count = 0

        for line in lines:
            stripped = line.strip()
            is_print = stripped.startswith('print(') or stripped.startswith('print (')
            is_comment = stripped.startswith('#')

            if is_print or is_comment:
                for emoji_char, icon_name in EXTRA_HTML.items():
                    if emoji_char in line:
                        c = line.count(emoji_char)
                        line = line.replace(emoji_char, f'[{icon_name.upper()[:4]}]')
                        file_count += c
                for emoji_char, repl in CIRCLE_MAP.items():
                    if emoji_char in line:
                        c = line.count(emoji_char)
                        line = line.replace(emoji_char, '*')
                        file_count += c
            else:
                for emoji_char, icon_name in EXTRA_HTML.items():
                    if emoji_char in line:
                        c = line.count(emoji_char)
                        line = line.replace(emoji_char, make_span(icon_name))
                        file_count += c
                for emoji_char, repl in CIRCLE_MAP.items():
                    if emoji_char in line:
                        c = line.count(emoji_char)
                        # Use actual unicode bullet for HTML
                        color = re.search(r'color:([^"]+)', repl)
                        line = line.replace(emoji_char, '\u25CF')
                        file_count += c

            # Remove orphan variation selectors
            line = line.replace('\uFE0F', '')

            new_lines.append(line)

        new_content = '\n'.join(new_lines)
        if new_content != original:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            total += file_count
            if file_count > 0:
                print(f"  {filepath}: {file_count}")

print(f"\nTotal: {total} replaced")

# Final count
count = 0
for root, dirs, files in os.walk('modules'):
    for fname in files:
        if not fname.endswith('.py'): continue
        with open(os.path.join(root, fname), 'r', encoding='utf-8') as f:
            c = f.read()
        found = re.findall(r'[\U0001F300-\U0001F9FF\U00002600-\U000027BF]', c)
        if found:
            count += len(found)
print(f"Remaining: {count}")
