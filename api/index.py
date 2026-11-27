"""Vercel entry point: exposes the FastAPI app as a serverless function.

The job worker does NOT run here (serverless functions are frozen between
requests). Run `python -m app.worker` on an always-on host with the same env.
"""
from app.main import app  # noqa: F401
