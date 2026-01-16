# watchd

**Background daemon that monitors terminal commands and sends push notifications to your phone.**

Get notified when your long-running commands finish, fail, or produce errors — without checking the terminal.

## Features

- Wraps any command in a PTY (works with SSH, interactive tools, AI coders)
- Detects errors via regex patterns (error, failed, traceback, panic, etc.)
- Notifies on non-zero exit codes
- Optional inactivity timeout alerts
- Push notifications via [ntfy.sh](https://ntfy.sh) (free, no app required)
- Runs as a systemd service (survives reboot)
- Zero dependencies (Python 3 stdlib only)

## Architecture

```
┌─────────┐    UNIX     ┌──────────┐    PTY    ┌─────────┐
│  watch  │───socket───▶│  watchd  │──────────▶│ command │
│  (CLI)  │◀──stream────│ (daemon) │◀──────────│ (child) │
└─────────┘             └────┬─────┘           └─────────┘
                             │
                      ┌──────▼──────┐
                      │  detector   │──────▶ ntfy.sh
                      └─────────────┘
```

| Component | Description |
|-----------|-------------|
| `watchd` | Background daemon (systemd service) |
| `watch` | CLI tool to run commands under monitoring |
| `ntfy.sh` | Push notification delivery |

## Installation

```bash
git clone https://github.com/Schnovak/watchd.git
cd watchd
sudo ./install.sh
```

## Quick Start (5 minutes)

### 1. Configure your ntfy.sh topic

Pick a unique, hard-to-guess topic name (it acts as your password):

```bash
sudo systemctl edit watchd
```

Add these lines:
```ini
[Service]
Environment=WATCHD_NTFY_URL=https://ntfy.sh/my-secret-topic-abc123
```

### 2. Enable and start the daemon

```bash
sudo systemctl enable watchd
sudo systemctl start watchd
```

### 3. Subscribe to notifications

**Option A:** Open `https://ntfy.sh/my-secret-topic-abc123` in your phone's browser

**Option B:** Install the [ntfy app](https://ntfy.sh) and subscribe to your topic

### 4. Run a command

```bash
watch echo "test notification"
```

You should receive a notification on your phone.

## Usage

```bash
# Basic commands
watch python train.py
watch make build
watch npm run test

# SSH sessions (fully interactive)
watch ssh user@server

# With inactivity timeout (alert if no output for 5 minutes)
watch --timeout 300 python long_training.py

# Interactive tools work too
watch htop
watch vim file.txt
```

## Detection Rules

| Event | Trigger | Notification Priority |
|-------|---------|----------------------|
| Pattern match | `error`, `failed`, `traceback`, `panic`, `fatal`, `exception`, `segmentation fault`, `killed`, `oom` | High |
| Non-zero exit | Exit code ≠ 0 | High |
| Inactivity | No output for N seconds (`--timeout`) | Default |

Pattern matching is case-insensitive with word boundaries (won't match "terror" for "error").

## Configuration

Set via `sudo systemctl edit watchd`:

| Variable | Default | Description |
|----------|---------|-------------|
| `WATCHD_NTFY_URL` | `https://ntfy.sh/watchd-alerts` | Your ntfy.sh topic URL |
| `WATCHD_SOCKET` | `/tmp/watchd.sock` | UNIX socket path |
| `WATCHD_LOG` | `/var/log/watchd.log` | Log file location |

## Service Management

```bash
# Check status
sudo systemctl status watchd

# View logs
sudo journalctl -u watchd -f

# Restart after config change
sudo systemctl restart watchd

# Stop
sudo systemctl stop watchd
```

## Platform Support

| Platform | Status |
|----------|--------|
| Linux (systemd) | Full support |
| WSL2 (systemd enabled) | Full support |
| WSL2 (no systemd) | Manual daemon start (see below) |
| macOS | Manual daemon start |

### WSL/macOS without systemd

```bash
# Start daemon manually
nohup watchd > /tmp/watchd.log 2>&1 &

# Auto-start: add to ~/.bashrc or ~/.zshrc
pgrep -x watchd > /dev/null || nohup watchd > /tmp/watchd.log 2>&1 &
```

## Troubleshooting

**"watchd is not running"**
```bash
sudo systemctl start watchd
sudo systemctl status watchd
```

**No notifications received**
```bash
# Test ntfy directly
curl -d "test message" https://ntfy.sh/your-topic

# Check daemon logs
sudo journalctl -u watchd -n 50
```

**Permission denied on socket**
```bash
sudo systemctl restart watchd
```

## Uninstall

```bash
sudo systemctl stop watchd
sudo systemctl disable watchd
sudo rm /etc/systemd/system/watchd.service
sudo rm /usr/local/bin/watchd /usr/local/bin/watch
sudo systemctl daemon-reload
```

## How It Works

1. `watch` connects to `watchd` via UNIX socket and sends the command
2. `watchd` spawns the command in a pseudo-terminal (PTY)
3. Output streams back to your terminal in real-time
4. `watchd` scans output for error patterns
5. On match/exit/timeout, `watchd` POSTs to ntfy.sh
6. ntfy.sh pushes to your subscribed devices

## License

MIT
