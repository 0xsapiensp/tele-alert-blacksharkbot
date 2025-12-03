import requests
import os
from typing import Optional


class TelegramBot:
    """
    A simple Telegram bot class for sending messages to a channel.
    
    Usage:
        bot = TelegramBot(token="YOUR_BOT_TOKEN", channel_id="@your_channel")
        bot.send_message("Hello from the bot!")
    """
    
    BASE_URL = "https://api.telegram.org/bot"
    
    def __init__(self, token: Optional[str] = None, channel_id: Optional[str] = None):
        """
        Initialize the Telegram bot.
        
        Args:
            token: Telegram bot token (or set TELEGRAM_BOT_TOKEN env var)
            channel_id: Channel username (e.g., "@mychannel") or chat ID (or set TELEGRAM_CHANNEL_ID env var)
        """
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.channel_id = channel_id or os.getenv("TELEGRAM_CHANNEL_ID")
        
        if not self.token:
            raise ValueError("Telegram bot token is required. Provide it as argument or set TELEGRAM_BOT_TOKEN env var.")
        if not self.channel_id:
            raise ValueError("Channel ID is required. Provide it as argument or set TELEGRAM_CHANNEL_ID env var.")
        
        self.api_url = f"{self.BASE_URL}{self.token}"
    
    def send_message(self, text: str, parse_mode: Optional[str] = None) -> bool:
        """
        Send a message to the configured channel.
        
        Args:
            text: Message text to send
            parse_mode: Optional parse mode ("HTML", "Markdown", or "MarkdownV2")
        
        Returns:
            True if message was sent successfully, False otherwise
        """
        url = f"{self.api_url}/sendMessage"
        
        payload = {
            "chat_id": self.channel_id,
            "text": text,
        }
        
        if parse_mode:
            payload["parse_mode"] = parse_mode
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Failed to send Telegram message: {e}")
            if hasattr(e.response, 'text'):
                print(f"Response: {e.response.text}")
            return False
    
    def send_message_html(self, text: str) -> bool:
        """
        Send a message with HTML formatting.
        
        Args:
            text: Message text with HTML tags
        
        Returns:
            True if message was sent successfully, False otherwise
        """
        return self.send_message(text, parse_mode="HTML")
    
    def send_message_markdown(self, text: str) -> bool:
        """
        Send a message with Markdown formatting.
        
        Args:
            text: Message text with Markdown syntax
        
        Returns:
            True if message was sent successfully, False otherwise
        """
        return self.send_message(text, parse_mode="Markdown")