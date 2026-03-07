"""Somerville legal QA web app package."""


def create_app(*args, **kwargs):
    # Lazy import to avoid requiring FastAPI in utility-only workflows.
    from .api import create_app as _create_app

    return _create_app(*args, **kwargs)


__all__ = ["create_app"]
