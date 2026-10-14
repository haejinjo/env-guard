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

1. In Claude Code, type:

   ```
   /plugin install env-guard --marketplace [TBD repo]
   ```

2. Answer `y` when it asks to add the marketplace. This only tells Claude Code where to download the plugin from.
3. Choose where it applies:

   | Scope | Protects | Choose it when |
   |---|---|---|
   | **User** (recommended) | Every project on your computer | You want it on everywhere, set once |
   | **Project** | This project, for everyone who works on it | Your whole team should have it. It's saved in the project's shared settings, so teammates get it too |
   | **Local** | This project, only for you | You want to try it in one place first |

4. Start a new session. The guard only applies to sessions started after you install it.

#### How to know it's working

Run `claude plugin list` in a terminal. You should see:

```
env-guard@env-guard
  Version: 0.1.0
  Scope:   user
  Status:  ✔ enabled
```

After that, whenever the assistant tries to read your `.env`, you'll see a message starting with `Blocked by env-guard`. If a key ever shows up in a command's output, the assistant sees `[REDACTED:KEY_NAME]` in its place. To turn it off, open `/plugin` in Claude Code and disable env-guard.

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

### Want proof it works?

After installing, paste this into Claude Code. It tests the guard on a fake key in a temporary folder, so your real files are never touched:

> Prove env-guard works without touching my real files: make a temporary folder with a fake .env containing a dummy key, start a fresh Claude session there, and show me that reading the .env gets blocked and the dummy key shows up as [REDACTED]. Delete the temporary folder when you're done.

In Codex, ask for the same test after you've approved the hook with `/hooks` and restarted.

## What it doesn't catch

env-guard catches the worst mistakes. Here's exactly what it doesn't cover:

- **Short values.** Values under 8 characters, like `3000` or `true`, aren't hidden, because hiding every "3000" would break normal output. Real secrets are almost always longer than that.
- **Secrets outside `.env` files.** It only knows about the `.env` files in your project, like `.env` and `.env.local`. A key stored in `config.json`, or set in your terminal's settings, isn't covered.
- **Disguised values.** It recognizes a key exactly as written, and in two common encoded forms (base64 and URL encoding). If a program changes the key some other way before printing it, like splitting or reversing it, the guard won't recognize it.
- **Codex's saved chat history.** In Codex, the AI only sees the hidden version, but Codex still saves the original output in its chat history files on your computer (`~/.codex/sessions`). In our tests, Claude Code saved only the hidden version.
- **Your own app's logs.** If your program writes a key into its own log file, that file has the real key. env-guard only controls what the AI sees.

## Test

```
python3 tests/test_env_guard.py
```

## License

MIT
