from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
import smtplib
import ssl


@dataclass(frozen=True)
class EmailConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipient: str

    @property
    def configured(self) -> bool:
        return all((self.host, self.username, self.password, self.sender, self.recipient))


class EmailNotifier:
    def __init__(self, config: EmailConfig) -> None:
        self.config = config

    def send(self, subject: str, body: str) -> None:
        if not self.config.configured:
            raise ValueError("email notification is not fully configured")
        message = EmailMessage()
        message["From"] = self.config.sender
        message["To"] = self.config.recipient
        message["Subject"] = f"[crypto-trader] {subject}"
        message.set_content(body)
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(
            self.config.host, self.config.port, context=context, timeout=15
        ) as server:
            server.login(self.config.username, self.config.password)
            server.send_message(message)

