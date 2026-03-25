"""Replace emoji in ALL modules/ .py files - both HTML and print contexts."""
import re, os, sys

sys.stdout.reconfigure(encoding='utf-8')

# HTML context: emoji -> Material Symbols span
HTML_MAP = {
    '\U0001F4E6': 'inventory_2',     # 📦
    '\U0001F5C4': 'dns',             # 🗄️ (with VS16)
    '\U0001F4CB': 'assignment',      # 📋
    '\U0001F504': 'sync',            # 🔄
    '\U0001F50D': 'search',          # 🔍
    '\U0001F3EA': 'store',           # 🏪
    '\U0001F3AA': 'storefront',      # 🎪
    '\U0001F4CA': 'bar_chart',       # 📊
    '\U0001F4C5': 'today',           # 📅
    '\U0001F4C6': 'calendar_month',  # 📆
    '\u2728': 'auto_awesome',        # ✨
    '\U0001F916': 'smart_toy',       # 🤖
    '\U0001F4C8': 'trending_up',     # 📈
    '\U0001F4C9': 'trending_down',   # 📉
    '\U0001F4B0': 'payments',        # 💰
    '\U0001F4B5': 'paid',            # 💵
    '\U0001F4C1': 'folder',          # 📁
    '\U0001F4C2': 'folder_open',     # 📂
    '\U0001F3C6': 'emoji_events',    # 🏆
    '\U0001F3AF': 'adjust',          # 🎯
    '\u2699\uFE0F': 'settings',      # ⚙️
    '\u2699': 'settings',            # ⚙ (no VS16)
    '\U0001F512': 'lock',            # 🔐🔒
    '\U0001F513': 'lock_open',       # 🔓
    '\U0001F4E2': 'campaign',        # 📢
    '\U0001F4DD': 'edit_note',       # 📝
    '\U0001F4A1': 'lightbulb',       # 💡
    '\U0001F550': 'schedule',        # 🕐
    '\U0001F527': 'build',           # 🔧
    '\u26A1': 'bolt',                # ⚡
    '\U0001F680': 'rocket_launch',   # 🚀
    '\U0001F6D2': 'shopping_cart',   # 🛒
    '\U0001F3F7\uFE0F': 'sell',      # 🏷️
    '\U0001F3F7': 'sell',            # 🏷 (no VS16)
    '\U0001F4F7': 'photo_camera',    # 📷
    '\U0001F4F8': 'photo_camera',    # 📸
    '\U0001F389': 'celebration',     # 🎉
    '\U0001F4B3': 'credit_card',     # 💳
    '\U0001F4CC': 'push_pin',        # 📌
    '\U0001F5BC\uFE0F': 'image',     # 🖼️
    '\U0001F5BC': 'image',           # 🖼
    '\U0001F5D1\uFE0F': 'delete',    # 🗑️
    '\U0001F5D1': 'delete',          # 🗑
    '\U0001F4BE': 'save',            # 💾
    '\U0001F517': 'link',            # 🔗
    '\U0001F4C4': 'description',     # 📄
    '\U0001F516': 'bookmark',        # 🔖
    '\U0001F503': 'autorenew',       # 🔃
    '\u23F1\uFE0F': 'timer',         # ⏱️
    '\u23F1': 'timer',               # ⏱
    '\U0001F4C3': 'article',         # 📃
    '\U0001F4D0': 'straighten',      # 📐
    '\U0001F9E0': 'psychology',      # 🧠
    '\u26A0\uFE0F': 'warning',       # ⚠️
    '\u26A0': 'warning',             # ⚠
    '\u270F\uFE0F': 'edit',          # ✏️
    '\u270F': 'edit',                # ✏
    '\U0001F5A8\uFE0F': 'print',     # 🖨️
    '\U0001F5A8': 'print',           # 🖨
    '\U0001F511': 'key',             # 🔑
    '\U0001F310': 'language',        # 🌐
    '\U0001F4E5': 'download',        # 📥
    '\U0001F4E4': 'upload',          # 📤
    '\U0001F6AA': 'logout',          # 🚪
    '\U0001F697': 'directions_car',  # 🚗
    '\U0001F464': 'person',          # 👤
    '\U0001F4CD': 'location_on',     # 📍
    '\U0001F9EA': 'science',         # 🧪
    '\U0001F4F1': 'smartphone',      # 📱
    '\U0001F525': 'local_fire_department', # 🔥
    '\U0001F4A5': 'flash_on',        # 💥
    '\U0001F48E': 'diamond',         # 💎
    '\U0001F3B2': 'casino',          # 🎲
    '\U0001F31F': 'star',            # 🌟
    '\U0001F451': 'crown',           # 👑 (not in material but using)
    '\u2B50': 'star',                # ⭐
    '\U0001F9F9': 'mop',             # 🧹
    '\U0001F69C': 'agriculture',     # 🚜
    '\U0001F4AC': 'chat',            # 💬
    '\U0001F4E9': 'mail',            # 📩
    '\U0001F4EC': 'mail',            # 📬
    '\U0001F44D': 'thumb_up',        # 👍
    '\U0001F4DE': 'call',            # 📞
    '\U0001F4BB': 'computer',        # 💻
    '\U0001F3E0': 'home',            # 🏠
    '\U0001F6E0\uFE0F': 'handyman',  # 🛠️
    '\U0001F6E0': 'handyman',        # 🛠
    '\U0001F4DA': 'menu_book',       # 📚
    '\U0001F4D6': 'auto_stories',    # 📖
    '\U0001F4E0': 'fax',             # 📠
    '\U0001F9FE': 'receipt_long',    # 🧾
}

# Simple check/cross replacements
CHECK_MAP = {
    '\u2705': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#22c55e">check_circle</span>',  # ✅
    '\u274C': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#ef4444">cancel</span>',  # ❌
    '\u2764': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">favorite</span>',  # ❤
    '\U0001F441': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">visibility</span>',  # 👁
    '\u2795': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">add</span>',  # ➕
    '\u2796': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">remove</span>',  # ➖
    '\u2139\uFE0F': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#3b82f6">info</span>',  # ℹ️
    '\u2139': '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#3b82f6">info</span>',  # ℹ
}

# Print-only replacements (simple text markers)
PRINT_MAP = {
    '\u2705': '[OK]',    # ✅
    '\u274C': '[ERR]',   # ❌
    '\u26A0': '[WARN]',  # ⚠
}

def make_span(icon_name):
    return f'<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">{icon_name}</span>'

total_files = 0
total_replaced = 0

for root, dirs, files in os.walk('modules'):
    for fname in files:
        if not fname.endswith('.py'):
            continue
        filepath = os.path.join(root, fname)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        original = content

        # Process line by line to distinguish print vs HTML context
        lines = content.split('\n')
        new_lines = []
        file_count = 0

        for line in lines:
            stripped = line.strip()
            is_print = stripped.startswith('print(') or stripped.startswith('print (')
            is_comment = stripped.startswith('#')

            if is_print or is_comment:
                # Replace emoji with text markers in print/comment
                for emoji_char, text in PRINT_MAP.items():
                    if emoji_char in line:
                        c = line.count(emoji_char)
                        line = line.replace(emoji_char, text)
                        file_count += c
                # Also replace other emoji with simple text in prints
                for emoji_char, icon_name in HTML_MAP.items():
                    if emoji_char in line:
                        c = line.count(emoji_char)
                        line = line.replace(emoji_char, f'[{icon_name.upper()[:4]}]')
                        file_count += c
            else:
                # HTML context - use Material Symbols spans
                for emoji_char, icon_name in HTML_MAP.items():
                    if emoji_char in line:
                        c = line.count(emoji_char)
                        line = line.replace(emoji_char, make_span(icon_name))
                        file_count += c
                for emoji_char, replacement in CHECK_MAP.items():
                    if emoji_char in line:
                        c = line.count(emoji_char)
                        line = line.replace(emoji_char, replacement)
                        file_count += c

            new_lines.append(line)

        new_content = '\n'.join(new_lines)

        if new_content != original:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            total_files += 1
            total_replaced += file_count
            print(f"  {filepath}: {file_count} replacements")

print(f"\nTotal: {total_replaced} emoji replaced across {total_files} files")

# Verify
count = 0
for root, dirs, files in os.walk('modules'):
    for fname in files:
        if not fname.endswith('.py'): continue
        with open(os.path.join(root, fname), 'r', encoding='utf-8') as f:
            c = f.read()
        count += len(re.findall(r'[\U0001F300-\U0001F9FF\U00002600-\U000027BF]', c))
print(f"Remaining emoji in modules: {count}")
