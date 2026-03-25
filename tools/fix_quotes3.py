"""Smart fix: detect string context and use appropriate quotes for material-symbols spans.
In double-quoted strings -> use single quotes in span.
In single-quoted strings -> use double quotes in span.
In print() with no f-string -> simplify to text."""
import os, re, sys

sys.stdout.reconfigure(encoding='utf-8')

SPAN_SINGLE = "<span class='material-symbols-outlined' style='font-size:inherit;vertical-align:middle'>"
SPAN_DOUBLE = '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">'
SPAN_SINGLE_GREEN = "<span class='material-symbols-outlined' style='font-size:inherit;vertical-align:middle;color:#22c55e'>"
SPAN_DOUBLE_GREEN = '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#22c55e">'
SPAN_SINGLE_RED = "<span class='material-symbols-outlined' style='font-size:inherit;vertical-align:middle;color:#ef4444'>"
SPAN_DOUBLE_RED = '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#ef4444">'
SPAN_SINGLE_BLUE = "<span class='material-symbols-outlined' style='font-size:inherit;vertical-align:middle;color:#3b82f6'>"
SPAN_DOUBLE_BLUE = '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#3b82f6">'

# For print statements, just remove HTML spans entirely and use icon name in brackets
ICON_PATTERN = re.compile(r"<span class=['\"]material-symbols-outlined['\"] style=['\"]font-size:inherit;vertical-align:middle(?:;color:#[0-9a-f]+)?['\"]>(\w+)</span>")

def simplify_for_print(line):
    """Replace material-symbols spans with [ICON_NAME] for print statements."""
    return ICON_PATTERN.sub(lambda m: f'[{m.group(1).upper()[:6]}]', line)

files = ['app.py']
for root, dirs, fnames in os.walk('modules'):
    for f in fnames:
        if f.endswith('.py'):
            files.append(os.path.join(root, f))

total_fixed = 0
for filepath in files:
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    file_fixes = 0

    for line in lines:
        if 'material-symbols-outlined' not in line:
            new_lines.append(line)
            continue

        stripped = line.lstrip()

        # Case 1: print() statement - simplify to text markers
        if stripped.startswith('print(') or stripped.startswith('print ('):
            old = line
            line = simplify_for_print(line)
            if line != old:
                file_fixes += 1
            new_lines.append(line)
            continue

        # Case 2: Comment line - simplify
        if stripped.startswith('#'):
            old = line
            line = simplify_for_print(line)
            if line != old:
                file_fixes += 1
            new_lines.append(line)
            continue

        # Case 3: Detect string quote type on this line
        # Look for the string opening before the span
        # If line uses f"..." or "..." -> span must use single quotes
        # If line uses f'...' or '...' -> span must use double quotes

        # Simple heuristic: check what quote type appears before "material-symbols"
        mat_idx = line.find('material-symbols')
        if mat_idx == -1:
            new_lines.append(line)
            continue

        # Look backwards from mat_idx to find the enclosing string delimiter
        prefix = line[:mat_idx]

        # Count unescaped quotes to determine context
        # Find the last unmatched quote before the span
        single_count = 0
        double_count = 0
        last_quote = None
        for i, ch in enumerate(prefix):
            if ch == "'" and (i == 0 or prefix[i-1] != '\\'):
                single_count += 1
                last_quote = "'"
            elif ch == '"' and (i == 0 or prefix[i-1] != '\\'):
                double_count += 1
                last_quote = '"'

        # If odd number of single quotes -> we're inside a single-quoted string
        # If odd number of double quotes -> we're inside a double-quoted string
        in_single = (single_count % 2 == 1)
        in_double = (double_count % 2 == 1)

        old = line
        if in_single and not in_double:
            # Inside single-quoted string -> span needs double quotes
            line = line.replace(SPAN_SINGLE, SPAN_DOUBLE)
            line = line.replace(SPAN_SINGLE_GREEN, SPAN_DOUBLE_GREEN)
            line = line.replace(SPAN_SINGLE_RED, SPAN_DOUBLE_RED)
            line = line.replace(SPAN_SINGLE_BLUE, SPAN_DOUBLE_BLUE)
        elif in_double and not in_single:
            # Inside double-quoted string -> span needs single quotes
            line = line.replace(SPAN_DOUBLE, SPAN_SINGLE)
            line = line.replace(SPAN_DOUBLE_GREEN, SPAN_SINGLE_GREEN)
            line = line.replace(SPAN_DOUBLE_RED, SPAN_SINGLE_RED)
            line = line.replace(SPAN_DOUBLE_BLUE, SPAN_SINGLE_BLUE)
        # else: mixed or unclear context, leave as-is

        if line != old:
            file_fixes += 1

        new_lines.append(line)

    if file_fixes > 0:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        total_fixed += file_fixes
        print(f"  {filepath}: {file_fixes} fixes")

print(f"\nTotal: {total_fixed} lines fixed")
