"""WSGI composition root for the container/web host."""

from .server import create_app


app = create_app()


__all__ = ["app"]
