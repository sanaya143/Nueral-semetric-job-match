"""Create a workspace API key for NSM_AUTH_MODE=api_key.

    backend/.venv/Scripts/python.exe scripts/make_api_key.py acme-recruiting

Prints the key once (give it to the people who use that workspace) and the line to add to
NSM_API_KEYS. Only the SHA-256 hash is stored on the server, so a leaked config does not leak keys.
To revoke a key, remove its hash from NSM_API_KEYS and restart.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sys


def main() -> None:
    if len(sys.argv) != 2 or not re.match(r"^[A-Za-z0-9_-]{1,64}$", sys.argv[1]):
        sys.exit("usage: make_api_key.py <workspace>   (letters, digits, - and _ only)")
    workspace = sys.argv[1]
    key = "nsm_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    print(f"Workspace : {workspace}")
    print(f"API key   : {key}")
    print("            (shown once; store it in a password manager or Key Vault)")
    print()
    print("Add this entry to NSM_API_KEYS (a JSON object; merge it with any existing entries):")
    print(f"NSM_API_KEYS={json.dumps({digest: workspace})}")


if __name__ == "__main__":
    main()
