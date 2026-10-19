"""Standalone Vercel Python function: POST multipart file → xlsx extract."""

from engine.vercel_fn import extract_payload, handler

__all__ = ["handler", "extract_payload"]
