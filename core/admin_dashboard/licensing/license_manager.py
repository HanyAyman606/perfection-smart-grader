"""
Nexus Edge Licensing
=====================
Verifies a signed license file before the admin dashboard is allowed to
run, so the app can be sold/distributed as a compiled binary without
handing over source, while still controlling who can actually use it.

How it works
------------
Ed25519 public-key signatures. The PUBLIC key is embedded in this file
and shipped inside the compiled app — that's safe, a public key can
only *verify* signatures, never *create* them. The matching PRIVATE key
is generated once (tools/keygen.py) and never leaves the seller's own
machine; it's used per-sale to sign a small license.lic file
(tools/generate_license.py). Neither the private key nor those two
tools are ever bundled into the built app or shipped to a buyer.

Verification is fully offline — no phone-home, no license server, no
internet requirement. A buyer's exam-room laptop can be air-gapped and
this still works, because the app only needs the public key (already
embedded) plus the license.lic file it was given at time of sale.

What this does NOT protect against
-----------------------------------
A sufficiently motivated attacker with a debugger can patch a compiled
binary to skip this check entirely — no offline license scheme
(this one included) is unbreakable against someone willing to
disassemble the binary. Combined with Nuitka compilation (see
BUILD.md), this raises the bar from "open the .py file" to "reverse
engineer a stripped native binary," which is the realistic goal here,
not perfect DRM.
"""
from __future__ import annotations

import base64
import hashlib
import json
import platform
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Generated once by tools/keygen.py, then pasted in here before building
# the app for release. The placeholder below will reject every license
# file (by design — it should be impossible to ship an app that
# "verifies" against a key nobody actually signed with).
PUBLIC_KEY_HEX = "16e9e1cd8efd23c80c0c09aa3ba23dbbd5938cf9adc289d6501853f7e0817afb"

# Where the app looks for a license, in order. Buyers are told to drop
# their license.lic next to the executable, or in a per-user folder if
# they'd rather not touch the install directory.
DEFAULT_LICENSE_PATHS = [
    Path.cwd() / "license.lic",
    Path.home() / ".smart_grader" / "license.lic",
]


class LicenseError(Exception):
    """Raised with a human-readable reason a license failed to validate.
    The message is written to be shown directly to the buyer in a
    dialog — see dashboard's license gate in main.py."""


@dataclass
class LicenseInfo:
    licensee: str
    issued: str
    expires: Optional[str]  # ISO date string, or None for perpetual
    bind_machine: bool

    @property
    def is_expired(self) -> bool:
        if self.expires is None:
            return False
        return date.today().isoformat() > self.expires


def machine_fingerprint() -> str:
    """A best-effort stable ID for the current machine. This is meant to
    stop casual copying (one license, one exam-room laptop) — it is not
    tamper-proof against a determined attacker, and it will change if
    the buyer's network adapter or hostname changes, so a machine-bound
    license may need reissuing after a hardware swap. Unlocked
    (non-machine-bound) licenses skip this check entirely — see
    tools/generate_license.py's --machine-id flag."""
    raw = f"{platform.system()}|{platform.node()}|{uuid.getnode()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _find_license_file() -> Optional[Path]:
    for path in DEFAULT_LICENSE_PATHS:
        if path.is_file():
            return path
    return None


def verify_license(license_path: Optional[Path] = None) -> LicenseInfo:
    """Raises LicenseError (with a buyer-facing message) if the license
    is missing, corrupted, forged, expired, or bound to a different
    machine. Returns LicenseInfo on success — call this once at startup,
    before the main window is shown."""
    path = license_path or _find_license_file()
    if path is None or not path.is_file():
        searched = "\n".join(f"  - {p}" for p in DEFAULT_LICENSE_PATHS)
        raise LicenseError(
            "No license file found. Place the license.lic you were sent "
            f"in one of these locations:\n{searched}"
        )

    try:
        raw = path.read_text().strip()
        payload_b64, sig_b64 = raw.split(".", 1)
        payload_bytes = base64.urlsafe_b64decode(payload_b64.encode())
        signature = base64.urlsafe_b64decode(sig_b64.encode())
    except Exception as exc:
        raise LicenseError(
            f"License file at {path} is corrupted or unreadable. "
            "Contact support for a fresh copy."
        ) from exc

    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(PUBLIC_KEY_HEX))
        public_key.verify(signature, payload_bytes)
    except (InvalidSignature, ValueError) as exc:
        raise LicenseError(
            "This license file is invalid — it was not issued for this "
            "application, or has been modified. Contact support."
        ) from exc

    try:
        data = json.loads(payload_bytes.decode())
        info = LicenseInfo(
            licensee=data["licensee"],
            issued=data["issued"],
            expires=data.get("expires"),
            bind_machine=data.get("bind_machine", False),
        )
    except (KeyError, json.JSONDecodeError) as exc:
        raise LicenseError(f"License file at {path} is malformed.") from exc

    if info.is_expired:
        raise LicenseError(
            f"This license expired on {info.expires}. Contact us to renew it."
        )

    if info.bind_machine:
        expected = data.get("machine_id")
        if expected != machine_fingerprint():
            raise LicenseError(
                "This license is locked to a different machine than the one "
                "it's running on. Contact us if you need it transferred to "
                "new hardware."
            )

    return info
