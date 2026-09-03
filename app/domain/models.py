from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Product(BaseModel):
    sku: str = Field(min_length=3)
    title: str = Field(min_length=3)
    bullets: list[str] = Field(min_length=1)
    category: str

    @field_validator("sku")
    @classmethod
    def sku_upper(cls, value: str) -> str:
        return value.upper()


class Inventory(BaseModel):
    sku: str
    available: int = Field(ge=0)
    reorder_point: int = Field(ge=0)


class Order(BaseModel):
    order_id: str
    sku: str
    status: str
    age_days: int = Field(ge=0)


class ListingAdvice(BaseModel):
    sku: str
    score: int = Field(ge=0, le=100)
    issues: list[str]
    suggested_title: str = Field(min_length=1)
    suggested_bullets: list[str] = Field(min_length=5, max_length=5)
    inventory_risk: str | None = None
    order_risk: str | None = None

    @field_validator("suggested_title")
    @classmethod
    def suggested_title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("suggested_title must not be blank")
        return value

    @field_validator("suggested_bullets")
    @classmethod
    def suggested_bullets_not_blank(cls, value: list[str]) -> list[str]:
        if any(not bullet.strip() for bullet in value):
            raise ValueError("suggested_bullets must not contain blank values")
        return value
