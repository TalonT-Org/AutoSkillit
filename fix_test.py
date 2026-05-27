import re

with open('tests/server/test_tools_bootstrap.py', 'r') as f:
    content = f.read()

# Find and replace the broken multi-line string
# The string spans lines 267-270 where the body has actual newlines instead of \n
old_pattern = '"body": "## Review Approach\n\n<!-- review_approach: true -->\n> ..."'
new_pattern = '"body": "## Review Approach\\n\\n<!-- review_approach: true -->\\n> ..."'

content = content.replace(old_pattern, new_pattern)

with open('tests/server/test_tools_bootstrap.py', 'w') as f:
    f.write(content)

print("Fixed")
