"""Signed channel manifests and verified Windows installer handoff."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import Version

from examai.branding import VERSION


def https(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("An HTTPS release URL is required")
    return url


def verify_manifest(envelope, public_key, channel):
    content = base64.b64decode(envelope["manifest"], validate=True)
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True)).verify(
        base64.b64decode(envelope["signature"], validate=True), content)
    manifest = json.loads(content)
    if manifest["channel"] != channel or channel not in {"beta", "stable"}:
        raise ValueError("Wrong update channel")
    if not isinstance(manifest["size"], int) or not 0 < manifest["size"] <= 2_000_000_000:
        raise ValueError("Invalid release size")
    if Version(manifest["version"]) <= Version(VERSION):
        return None
    https(manifest["url"])
    return manifest


def fetch_release(base, key, channel):
    response = httpx.get(https(base.rstrip("/") + f"/{channel}.json"), timeout=10)
    response.raise_for_status()
    return verify_manifest(response.json(), key, channel)


def download(manifest):
    folder = Path(tempfile.mkdtemp(prefix="examai-update-"))
    target = folder / "EXAM-AI-Setup.exe"
    digest = hashlib.sha256()
    size = 0
    try:
        with httpx.stream("GET", https(manifest["url"]), timeout=30) as response, target.open("xb") as file:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > manifest["size"]:
                    raise ValueError("Release exceeds its signed size")
                digest.update(chunk)
                file.write(chunk)
        if size != manifest["size"] or digest.hexdigest() != manifest["sha256"]:
            raise ValueError("Release checksum mismatch")
        return target
    except Exception:
        target.unlink(missing_ok=True)
        folder.rmdir()
        raise


def launch(path):
    os.startfile(path)
