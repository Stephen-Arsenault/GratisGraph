#!/usr/bin/env python3
"""
usb_helper.py — one-time privileged-helper installer for GratisGraph (macOS).

The only operation the app needs root for is unloading/reloading the Apple USB
printer driver so libusb can claim the M20 interface. Rather than run the whole
app as root, this installs a *scoped* sudoers rule that lets the current user
run ONLY those two exact commands without a password:

    /sbin/kextunload -b com.apple.driver.AppleUSBPrinter
    /sbin/kextload   -b com.apple.driver.AppleUSBPrinter

After that the menu-bar app (and CLI) run unprivileged; KextManager elevates
just those calls via `sudo -n`. No Apple Developer ID / code-signing required
(unlike SMAppService), and the privilege granted is exactly two fixed commands
— no wildcards, restricted to one user.

    python3 usb_helper.py status      # installed / not installed
    python3 usb_helper.py install     # one admin prompt, then password-free
    python3 usb_helper.py uninstall   # one admin prompt; removes the rule

Install/uninstall pop the native macOS admin dialog (osascript). The candidate
sudoers file is syntax-checked with `visudo -cf` BEFORE it is put in place, so
a malformed rule can never lock you out of sudo.
"""
import getpass
import os
import re
import shlex
import subprocess
import sys
import tempfile

KEXT_ID = 'com.apple.driver.AppleUSBPrinter'
KEXTUNLOAD = '/sbin/kextunload'
KEXTLOAD = '/sbin/kextload'
SUDOERS_PATH = '/etc/sudoers.d/gratisgraph'
VISUDO = '/usr/sbin/visudo'


def _username() -> str:
    u = getpass.getuser()
    # Guard against sudoers injection via an unexpected username.
    if not re.match(r'^[a-z_][a-z0-9_-]*$', u):
        raise ValueError(f'Refusing to build a sudoers rule for unusual username {u!r}')
    return u


def sudoers_content(user: str = None) -> str:
    """The scoped rule. Pure/testable."""
    user = user or _username()
    return (
        '# GratisGraph USB helper — installed by tray/usb_helper.py\n'
        f'# Lets {user} load/unload ONLY the Apple USB printer driver without a\n'
        '# password, so the app can free the M20 interface without running as root.\n'
        '# Remove with: python3 tray/usb_helper.py uninstall\n'
        f'{user} ALL=(root) NOPASSWD: {KEXTUNLOAD} -b {KEXT_ID}, {KEXTLOAD} -b {KEXT_ID}\n'
    )


def is_installed() -> bool:
    """True only if our *persistent* scoped NOPASSWD rule is in effect.

    We can't read /etc/sudoers.d (root-only), so we parse `sudo -n -l` and look
    for the NOPASSWD entry naming kextunload. This deliberately does NOT report
    True just because you happen to hold a cached sudo credential (general admin
    rights) — only the installed rule guarantees the app runs password-free.
    """
    if sys.platform != 'darwin':
        return False
    try:
        r = subprocess.run(['sudo', '-n', '-l'], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return False
        for line in r.stdout.splitlines():
            if 'NOPASSWD' in line and KEXTUNLOAD in line:
                return True
        return False
    except Exception:
        return False


def _applescript_quote(s: str) -> str:
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _run_as_admin(shell_cmd: str) -> subprocess.CompletedProcess:
    """Run a /bin/sh command with one native admin auth prompt."""
    osa = f'do shell script {_applescript_quote(shell_cmd)} with administrator privileges'
    return subprocess.run(['osascript', '-e', osa], capture_output=True, text=True)


def install() -> bool:
    if sys.platform != 'darwin':
        raise RuntimeError('The USB helper is macOS-only.')
    content = sudoers_content()

    # Pre-validate syntax WITHOUT root, so a bad file is never installed.
    fd, tmp = tempfile.mkstemp(prefix='gg-sudoers-')
    try:
        os.write(fd, content.encode())
        os.close(fd)
        chk = subprocess.run([VISUDO, '-cf', tmp], capture_output=True, text=True)
        if chk.returncode != 0:
            raise RuntimeError(f'sudoers validation failed: {(chk.stderr or chk.stdout).strip()}')

        # Elevate once: install root:wheel 0440, then re-validate the live config.
        shell_cmd = (
            f'/usr/bin/install -o root -g wheel -m 0440 {shlex.quote(tmp)} '
            f'{shlex.quote(SUDOERS_PATH)} && {VISUDO} -cf {shlex.quote(SUDOERS_PATH)}'
        )
        r = _run_as_admin(shell_cmd)
        if r.returncode != 0:
            msg = r.stderr.strip() or 'authorization cancelled'
            if 'User canceled' in msg or '-128' in msg:
                msg = 'authorization cancelled'
            raise RuntimeError(msg)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return is_installed()


def uninstall() -> bool:
    if sys.platform != 'darwin':
        return True
    r = _run_as_admin(f'/bin/rm -f {shlex.quote(SUDOERS_PATH)}')
    if r.returncode != 0:
        msg = r.stderr.strip() or 'authorization cancelled'
        raise RuntimeError(msg)
    return not os.path.exists(SUDOERS_PATH)


def main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else 'status'
    try:
        if cmd == 'status':
            print('installed' if is_installed() else 'not installed')
            return 0
        if cmd == 'install':
            ok = install()
            print('installed' if ok else 'install reported not-active')
            return 0 if ok else 1
        if cmd == 'uninstall':
            print('removed' if uninstall() else 'still present')
            return 0
    except Exception as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    print('usage: usb_helper.py [status|install|uninstall]', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
