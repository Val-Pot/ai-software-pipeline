"""
Inline keyboard builders for Telegram bot interfaces.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def confirm_task_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for confirming task submission."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Confirm & Launch", callback_data="confirm_task"),
                InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_task"),
            ]
        ]
    )


def review_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Keyboard for reviewing and approving/rejecting a pull request."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👍 Approve PR", callback_data=f"approve_pr:{job_id}"),
                InlineKeyboardButton(text="👎 Request Changes", callback_data=f"reject_pr:{job_id}"),
            ]
        ]
    )
