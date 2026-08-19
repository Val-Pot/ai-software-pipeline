from typing import Protocol


class NotifierPort(Protocol):
    async def send_text(self, chat_id: int, text: str) -> None: ...

    async def send_document(
        self, chat_id: int, filename: str, content: bytes, caption: str = ""
    ) -> None: ...

    async def send_merge_confirmation(
        self, chat_id: int, job_id: str, text: str
    ) -> None: ...
