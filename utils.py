"""Common utilities for supermarket scraping."""
import logging
from typing import Dict


def setup_logging() -> None:
    """Configure common logging for all supermarket modules."""
    # Create the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Remove any existing handlers to avoid duplication
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Set up file handler
    file_handler = logging.FileHandler('supermarket.log', mode='a')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Configure console output
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)  # Keep console at INFO level
    console.setFormatter(formatter)
    root_logger.addHandler(console)


def get_module_logger(module_name: str) -> logging.Logger:
    """Get a logger for a specific supermarket module.

    Args:
        module_name: Name of the supermarket module (e.g., 'shufersal', 'tivtaam')

    Returns:
        Logger configured for the specific module.
    """
    logger = logging.getLogger(module_name)
    logger.setLevel(logging.DEBUG)
    return logger


def get_browser_headers(referer_host: str) -> Dict[str, str]:
    """Generate browser-like headers for making requests to supermarket APIs.

    Args:
        referer_host: The host URL to use as referer
                     (e.g., 'https://www.shufersal.co.il')

    Returns:
        Dictionary of headers mimicking a legitimate browser request.
    """
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Referer": f"{referer_host}/",
    }
