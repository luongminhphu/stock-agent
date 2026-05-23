"""Core Intelligence Engine — bounded context.

Owner: core segment.
Responsible for: cross-segment snapshot, signal synthesis, verdict dispatch,
feedback recording, and self-improvement suggestions.

This segment is a consumer of all other segments.
It must never be imported by other segments (dependency goes one way only).
"""
