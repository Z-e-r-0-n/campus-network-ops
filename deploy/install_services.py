#!/usr/bin/env python3
"""Supervise the isolated build runtime without enabling campus mutations."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    units = Path.home() / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    for name, command in {
        "api": "-m uvicorn backend.api.app:create_app --factory --host 127.0.0.1 --port 8875 --no-access-log",
        "observe": "-m backend.workflows.runtime",
        "inventory": "-m backend.workflows.runtime inventory",
    }.items():
        text = f'''[Unit]
Description=Campus replacement {name} (isolated build)
After=network-online.target

[Service]
Type=simple
WorkingDirectory={ROOT}
ExecStart="{ROOT}/.venv/bin/python" {command}
Environment=CAMPUS_NEXT_MUTATIONS=disabled
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
TimeoutStopSec=30

[Install]
WantedBy=default.target
'''
        (units / f"campus-next-{name}.service").write_text(text)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        [
            "systemctl",
            "--user",
            "start",
            "campus-next-api.service",
            "campus-next-observe.service",
            "campus-next-inventory.service",
        ],
        check=True,
    )
    print(
        "Isolated API, observation and inventory workers are supervised; campus execution remains disabled."
    )


if __name__ == "__main__":
    main()
