"""Tests for hooks/env-guard.py. Run: python3 tests/test_env_guard.py

Every secret below is a dummy value in a throwaway folder.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

GUARD = os.path.join(os.path.dirname(__file__), "..", "hooks", "env-guard.py")


def run_guard(event):
    result = subprocess.run([sys.executable, "-I", GUARD], input=json.dumps(event), capture_output=True, text=True)
    return result.returncode, result.stdout


def before(tool, tool_input):
    code, _ = run_guard({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input})
    return "blocked" if code == 2 else "allowed"


def bash_output(text):
    return {"stdout": text, "stderr": "", "interrupted": False, "isImage": False}


class Project:
    """A fake git project with dummy secrets."""

    def __init__(self):
        self.root = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.root, ".git"))
        self.write(".env", 'OPENAI_API_KEY=sk-test-abcdefgh12345\nPORT=3000\nexport SOME_COMPANY_NAME_JWT="eyJhbGciOiJIUzI1NiJ9.fake"\n# a comment\n')
        self.write("apps/web/.env.local", "DB_PASSWORD=hunter2hunter2\n")
        self.write(".env.example", "EXAMPLE_KEY=placeholder-value-123\n")
        self.write("node_modules/pkg/.env", "IGNORED=in-node-modules-999\n")

    def write(self, path, text):
        full = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text)

    def after(self, output, codex=False, cwd=None):
        event = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "x"},
                 "tool_response": output, "cwd": cwd or self.root}
        if codex:
            event["turn_id"] = "t1"
        return run_guard(event)

    def remove(self):
        for folder, _, files in os.walk(self.root):
            for name in files:
                os.chmod(os.path.join(folder, name), 0o644)
        shutil.rmtree(self.root)


class Before(unittest.TestCase):
    def test_blocks_reading_secret_files(self):
        for tool, tool_input in [
            ("Read", {"file_path": "/x/.env"}),
            ("Read", {"file_path": "/x/.env.local"}),
            ("Grep", {"pattern": "KEY", "path": "/x/.env"}),
            ("Bash", {"command": "cat .env"}),
            ("Bash", {"command": "grep -v 'TOKEN|SECRET|KEY' .env"}),
            ("Bash", {"command": "source .env && echo $KEY"}),
            ("Bash", {"command": "python3 -c \"print(open('.env').read())\""}),
            ("Bash", {"command": "cat .env | cut -d= -f1"}),
            ("Bash", {"command": ["bash", "-lc", "cat .env"]}),
        ]:
            self.assertEqual(before(tool, tool_input), "blocked", tool_input)

    def test_allows_safe_checks_and_normal_work(self):
        for tool, tool_input in [
            ("Read", {"file_path": "/x/.env.example"}),
            ("Grep", {"pattern": "KEY", "path": "/x/src"}),
            ("Bash", {"command": "grep -q '^OPENAI_API_KEY=.\\+' .env && echo set || echo missing"}),
            ("Bash", {"command": "grep -o '^[A-Za-z_][A-Za-z0-9_]*=' .env"}),
            ("Bash", {"command": "cut -d= -f1 .env"}),
            ("Bash", {"command": "ls -la .env"}),
            ("Bash", {"command": "cp .env.example .env"}),
            ("Bash", {"command": "npm run dev"}),
            ("Bash", {"command": ["bash", "-lc", "ls .env"]}),
        ]:
            self.assertEqual(before(tool, tool_input), "allowed", tool_input)

    def test_allows_text_that_only_mentions_env_files(self):
        for command in [
            "python3 -I - README.md <<'EOF'\nNever cat .env; use grep -q instead.\nEOF",
            "cat > notes.md <<EOF\nCopy .env.example to .env first.\nEOF",
            "git commit -m \"$(cat <<'EOF'\nStop reading .env in tests\nEOF\n)\"",
            "echo \"copy .env.example to .env; then run it\"",
            "printf '%s\\n' 'source .env'",
            "git commit -m 'Ignore .env | keep .env.example'",
            "git -C ~/proj commit -m \"Handle .env files\"",
        ]:
            self.assertEqual(before("Bash", {"command": command}), "allowed", command)

    def test_still_blocks_reads_hidden_in_text_shapes(self):
        for command in [
            "head .env",
            "head -n 3 .env",
            "bash <<EOF\ncat .env\nEOF",
            "cat <<'EOF' | bash\ncat .env\nEOF",
            "cat <<EOF\n$(cat .env)\nEOF",
            "echo 'cat .env' | bash",
            "echo .env | xargs cat",
            "echo \"print(open('.env').read())\" | python3",
            "printf '%s' \"require('fs').readFileSync('.env')\" | node",
            "echo 'cat .env' | sudo bash",
            "git commit -F '.env'",
            "git commit --file='.env'",
            "echo \"$(cat .env)\"",
            "printf '%s' \"`cat .env`\"",
            "python3 -c \"print(open('.env').read())\nprint(1)\"",
            "python3 <<< 'open(\".env\")'",
        ]:
            self.assertEqual(before("Bash", {"command": command}), "blocked", command)


class After(unittest.TestCase):
    def setUp(self):
        self.project = Project()

    def tearDown(self):
        self.project.remove()

    def test_claude_code_gets_redacted_output_with_the_same_shape(self):
        _, out = self.project.after(bash_output("key is sk-test-abcdefgh12345 ok"))
        cleaned = json.loads(out)["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(cleaned["stdout"], "key is [REDACTED:OPENAI_API_KEY] ok")
        self.assertEqual(set(cleaned), {"stdout", "stderr", "interrupted", "isImage"})

    def test_redacts_quoted_export_lines_base64_and_nested_files(self):
        encoded = base64.b64encode(b"sk-test-abcdefgh12345").decode()
        for text, label in [
            ("jwt eyJhbGciOiJIUzI1NiJ9.fake", "SOME_COMPANY_NAME_JWT"),
            (encoded, "OPENAI_API_KEY"),
            ("pw=hunter2hunter2", "DB_PASSWORD"),
        ]:
            _, out = self.project.after(bash_output(text), cwd=os.path.join(self.project.root, "apps"))
            self.assertIn(f"[REDACTED:{label}]", out)

    def test_leaves_short_values_templates_and_node_modules_alone(self):
        _, out = self.project.after(bash_output("port 3000, placeholder-value-123, in-node-modules-999"))
        self.assertEqual(out, "")

    def test_codex_gets_redacted_text_as_the_result(self):
        _, out = self.project.after("token sk-test-abcdefgh12345", codex=True)
        result = json.loads(out)
        self.assertEqual(result["decision"], "block")
        self.assertTrue(result["reason"].endswith("\ntoken [REDACTED:OPENAI_API_KEY]"))

    def test_withholds_output_when_a_secret_file_cant_be_read(self):
        os.chmod(os.path.join(self.project.root, ".env"), 0o000)
        _, out = self.project.after(bash_output("anything"))
        self.assertIn("output withheld", out)


class FailClosed(unittest.TestCase):
    def test_missing_python_blocks(self):
        result = subprocess.run(["sh", "-c", "python3-not-installed guard.py || exit 2"], capture_output=True)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
