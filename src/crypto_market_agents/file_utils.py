"""Atomic local text exports and collision-free report bundles."""

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile


def atomic_write_text(path: str | Path, content: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def reserve_report_paths(directory: str | Path, base: str):
    """Reserve a bundle across processes; incomplete bundles are never overwritten."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        name = base if index == 1 else f"{base}_{index}"
        paths = tuple(directory / f"{name}{extension}" for extension in (".md", ".json", ".html"))
        reservation = directory / f".{name}.reserved"
        try:
            descriptor = os.open(reservation, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            index += 1
            continue
        os.close(descriptor)
        if any(path.exists() for path in paths):
            reservation.unlink()
            index += 1
            continue
        break
    try:
        yield paths
    finally:
        reservation.unlink(missing_ok=True)
