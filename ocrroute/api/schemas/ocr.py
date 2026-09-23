# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

from typing import Any
from pydantic import BaseModel, Field, field_validator, model_validator


class OcrJsonRequest(BaseModel):
    url: str | None = None
    base64: str | None = None
    path: str | None = Field(default=None, description='Server-local file path (panel/desktop embedded mode only)')
    route: str | None = None
    engine: str | None = None
    provider_id: str | None = None
    language: list[str] | str = ['en']
    pages: str = ''
    pdf_dpi: int = 150
    prompt: str = ''
    options: dict[str, Any] = Field(default_factory=dict)
    preprocess: dict[str, Any] = Field(default_factory=dict)
    output: list[str] = ['json']
    stop_condition: dict[str, Any] = Field(default_factory=dict)
    strategy: str = ''
    hints: dict[str, Any] = Field(default_factory=dict)
    cache: bool = True
    async_: bool = Field(default=False, alias='async')
    webhook_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {'populate_by_name': True}

    @field_validator('language', mode='before')
    @classmethod
    def _lang(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [x.strip() for x in v.split(',') if x.strip()] or ['en']
        return list(v) if v else ['en']

    @model_validator(mode='after')
    def _exclusive(self) -> 'OcrJsonRequest':
        if self.engine and self.route:
            raise ValueError("'engine' and 'route' are mutually exclusive")
        return self


class RunEnvelope(BaseModel):
    run_id: str
    status: str
    cached: bool = False
    result: dict[str, Any]
    routing: dict[str, Any]
    usage: dict[str, Any]
    artifacts: list[dict[str, Any]]
    metadata: dict[str, Any] = {}
    error_code: str | None = None
    error_message: str | None = None
