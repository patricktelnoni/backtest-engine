"""Operator CLI for user accounts (there is no public sign-up).

Run as the service user so the database stays owned by it, e.g. on the server:
  sudo -u backtest BT_AUTH_DB=/var/lib/backtest/auth.db /opt/backtest-platform/.venv/bin/python manage.py add admin

Commands:
  add <username> [--generate]     create a user (prompts for the password, or generates a strong one)
  passwd <username> [--generate]  set a new password (signs the user out everywhere)
  delete <username>               delete a user and their sessions
  revoke <username>               sign the user out everywhere
  list                            list users and their active session count
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import sqlite3
import sys

import auth


def _ask_password(username: str, generate: bool) -> str:
    if generate:
        pw = auth.generate_password()
        print(f"Generated password for {username!r} (shown once, store it in a password manager):\n\n  {pw}\n")
        return pw
    print(f"Password policy: >= {auth.PASSWORD_MIN_LEN} chars, 3 of lowercase/uppercase/digits/symbols, "
          "not a common word, not containing the username.")
    while True:
        pw = getpass.getpass("New password: ")
        if probs := auth.password_problems(pw, username):
            print("Rejected: password " + "; ".join(probs), file=sys.stderr)
            continue
        if getpass.getpass("Repeat password: ") != pw:
            print("Passwords do not match.", file=sys.stderr)
            continue
        return pw


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Manage Backtest Platform users.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("add", "passwd"):
        p = sub.add_parser(name)
        p.add_argument("username")
        p.add_argument("--generate", action="store_true", help="generate a random 24-character password")
    for name in ("delete", "revoke"):
        sub.add_parser(name).add_argument("username")
    sub.add_parser("list")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "add":
            auth.create_user(a.username, _ask_password(a.username, a.generate))
            print(f"User {a.username!r} created.")
        elif a.cmd == "passwd":
            auth.set_password(a.username, _ask_password(a.username, a.generate))
            print(f"Password for {a.username!r} changed; all of their sessions were signed out.")
        elif a.cmd == "delete":
            auth.delete_user(a.username)
            print(f"User {a.username!r} deleted.")
        elif a.cmd == "revoke":
            print(f"Signed out {auth.revoke_sessions(a.username)} session(s) of {a.username!r}.")
        else:
            users = auth.list_users()
            if not users:
                print("No users yet. Create one with: python manage.py add <username>")
            for u in users:
                created = dt.datetime.fromtimestamp(u["created_at"]).strftime("%Y-%m-%d %H:%M")
                print(f"{u['username']:<32} created {created}   active sessions: {u['sessions']}")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except sqlite3.OperationalError as e:
        print(f"Error: cannot use database {auth.db_path()!r}: {e} (does the directory exist and is it "
              "writable by this user? set BT_AUTH_DB)", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        return 130
    print(f"(database: {auth.db_path()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
