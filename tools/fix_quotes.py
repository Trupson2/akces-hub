"""Fix quote conflicts: material-symbols spans inside f-strings need single quotes."""
import os, re, sys

sys.stdout.reconfigure(encoding='utf-8')

# The span with double quotes
OLD_SPAN = '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">'
# Replace with single quotes
NEW_SPAN = "<span class='material-symbols-outlined' style='font-size:inherit;vertical-align:middle'>"

# Also handle the colored variants
OLD_SPAN_GREEN = '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#22c55e">'
NEW_SPAN_GREEN = "<span class='material-symbols-outlined' style='font-size:inherit;vertical-align:middle;color:#22c55e'>"

OLD_SPAN_RED = '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#ef4444">'
NEW_SPAN_RED = "<span class='material-symbols-outlined' style='font-size:inherit;vertical-align:middle;color:#ef4444'>"

OLD_SPAN_BLUE = '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#3b82f6">'
NEW_SPAN_BLUE = "<span class='material-symbols-outlined' style='font-size:inherit;vertical-align:middle;color:#3b82f6'>"

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

        # Detect if this line is inside an f-string with double quotes
        stripped = line.lstrip()

        # Check for f"..." context (double-quoted f-string)
        is_fdouble = bool(re.search(r'f".*material-symbols', line))
        # Check for triple-quoted f-string
        is_ftriple = bool(re.search(r'f""".*material-symbols', line))
        # Also check if the line is a continuation of a f"..." string
        # (has material-symbols but line starts with content, not assignment)

        if is_fdouble and not is_ftriple:
            # Replace double quotes in span with single quotes
            if OLD_SPAN in line:
                line = line.replace(OLD_SPAN, NEW_SPAN)
                file_fixes += 1
            if OLD_SPAN_GREEN in line:
                line = line.replace(OLD_SPAN_GREEN, NEW_SPAN_GREEN)
                file_fixes += 1
            if OLD_SPAN_RED in line:
                line = line.replace(OLD_SPAN_RED, NEW_SPAN_RED)
                file_fixes += 1
            if OLD_SPAN_BLUE in line:
                line = line.replace(OLD_SPAN_BLUE, NEW_SPAN_BLUE)
                file_fixes += 1

        new_lines.append(line)

    if file_fixes > 0:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        total_fixed += file_fixes
        print(f"  {filepath}: {file_fixes} quote fixes")

print(f"\nTotal: {total_fixed} lines fixed")
