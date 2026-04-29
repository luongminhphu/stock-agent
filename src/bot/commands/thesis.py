"""DEPRECATED — thesis.py has been split into focused modules.

This file is kept only for import backward-compatibility.
Do NOT add new code here.

See:
    thesis_embeds.py  — display constants + embed builders
    thesis_crud.py    — ThesisCrudCog  (/thesis add / list / close)
    thesis_review.py  — ThesisReviewCog (/review_thesis /recommendations /accept /reject)
"""

from src.bot.commands.thesis_crud import ThesisCrudCog
from src.bot.commands.thesis_embeds import (
    STATUS_ICON,
    TARGET_ICON,
    build_review_embed,
    confidence_bar,
)
from src.bot.commands.thesis_review import ThesisReviewCog

# Legacy alias so any external code that imported ThesisCog still works
ThesisCog = ThesisCrudCog

__all__ = [
    "ThesisCrudCog",
    "ThesisReviewCog",
    "ThesisCog",
    "build_review_embed",
    "confidence_bar",
    "STATUS_ICON",
    "TARGET_ICON",
]
