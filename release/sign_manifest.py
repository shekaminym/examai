"""Sign a Windows release manifest using an offline Ed25519 private key."""
import argparse
import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.serialization import load_pem_private_key

if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("channel", choices=("beta", "stable"))
    cli.add_argument("version")
    cli.add_argument("url")
    cli.add_argument("installer", type=Path)
    cli.add_argument("key", type=Path)
    args = cli.parse_args()
    checksum = hashlib.sha256()
    with args.installer.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    content = json.dumps(dict(channel=args.channel, version=args.version, url=args.url,
                              size=args.installer.stat().st_size, sha256=checksum.hexdigest()),
                         separators=(",", ":")).encode()
    key = load_pem_private_key(args.key.read_bytes(), password=None)
    print(json.dumps(dict(manifest=base64.b64encode(content).decode(),
                          signature=base64.b64encode(key.sign(content)).decode())))
