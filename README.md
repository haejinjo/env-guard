# env-guard

Keeps the secrets in your `.env` files out of your AI coding assistant's context, in Claude Code and Codex.

## Why

A `.env` file holds the secret values your code needs, like API keys and database passwords. Most developers know to keep it out of GitHub with `.gitignore`. AI coding assistants need the same protection: once a value shows up in the chat, the AI model has seen it.

Writing "never read .env" in CLAUDE.md or AGENTS.md isn't enough. The model may forget, or run a command that prints the file anyway. For example, a command that hides lines containing `TOKEN`, `SECRET` or `KEY` still shows a key named `SOME_COMPANY_NAME_JWT`.

## How it works

env-guard is a hook: a small program the app runs automatically before and after every action the assistant takes. The app runs it, not the AI, so the AI can't skip it.

1. **Blocks the obvious ways in.** Before each action, it checks whether the assistant is about to open a `.env` file. If so, it stops the action and shows the assistant a safe way to check a key instead.
2. **Hides anything that slips through.** After each action, it looks for your `.env` values in the output and replaces each one with a label like `[REDACTED:API_KEY]`.
3. **Blocks everything if it can't run.** If Python is missing, actions are blocked instead of allowed.

The assistant can still check whether a key is set (`grep -q '^API_KEY=.\+' .env`) or list key names (`cut -d= -f1 .env`).

## Install

### Claude Code

Type this in Claude Code:

```
/plugin install env-guard --marketplace [TBD repo]
```

Answer `y` to add the marketplace, then pick a scope. "User" protects every project.

### Codex

Codex's command-line app doesn't currently run hooks that come from plugins, so Codex uses a manual install:

1. Copy `hooks/env-guard.py` to `~/.codex/hooks/env-guard.py`.
2. Add this to `~/.codex/hooks.json` (merge it if the file already exists):

   ```json
   {
     "hooks": {
       "PreToolUse": [{ "matcher": "Bash", "hooks": [{ "type": "command", "command": "python3 \"$HOME/.codex/hooks/env-guard.py\" || exit 2", "timeout": 10 }] }],
       "PostToolUse": [{ "matcher": "Bash", "hooks": [{ "type": "command", "command": "python3 \"$HOME/.codex/hooks/env-guard.py\" || exit 2", "timeout": 10 }] }]
     }
   }
   ```

3. Type `/hooks` in Codex and approve the new hook, then restart Codex.

Requires Python 3.9 or newer.

## What it doesn't catch

- A value a program rewrote into another form before printing it. It recognizes the original value, plus its base64 and URL-encoded forms.
- Values shorter than 8 characters, like `3000` or `true`. Hiding every "3000" would break normal output.
- Secrets stored outside your project's `.env` files.
- Log files on your machine. The assistant sees the hidden version, but the original output can still be in local logs.

## Test

```
python3 tests/test_env_guard.py
```

## License

MIT
