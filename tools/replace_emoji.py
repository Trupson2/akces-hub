"""Batch replace emoji with Material Symbols across codebase."""
import os

EMOJI_MAP = {
    '\U0001f4e6': 'inventory_2',     # 📦
    '\U0001f4cb': 'list_alt',        # 📋
    '\U0001f50d': 'search',          # 🔍
    '\U0001f4ca': 'bar_chart',       # 📊
    '\U0001f4b0': 'paid',            # 💰
    '\U0001f6d2': 'shopping_cart',    # 🛒
    '\u2705': 'check_circle',        # ✅
    '\u274c': 'cancel',              # ❌
    '\u26a0\ufe0f': 'warning',       # ⚠️
    '\U0001f4f8': 'photo_camera',    # 📸
    '\U0001f5a8\ufe0f': 'print',     # 🖨️
    '\u270f\ufe0f': 'edit',          # ✏️
    '\U0001f5d1\ufe0f': 'delete',    # 🗑️
    '\U0001f4f1': 'smartphone',      # 📱
    '\U0001f4b5': 'payments',        # 💵
    '\U0001f4cd': 'pin_drop',        # 📍
    '\U0001f504': 'sync',            # 🔄
    '\U0001f3af': 'target',          # 🎯
    '\U0001f527': 'build',           # 🔧
    '\u2b50': 'star',                # ⭐
    '\U0001f3f7\ufe0f': 'label',     # 🏷️
    '\U0001f4c2': 'folder',          # 📂
    '\U0001f4e5': 'download',        # 📥
    '\U0001f4e4': 'upload',          # 📤
    '\U0001f4be': 'save',            # 💾
    '\U0001f69a': 'local_shipping',  # 🚚
    '\U0001f4b8': 'money_off',       # 💸
    '\U0001f91d': 'handshake',       # 🤝
    '\U0001f5bc\ufe0f': 'photo_library', # 🖼️
    '\u23f3': 'hourglass_top',       # ⏳
    '\U0001f4c5': 'calendar_month',  # 📅
    '\U0001f4dd': 'edit_note',       # 📝
    '\u2611\ufe0f': 'check_box',     # ☑️
    '\u2b07\ufe0f': 'arrow_downward',# ⬇️
    '\u2b06\ufe0f': 'arrow_upward',  # ⬆️
    '\U0001f195': 'add_circle',      # 🆕
    '\U0001f4e2': 'campaign',        # 📢
    '\U0001f514': 'notifications',   # 🔔
    '\U0001f916': 'smart_toy',       # 🤖
    '\U0001f680': 'rocket_launch',   # 🚀
    '\U0001f389': 'celebration',     # 🎉
    '\U0001f517': 'link',            # 🔗
    '\U0001f534': 'fiber_manual_record', # 🔴
    '\U0001f4ee': 'markunread_mailbox', # 📮
    '\u21a9\ufe0f': 'undo',          # ↩️
    '\U0001f48e': 'diamond',         # 💎
    '\U0001f4a5': 'error',           # 💥
    '\u267b\ufe0f': 'recycling',     # ♻️
    '\u26a1': 'bolt',                # ⚡
    '\U0001f4eb': 'inbox',           # 📫
    '\u2728': 'auto_awesome',        # ✨
    '\U0001f6e1\ufe0f': 'shield',    # 🛡️
    '\U0001f4f2': 'install_mobile',  # 📲
    '\U0001f511': 'key',             # 🔑
    '\u2699\ufe0f': 'settings',      # ⚙️
    '\U0001f3e2': 'business',        # 🏢
    '\U0001f4cc': 'push_pin',        # 📌
    '\U0001f5fa\ufe0f': 'map',       # 🗺️
    '\U0001f4f7': 'photo_camera',    # 📷
    '\U0001f510': 'lock',            # 🔐
    '\U0001f4c8': 'trending_up',     # 📈
    '\U0001f4c9': 'trending_down',   # 📉
    '\U0001f3f7': 'label',           # 🏷
}

MS_TPL = '<span class="material-symbols-outlined" style="font-size:1rem;vertical-align:middle">{}</span>'

files = [
    'modules/magazynier.py',
    'modules/paletomat.py',
    'modules/palety.py',
    'modules/sprzedaze.py',
    'modules/analityka.py',
    'modules/serwisant.py',
    'modules/wysylki.py',
    'templates/home.html',
    'templates/narzedzia.html',
    'templates/pakowanie.html',
    'templates/raporty.html',
    'templates/setup.html',
    'templates/generator.html',
    'templates/cloud_export.html',
    'templates/warehouse_editor.html',
    'templates/warehouse_heatmap.html',
    'templates/admin_subscriptions.html',
    'templates/changelog.html',
    'templates/export.html',
    'templates/kalkulator.html',
    'templates/licencje.html',
    'templates/plan_upgrade.html',
    'templates/powiadomienia.html',
    'templates/wysylki.html',
    'templates/stitch_pallet_details.html',
]

total = 0
for fpath in files:
    if not os.path.exists(fpath):
        continue
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()
    count = 0
    for emoji_char, icon_name in EMOJI_MAP.items():
        n = content.count(emoji_char)
        if n > 0:
            content = content.replace(emoji_char, MS_TPL.format(icon_name))
            count += n
    if count > 0:
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'{fpath}: {count} emoji replaced')
        total += count

print(f'--- Total: {total} emoji replaced ---')
