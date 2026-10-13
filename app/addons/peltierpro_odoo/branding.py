from typing import Any


BRANDING = {
    "provider": "Zen Business Solutions",
    "powered_by": "Powered by: Zen Business Solutions",
}


def branded_response(data: dict[str, Any]):
    return {
        **data,
        "branding": BRANDING,
    }