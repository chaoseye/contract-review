"""Поддельный Codex CLI для тестов: python fake_codex.py exec … / login … / logout.

Поведение задаётся файлом $CODEX_HOME/fake_mode.json (переменные окружения Codex получает отфильтрованными,
а CODEX_HOME проходит всегда). Каждый вызов дописывается в $CODEX_HOME/fake_calls.jsonl: аргументы,
stdin, рабочая папка и её содержимое, имена переменных окружения.
"""

import json
import os
import sys
import time
from pathlib import Path

# Настоящий Codex читает и пишет UTF-8 независимо от кодировки системы.
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
home = Path(os.environ["CODEX_HOME"])
mode_file = home / "fake_mode.json"
config = json.loads(mode_file.read_text(encoding="utf-8")) if mode_file.exists() else {}
mode = config.get("mode", "ok")
args = sys.argv[1:]


def record(stdin: str = "") -> None:
    call = {
        "args": args,
        "stdin": stdin,
        "cwd": os.getcwd(),
        "cwd_files": sorted(os.listdir(os.getcwd())),
        "env": sorted(os.environ),
    }
    with (home / "fake_calls.jsonl").open("a", encoding="utf-8") as calls:
        calls.write(json.dumps(call, ensure_ascii=False) + "\n")


def option(name: str) -> str:
    return args[args.index(name) + 1]


def emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


if args[:1] == ["exec"]:
    stdin = sys.stdin.buffer.read().decode("utf-8")
    record(stdin)
    if mode == "sleep":
        time.sleep(60)
    if mode == "not_logged_in":
        print("Error: Not logged in. Please run codex login.", file=sys.stderr)
        sys.exit(1)
    emit({"type": "thread.started", "thread_id": "t-1"})
    emit({"type": "turn.started"})
    if mode == "usage_limit":
        emit({"type": "error", "message": "You've hit your usage limit. Try again later."})
        emit({"type": "turn.failed", "error": {"message": "usage limit reached"}})
        sys.exit(1)
    answer = config.get("answer", '{"ok": true, "answer": "готов"}')
    if mode != "no_answer_file":
        Path(option("--output-last-message")).write_text(answer, encoding="utf-8")
    emit({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": answer}})
    emit(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 1200, "cached_input_tokens": 1000, "output_tokens": 30},
        }
    )
    sys.exit(0)

if args[:2] == ["login", "status"]:
    record()
    if (home / "auth.json").exists():
        print("Logged in using ChatGPT")
        sys.exit(0)
    print("Not logged in", file=sys.stderr)
    sys.exit(1)

if args[:2] == ["login", "--device-auth"]:
    record()
    print("\x1b[1mWelcome to Codex\x1b[0m", flush=True)
    print("Follow these steps to sign in with ChatGPT using device code authorization:", flush=True)
    print("1. Open this link in your browser: https://auth.openai.com/codex/device", flush=True)
    print("2. Enter this one-time code: \x1b[94mABCD-12345\x1b[0m", flush=True)
    time.sleep(float(config.get("login_delay", 0.2)))
    if mode == "login_fail":
        print("Device code expired", flush=True)
        sys.exit(1)
    (home / "auth.json").write_text("{}", encoding="utf-8")
    print("Successfully logged in", flush=True)
    sys.exit(0)

if args[:1] == ["logout"]:
    record()
    (home / "auth.json").unlink(missing_ok=True)
    print("Successfully logged out")
    sys.exit(0)

print(f"unknown command: {args}", file=sys.stderr)
sys.exit(2)
