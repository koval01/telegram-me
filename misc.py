"""Global misc module"""

from typing import Any

import logging
import json

from fastapi import FastAPI


def write_to_file(filename: str, content: str) -> None:
    """
    Writes content to a file.

    Args:
        filename (str): The name of the file to write to.
        content (str): The content to write to the file.

    Returns:
        None

    Raises:
        IOError: If an error occurs while writing to the file.
    """
    try:
        with open(filename, 'w', encoding='utf-8') as file:
            file.write(content)
    except IOError as e:
        logging.error("Error writing data to file: %s", e)


def open_api(app: FastAPI) -> None:
    """
    Generate and write the OpenAPI JSON schema for the provided FastAPI application.

    Parameters:
    - app (FastAPI): The FastAPI application instance for which to generate the OpenAPI schema.

    Returns:
    - None: This function does not return anything. It writes the OpenAPI schema to a file.

    This function generates the OpenAPI schema for the provided FastAPI application
    and writes it to a JSON file located at "docs/static/openapi.json".
    If the OpenAPI schema cannot be generated (for example, if the FastAPI
    application does not have any documented endpoints), nothing is written to the file.
    """
    openapi: dict[str, Any] = app.openapi()
    if openapi:
        write_to_file(
            "docs/static/openapi.json",
            json.dumps(openapi)
        )
