#!/usr/bin/env python3
"""env-guard: keep .env secret values out of an AI coding agent's context.

Two checkpoints:
  before a tool runs:  block actions that would open a .env file
  after a tool runs:   replace any .env value in the output with [REDACTED:KEY]

Works in Claude Code and Codex. It tells them apart by the event it receives:
Codex sends a turn_id, Claude Code doesn't.
"""
import base64
import json
import os
import re
import sys
from urllib.parse import quote

MIN_SECRET_LENGTH = 8  # shorter values ("3000", "true") are too common to scrub
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}
TEMPLATE_SUFFIXES = {"example", "sample", "template", "dist", "defaults"}

# Commands that may mention a .env file because they never print its values.
SAFE_COMMANDS = [
    r"ls\b",
    r"test\s+-[efs]\b",
    r"\[\s+-[efs]\b",
    r"grep\s+(-\w*[qcl]\w*\s+)+",                      # grep -q / -c / -l: yes/no, counts, file names
    r"grep\s+-o\s+['\"]\^\[A-Za-z_\]\[A-Za-z0-9_\]\*=['\"]",  # key names only
    r"grep\s+-o\s+['\"]\^\[A-Z_\]\*=['\"]",
    r"cut\s+-d\s*['\"]?=['\"]?\s+-f\s*1\b",            # key names only
    r"git\s+(check-ignore|status|ls-files)\b",
    r"(touch|chmod|stat)\b",
    r"cp\s+\S*\.env\.(example|sample|template)\s",     # create .env from a template
]
SAFE_COMMAND = re.compile(r"^\s*(" + "|".join(SAFE_COMMANDS) + ")")
ENV_FILE_IN_COMMAND = re.compile(r"(?:^|[\s'\"=/<>|;&(`:])((?:[\w-]+)?\.env(?:\.[\w.-]+)?)(?=$|[\s'\";|&)<>`])")


# ---------- which files hold secrets ----------

def is_secret_file(path):
    """True for .env, .env.local, prod.env ... but not .env.example and friends."""
    name = os.path.basename(path.strip("'\""))
    if not re.fullmatch(r"([\w-]+)?\.env(\.[\w.-]+)?", name):
        return False
    return name.rsplit(".", 1)[-1].lower() not in TEMPLATE_SUFFIXES


def project_root(cwd):
    """The enclosing git repo, or cwd if there isn't one."""
    path = os.path.abspath(cwd)
    while path != os.path.dirname(path):
        if os.path.exists(os.path.join(path, ".git")):
            return path
        path = os.path.dirname(path)
    return os.path.abspath(cwd)


def find_secret_files(root, max_depth=4):
    for folder, dirs, files in os.walk(root):
        depth = folder[len(root):].count(os.sep)
        dirs[:] = [] if depth >= max_depth else [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if is_secret_file(name):
                yield os.path.join(folder, name)


def load_secrets(cwd):
    """Map every secret value (plus its common encodings) to its key name."""
    secrets = {}
    for path in find_secret_files(project_root(cwd)):
        with open(path, errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.removeprefix("export ").split("=", 1)
                value = value.strip().strip("'\"")
                if len(value) < MIN_SECRET_LENGTH:
                    continue
                for form in (value, base64.b64encode(value.encode()).decode(), quote(value, safe="")):
                    secrets[form] = key.strip()
    return secrets


# ---------- checkpoint 1: before a tool runs ----------

def block(reason):
    print(
        f"Blocked by env-guard: {reason}\n"
        "Don't read .env values. To check a key exists: "
        "grep -q '^KEY_NAME=.\\+' .env && echo set || echo missing. "
        "To list key names: grep -o '^[A-Za-z_][A-Za-z0-9_]*=' .env. "
        "If you need a value, ask the user.",
        file=sys.stderr,
    )
    sys.exit(2)


def shell_command(tool_input):
    command = tool_input.get("command", "")
    if isinstance(command, list):  # older Codex sends ["bash", "-lc", "<script>"]
        is_wrapped = len(command) >= 3 and os.path.basename(command[0]) in ("bash", "sh", "zsh") and command[1] in ("-c", "-lc")
        command = command[2] if is_wrapped else " ".join(map(str, command))
    return str(command)


def check_before(tool, tool_input):
    if tool in ("Read", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if is_secret_file(path):
            block(f"{tool} on {os.path.basename(path)}")

    if tool == "Grep":
        for target in (tool_input.get("path") or "", tool_input.get("glob") or ""):
            if is_secret_file(target) or target.endswith((".env", ".env*")):
                block(f"searching inside {target}")

    if tool == "Bash":
        for part in re.split(r"\|\||&&|[;|\n]", shell_command(tool_input)):
            env_files = [f for f in ENV_FILE_IN_COMMAND.findall(part) if is_secret_file(f)]
            if env_files and not SAFE_COMMAND.match(part):
                block(f"this command could print the values in {env_files[0]}")


# ---------- checkpoint 2: after a tool runs ----------

def scrub(value, secrets):
    """Replace secrets in every string, keeping the output's shape intact."""
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, f"[REDACTED:{secrets[secret]}]")
        return value
    if isinstance(value, list):
        return [scrub(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: scrub(item, secrets) for key, item in value.items()}
    return value


def withhold(value):
    """Blank every string, keeping the output's shape intact."""
    if isinstance(value, str):
        return "[env-guard failed: output withheld]"
    if isinstance(value, list):
        return [withhold(item) for item in value]
    if isinstance(value, dict):
        return {key: withhold(item) for key, item in value.items()}
    return value


def check_after(agent, event):
    output = event.get("tool_response")
    try:
        cleaned = scrub(output, load_secrets(event.get("cwd") or os.getcwd()))
    except Exception:
        cleaned = withhold(output)  # fail closed: hide output rather than leak it
    if cleaned == output:
        return

    if agent == "claude":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": cleaned}}))
    else:  # Codex can't edit output, but "block" swaps the result for our text
        text = cleaned if isinstance(cleaned, str) else json.dumps(cleaned, indent=2)
        header = "[env-guard: the command ran normally; secret values below were redacted]\n"
        print(json.dumps({"decision": "block", "reason": header + text}))


# ---------- entry point ----------

def main():
    event = json.load(sys.stdin)
    agent = "codex" if "turn_id" in event else "claude"
    tool = event.get("tool_name", "")
    if event.get("hook_event_name") == "PostToolUse":
        check_after(agent, event)
    else:
        check_before(tool, event.get("tool_input") or {})


if __name__ == "__main__":
    main()
