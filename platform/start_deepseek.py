"""Prompt for a backend-only key without placing it in shell history or files."""

from __future__ import annotations

import argparse
import getpass
import os
import runpy
import socket
import sys
import warnings
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the research workbench with DeepSeek")
    parser.add_argument("--port", type=int, default=8776)
    parser.add_argument("--model", default="deepseek-chat")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    # Check before asking for a secret so an occupied port is easy to fix.
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", args.port))
    except OSError:
        parser.error(f"port {args.port} is already in use; choose another --port")

    # Never fall back to echoed stdin if the terminal cannot hide the input.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            key = getpass.getpass("DeepSeek API key (hidden, process memory only): ").strip()
        except (getpass.GetPassWarning, EOFError):
            parser.error("run this command in an interactive terminal with hidden input support")
    if not key:
        parser.error("key must not be empty")
    settings = {
        "DEEPSEEK_API_KEY": key,
        "CRK_LLM_PROVIDER": "deepseek",
        "DEEPSEEK_MODEL": args.model,
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
    }
    previous = {name: os.environ.get(name) for name in settings}
    old_argv = sys.argv
    try:
        os.environ.update(settings)
        sys.argv = ["serve.py", "--port", str(args.port)]
        print(f"Open http://127.0.0.1:{args.port}/osint.html")
        print("DeepSeek is configured. Connectivity is checked on the first extraction.")
        print("Newly submitted material will be sent to DeepSeek for extraction. Ctrl+C stops the service.")
        runpy.run_path(str(Path(__file__).with_name("serve.py")), run_name="__main__")
    except KeyboardInterrupt:
        pass
    finally:
        sys.argv = old_argv
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    main()
