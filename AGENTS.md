# Agents Guidelines

## "bug" shortcut

When the user types just **`bug`** (or similar shorthand), do the following:

1. Read the tail of `server.log` to find the most recent request/response cycle.
2. Analyze the log entries for that request to identify what went wrong (errors, unexpected behavior, failed parsing, etc.).
3. Present a summary of the bug to the user, including the question asked, what happened, and any error details.
