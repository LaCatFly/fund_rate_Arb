from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    tg_bot_token: str = ""
    tg_chat_id: str = ""
    tg_parse_mode: str = "MarkdownV2"

    model_config = {"env_prefix": "", "env_file": ".env"}


settings = Settings()
