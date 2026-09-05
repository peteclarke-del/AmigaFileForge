from pathlib import Path


def application_version() -> str:
    """Read the packaged release identifier from its single source of truth."""
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(
            encoding="ascii"
        ).strip()
    except OSError:
        return "development"
