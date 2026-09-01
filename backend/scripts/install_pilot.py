#!/usr/bin/env python3
"""Install one checksum-pinned Pilot binary during the worker image build."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
import urllib.request
from pathlib import Path


PILOT_URL = "https://pilot.touchingtalk.com/download/pilot-linux-amd64.tar.gz"
PILOT_SHA256 = "cbc83b6ddb7be5da60ae22d989482e7ad14b7cf7c7e62cbef375538b0ec505b0"
PILOT_MEMBER = "pilot-linux-amd64"
PILOT_ARCHIVE_MEMBERS = frozenset({PILOT_MEMBER, "._pilot-linux-amd64"})


def main() -> None:
    with urllib.request.urlopen(PILOT_URL, timeout=120) as response:
        archive = response.read()
    actual = hashlib.sha256(archive).hexdigest()
    if actual != PILOT_SHA256:
        raise RuntimeError(f"Pilot checksum mismatch: {actual}")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        members = bundle.getmembers()
        if {member.name for member in members} != PILOT_ARCHIVE_MEMBERS or any(
            not member.isfile() for member in members
        ):
            raise RuntimeError("Pilot archive contains unexpected members")
        executable = next(member for member in members if member.name == PILOT_MEMBER)
        source = bundle.extractfile(executable)
        if source is None:
            raise RuntimeError("Pilot executable is missing")
        payload = source.read()
    destination = Path("/app/bin/pilot")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    os.chmod(destination, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


if __name__ == "__main__":
    main()
