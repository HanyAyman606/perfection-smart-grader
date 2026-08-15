"""
SELLER-ONLY TOOL — do not ship this with the app, do not commit
private_key.pem to git.

Run ONCE, ever, to create the Ed25519 signing keypair for Nexus Edge
licensing:

    python tools/keygen.py

This writes private_key.pem (keep it secret, back it up somewhere safe
— losing it means you can never issue another valid license for
copies of the app already built with the matching public key) and
prints the public key hex, which you paste into
admin_dashboard/licensing/license_manager.py's PUBLIC_KEY_HEX before
building the app for release.

If you ever need to revoke trust in a leaked private key, generate a
new pair and rebuild/reship the app with the new public key — old
licenses signed with the old key will stop verifying against the new
build, which is the intended "kill switch" for a compromised key.
"""
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

OUT_PATH = Path(__file__).parent / "private_key.pem"


def main():
    if OUT_PATH.exists():
        raise SystemExit(
            f"{OUT_PATH} already exists — refusing to overwrite an existing "
            "signing key. Delete it manually first if you really mean to "
            "generate a new one (this will invalidate every license issued "
            "with the old key against apps still built with the old public "
            "key)."
        )

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    OUT_PATH.write_bytes(private_bytes)
    OUT_PATH.chmod(0o600)

    print(f"Private key saved to {OUT_PATH} (permissions restricted to you).")
    print("KEEP THIS SECRET. Back it up somewhere safe. Never commit it,")
    print("never ship it inside the built app, never send it to anyone.\n")
    print("Public key — paste this into license_manager.py's PUBLIC_KEY_HEX:")
    print(public_bytes.hex())


if __name__ == "__main__":
    main()
