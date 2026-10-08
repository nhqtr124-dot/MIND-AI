"""Domain services. Importing the handler modules registers their job handlers."""


def register_handlers() -> None:
    from . import (  # noqa: F401
        agents,
        builder,
        cad_service,
        docs_service,
        image_service,
        memory_service,
        research_service,
    )
