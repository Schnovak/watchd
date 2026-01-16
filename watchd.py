#!/usr/bin/env python3
"""
watchd - Background daemon for monitoring terminal commands.

Architecture:
┌─────────┐    UNIX     ┌──────────┐    PTY    ┌─────────┐
│  watch  │───socket───▶│  watchd  │──────────▶│ command │
│  (CLI)  │◀──stream────│ (daemon) │◀──────────│ (child) │
└─────────┘             └────┬─────┘           └─────────┘
                             │
                      ┌──────▼──────┐
                      │  detector   │──────▶ ntfy.sh
                      └─────────────┘

Listens on: /tmp/watchd.sock (or $WATCHD_SOCKET)
Protocol: newline-delimited JSON
"""

import errno
import fcntl
import json
import os
import pty
import re
import select
import signal
import socket
import struct
import sys
import termios
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

# Configuration
SOCKET_PATH = os.environ.get('WATCHD_SOCKET', '/tmp/watchd.sock')
NTFY_URL = os.environ.get('WATCHD_NTFY_URL', 'https://ntfy.sh/watchd-alerts')
LOG_FILE = os.environ.get('WATCHD_LOG', '/tmp/watchd.log')

# Default detection patterns
DEFAULT_PATTERNS = [
    r'\berror\b',
    r'\bfailed\b',
    r'\bfailure\b',
    r'\btraceback\b',
    r'\bpanic\b',
    r'\bfatal\b',
    r'\bexception\b',
    r'\bsegmentation fault\b',
    r'\bkilled\b',
    r'\boom\b',
]


def log(msg: str):
    """Simple logging to file and stderr."""
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, file=sys.stderr)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except OSError:
        pass


@dataclass
class Event:
    event_type: str
    message: str
    priority: str
    tags: list
    timestamp: float
    context: str = ''
    command: str = ''


class Notifier:
    """Sends notifications via ntfy.sh."""

    def __init__(self, topic_url: str):
        self.topic_url = topic_url
        self.last_notify: dict[str, float] = {}
        self.rate_limit = 10  # seconds between same-type notifications

    def send(self, event: Event) -> bool:
        now = time.time()
        key = f'{event.event_type}:{event.command}'
        if key in self.last_notify and now - self.last_notify[key] < self.rate_limit:
            return False
        self.last_notify[key] = now

        title = f'[watchd] {event.event_type}'
        body = f'{event.message}\nCommand: {event.command}'
        if event.context:
            body += f'\n\n{event.context[-500:]}'

        priority_map = {'low': '2', 'default': '3', 'high': '4', 'urgent': '5'}
        headers = {
            'Title': title,
            'Priority': priority_map.get(event.priority, '3'),
            'Tags': ','.join(event.tags),
        }

        try:
            req = urllib.request.Request(
                self.topic_url,
                data=body.encode('utf-8'),
                headers=headers,
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                log(f'Notification sent: {event.event_type}')
                return resp.status == 200
        except Exception as e:
            log(f'Notification failed: {e}')
            return False


class PatternDetector:
    """Detects patterns in streaming output."""

    def __init__(self, patterns: list[str]):
        self.patterns = [re.compile(p, re.IGNORECASE) for p in patterns]
        self.lines: list[str] = []
        self.partial = ''
        self.seen: set[int] = set()

    def feed(self, data: str, command: str) -> list[Event]:
        events = []
        self.partial += data

        while '\n' in self.partial:
            line, self.partial = self.partial.split('\n', 1)
            self.lines.append(line)
            idx = len(self.lines) - 1

            for pattern in self.patterns:
                if pattern.search(line) and idx not in self.seen:
                    self.seen.add(idx)
                    ctx_start = max(0, idx - 2)
                    context = '\n'.join(self.lines[ctx_start:idx + 1])
                    events.append(Event(
                        event_type='pattern_match',
                        message=f'Matched: {pattern.pattern}',
                        priority='high',
                        tags=['warning'],
                        timestamp=time.time(),
                        context=context,
                        command=command,
                    ))

            if len(self.lines) > 500:
                self.lines = self.lines[-250:]

        return events


class Session:
    """Manages a single PTY session."""

    def __init__(self, command: list[str], client_sock: socket.socket,
                 notifier: Notifier, inactivity_timeout: Optional[int]):
        self.command = command
        self.command_str = ' '.join(command)
        self.client = client_sock
        self.notifier = notifier
        self.inactivity_timeout = inactivity_timeout
        self.detector = PatternDetector(DEFAULT_PATTERNS)
        self.master_fd: Optional[int] = None
        self.child_pid: Optional[int] = None
        self.running = True

    def start(self):
        """Fork PTY and start event loop in thread."""
        self.child_pid, self.master_fd = pty.fork()

        if self.child_pid == 0:
            # Child: exec command
            try:
                os.execvp(self.command[0], self.command)
            except Exception:
                pass
            os._exit(127)

        # Parent: run event loop
        thread = threading.Thread(target=self._loop, daemon=True)
        thread.start()
        return thread

    def _send_to_client(self, msg_type: str, data: str):
        """Send JSON message to client."""
        try:
            msg = json.dumps({'type': msg_type, 'data': data}) + '\n'
            self.client.sendall(msg.encode('utf-8'))
        except OSError:
            self.running = False

    def _loop(self):
        """Main event loop."""
        last_activity = time.time()
        inactivity_notified = False

        # Set non-blocking on master
        flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        try:
            self.client.setblocking(False)
        except OSError:
            pass

        while self.running:
            timeout = 1.0
            if self.inactivity_timeout:
                remaining = self.inactivity_timeout - (time.time() - last_activity)
                timeout = max(0.1, min(timeout, remaining))

            try:
                readable, _, _ = select.select(
                    [self.master_fd, self.client], [], [], timeout
                )
            except (select.error, ValueError):
                break

            # Check inactivity
            if self.inactivity_timeout and not inactivity_notified:
                if time.time() - last_activity > self.inactivity_timeout:
                    event = Event(
                        event_type='inactivity',
                        message=f'No output for {self.inactivity_timeout}s',
                        priority='default',
                        tags=['hourglass_done'],
                        timestamp=time.time(),
                        command=self.command_str,
                    )
                    self.notifier.send(event)
                    self._send_to_client('event', json.dumps(asdict(event)))
                    inactivity_notified = True

            for fd in readable:
                if fd == self.master_fd:
                    try:
                        data = os.read(self.master_fd, 4096)
                    except OSError:
                        data = b''

                    if not data:
                        self._finish()
                        return

                    last_activity = time.time()
                    inactivity_notified = False

                    # Send to client
                    self._send_to_client('output', data.decode('utf-8', errors='replace'))

                    # Check patterns
                    text = data.decode('utf-8', errors='replace')
                    for event in self.detector.feed(text, self.command_str):
                        self.notifier.send(event)

                elif fd == self.client:
                    # Input from client -> PTY
                    try:
                        data = self.client.recv(4096)
                        if data:
                            # Check for resize message
                            try:
                                msg = json.loads(data.decode('utf-8').strip())
                                if msg.get('type') == 'resize':
                                    self._resize(msg.get('rows', 24), msg.get('cols', 80))
                                    continue
                                elif msg.get('type') == 'input':
                                    os.write(self.master_fd, msg['data'].encode('utf-8'))
                                    continue
                            except (json.JSONDecodeError, KeyError):
                                pass
                            os.write(self.master_fd, data)
                        else:
                            self.running = False
                    except OSError:
                        pass

            # Check child status
            try:
                pid, status = os.waitpid(self.child_pid, os.WNOHANG)
                if pid != 0:
                    self._handle_exit(status)
                    return
            except ChildProcessError:
                self._finish()
                return

    def _resize(self, rows: int, cols: int):
        """Resize PTY."""
        if self.master_fd:
            try:
                size = struct.pack('HHHH', rows, cols, 0, 0)
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, size)
            except OSError:
                pass

    def _handle_exit(self, status: int):
        """Handle child exit."""
        if os.WIFEXITED(status):
            code = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            code = 128 + os.WTERMSIG(status)
        else:
            code = 1

        if code != 0:
            event = Event(
                event_type='exit_code',
                message=f'Exited with code {code}',
                priority='high',
                tags=['x'],
                timestamp=time.time(),
                command=self.command_str,
            )
            self.notifier.send(event)

        self._send_to_client('exit', str(code))
        self._finish()

    def _finish(self):
        """Clean up."""
        self.running = False
        try:
            os.close(self.master_fd)
        except OSError:
            pass
        try:
            self.client.close()
        except OSError:
            pass


class Daemon:
    """Main daemon managing sessions."""

    def __init__(self):
        self.notifier = Notifier(NTFY_URL)
        self.sessions: list[Session] = []
        self.running = True

    def run(self):
        """Run daemon main loop."""
        # Clean up old socket
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass

        # Create socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o600)
        sock.listen(5)
        sock.setblocking(False)

        log(f'Daemon started, listening on {SOCKET_PATH}')

        def handle_signal(sig, frame):
            log('Shutting down...')
            self.running = False

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        while self.running:
            try:
                readable, _, _ = select.select([sock], [], [], 1.0)
            except select.error:
                continue

            if sock in readable:
                try:
                    client, _ = sock.accept()
                    self._handle_client(client)
                except OSError as e:
                    log(f'Accept error: {e}')

            # Clean up finished sessions
            self.sessions = [s for s in self.sessions if s.running]

        sock.close()
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass
        log('Daemon stopped')

    def _handle_client(self, client: socket.socket):
        """Handle new client connection."""
        try:
            client.settimeout(5.0)
            data = client.recv(4096)
            if not data:
                client.close()
                return

            msg = json.loads(data.decode('utf-8'))
            command = msg.get('command', [])
            timeout = msg.get('timeout')

            if not command:
                client.close()
                return

            log(f'Starting session: {" ".join(command)}')
            session = Session(command, client, self.notifier, timeout)
            self.sessions.append(session)
            session.start()

        except Exception as e:
            log(f'Client error: {e}')
            try:
                client.close()
            except OSError:
                pass


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--version':
        print('watchd 1.0.0')
        sys.exit(0)

    daemon = Daemon()
    daemon.run()


if __name__ == '__main__':
    main()
