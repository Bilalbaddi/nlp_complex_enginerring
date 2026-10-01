from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class Envelope(BaseModel, Generic[T]):
    """Every API response: {success, data, error}."""

    success: bool = True
    data: T | None = None
    error: ErrorBody | None = None


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    pages: int


class Page(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class PageParams(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(25, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def ok(data: Any = None) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def make_page(items: list[Any], total: int, params: PageParams) -> dict[str, Any]:
    pages = max(1, -(-total // params.page_size))
    return {
        "items": items,
        "meta": {"page": params.page, "page_size": params.page_size, "total": total, "pages": pages},
    }
