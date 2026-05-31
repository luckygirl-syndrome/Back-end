from typing import Any, Optional


def success(result: Any = None, message: str = "OK", code: str = "200") -> dict:
    return {
        "isSuccess": True,
        "code": code,
        "message": message,
        "result": result,
    }


def fail(message: str, code: str = "400") -> dict:
    return {
        "isSuccess": False,
        "code": code,
        "message": message,
        "result": None,
    }
