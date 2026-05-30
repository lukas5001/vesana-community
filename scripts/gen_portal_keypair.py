#!/usr/bin/env python3
"""Generate the Ed25519 keypair that secures Community-Hub SSO.

The licence portal SIGNS short-lived login JWTs with the PRIVATE key; the
community app VERIFIES them with the PUBLIC key (algorithm "EdDSA"). The two
sides never share a secret — community.vesana.org only ever holds the public
half, so a leak there cannot forge logins.

Usage:
    python scripts/gen_portal_keypair.py [--out-dir ./secrets]

Writes:
    portal_ed25519_private.pem   -> licence portal ONLY  (keep secret, 0600)
    portal_ed25519_public.pem    -> community app         (PORTAL_PUBLIC_KEY[_PATH])

Wire-up:
  * Licence portal: load the PRIVATE PEM and sign with PyJWT
    jwt.encode(payload, private_pem, algorithm="EdDSA")
    (see docs/portal-issue-login-token.md for the full endpoint).
  * Community app: set PORTAL_PUBLIC_KEY to the public PEM (inline) or
    PORTAL_PUBLIC_KEY_PATH to its file path (see app/config.py).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def generate(out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path = out_dir / "portal_ed25519_private.pem"
    pub_path = out_dir / "portal_ed25519_public.pem"

    priv_path.write_bytes(private_pem)
    priv_path.chmod(0o600)
    pub_path.write_bytes(public_pem)

    return priv_path, pub_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("./secrets"),
        help="directory to write the PEM files into (default: ./secrets)",
    )
    args = parser.parse_args()

    priv_path, pub_path = generate(args.out_dir)

    print("Ed25519 keypair generated:")
    print(f"  PRIVATE (licence portal only, keep secret): {priv_path}")
    print(f"  PUBLIC  (community app PORTAL_PUBLIC_KEY)  : {pub_path}")
    print()
    print("Next steps:")
    print("  1. Install the PRIVATE key on the licence portal and add the")
    print("     issue-login-token endpoint (docs/portal-issue-login-token.md).")
    print("  2. Set PORTAL_PUBLIC_KEY_PATH (or paste PORTAL_PUBLIC_KEY) in the")
    print("     community app .env to the PUBLIC PEM above.")
    print("  3. NEVER commit either PEM. Add secrets/ to .gitignore (already is).")


if __name__ == "__main__":
    main()
