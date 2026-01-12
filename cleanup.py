#!/usr/bin/env python3
"""Clean up linting issues"""

# Corriger consumer.py - enlever espaces blancs
with open('backend/workers/consumer.py', 'r') as f:
    lines = f.readlines()

cleaned_lines = []
for line in lines:
    cleaned_lines.append(line.rstrip() + '\n')

with open('backend/workers/consumer.py', 'w') as f:
    f.writelines(cleaned_lines)

print("✓ Espaces blancs supprimés dans consumer.py")

# Corriger frontend/app.py - enlever espaces blancs
with open('frontend/app.py', 'r') as f:
    lines = f.readlines()

cleaned_lines = []
for line in lines:
    cleaned_lines.append(line.rstrip() + '\n')

while cleaned_lines and cleaned_lines[-1].strip() == '':
    cleaned_lines.pop()

if cleaned_lines:
    cleaned_lines.append('\n')

with open('frontend/app.py', 'w') as f:
    f.writelines(cleaned_lines)

print("✓ Espaces blancs supprimés dans frontend/app.py")
