"""Local interactive enrollment/recovery; never accepts credentials in argv."""
import argparse
import getpass
import os
from pathlib import Path

import pyotp

from .auth import AuthStore, AuthUnavailable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("enroll", "recover"))
    args = parser.parse_args()
    if not os.isatty(0) or not os.isatty(1):
        parser.error("Owner enrollment requires an interactive terminal.")
    raw = os.getenv("ONEOS_AUTH_STATE_DIR")
    if not raw:
        parser.error("ONEOS_AUTH_STATE_DIR must select the private authentication directory.")
    directory = Path(raw).absolute()
    if not directory.exists():
        directory.mkdir(mode=0o700)
    password = getpass.getpass("New owner password (at least 14 characters): ")
    if password != getpass.getpass("Confirm new password: "):
        parser.error("Passwords do not match.")
    secret = pyotp.random_base32()
    print("Add this secret to your authenticator (do not save it in logs):", secret)
    print(pyotp.TOTP(secret).provisioning_uri(name="owner", issuer_name="OneOS"))
    code = getpass.getpass("Current authenticator code: ")
    try:
        AuthStore(directory).enroll(password, secret, code, recover=args.action == "recover")
    except (AuthUnavailable, ValueError, OSError):
        parser.error("Enrollment refused. Check the code, password length, and private state permissions.")
    print("Owner credentials saved. Previous sessions are revoked.")


if __name__ == "__main__":
    main()
