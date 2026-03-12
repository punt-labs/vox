---
description: "Test AskUserQuestion inside a command"
allowed-tools: ["AskUserQuestion"]
---

# /ask-test-dev

Test whether AskUserQuestion renders inside command execution.

## Implementation

Call AskUserQuestion with:
- question: "Pick a color"
- options: ["Red", "Green", "Blue"]

Then report the result: which option was selected, or whether the dialog returned empty/failed.
