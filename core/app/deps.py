"""FastAPI dependencies shared by routers."""

from fastapi import Request

from .container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container
