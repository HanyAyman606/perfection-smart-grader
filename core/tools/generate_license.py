"""
SELLER-ONLY TOOL — do not ship this with the app. Requires
private_key.pem (from tools/keygen.py) to be present alongside this
script or passed via --key.

Issue one signed license.lic per sale, then send that single file to
the buyer:

    # Perpetual, works on any of the buyer's machines:
    python tools/generate_license.py --licensee "Al Nour School"

    # Expires on a date (e.g. a 1-year support/subscription term):
    python tools/generate_license.py --licensee "Al Nour School" \\
        --expires 2027-08-14

    # Locked to one machine (ask the buyer to run the app once, it will
    # print its machine_fingerprint() in the license-error dialog, or
    # run: python -c "from admin_dashboard.licensing.license_manager
    # import machine_fingerprint; print(machine_fingerprint())" on
    # their machine and send you the result):
    python tools/generate_license.py --licensee "Al Nour School" \\
        --machine-id 3f9a1c...
"""
import argparse
import base64
import json
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--licensee", required=True, help="Buyer name, shown nowhere in the UI currently but kept for your own records")
    parser.add_argument("--expires", default=None, help="YYYY-MM-DD; omit for a perpetual license")
    parser.add_argument("--machine-id", default=None, help="Lock to one machine's fingerprint; omit for an unlocked license")
    parser.add_argument("--out", default="license.lic", help="Output path")
    parser.add_argument("--key", default=str(Path(__file__).parent / "private_key.pem"))
    args = parser.parse_args()

    key_path = Path(args.key)
    if not key_path.exists():
        raise SystemExit(f"Private key not found at {key_path}. Run tools/keygen.py first.")

    private_key = Ed25519PrivateKey.from_private_bytes(key_path.read_bytes())

    if args.expires:
        # Fail fast on a typo'd date rather than shipping a license that
        # silently never expires or never validates.
        date.fromisoformat(args.expires)

    payload = {
        "licensee": args.licensee,
        "issued": date.today().isoformat(),
        "expires": args.expires,
        "bind_machine": args.machine_id is not None,
    }
    if args.machine_id:
        payload["machine_id"] = args.machine_id

    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = private_key.sign(payload_bytes)

    license_text = (
        base64.urlsafe_b64encode(payload_bytes).decode()
        + "."
        + base64.urlsafe_b64encode(signature).decode()
    )
    Path(args.out).write_text(license_text)

    summary = f"Wrote {args.out} for '{args.licensee}'"
    summary += f", expires {args.expires}" if args.expires else ", perpetual"
    summary += f", locked to machine {args.machine_id}" if args.machine_id else ", unlocked (any machine)"
    print(summary)
    print("Send only this .lic file to the buyer — nothing else in tools/ or private_key.pem.")


if __name__ == "__main__":
    main()
