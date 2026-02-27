from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    threads_access_token: str
    deepinfra_token: str
    deepinfra_base_url: str = "https://api.deepinfra.com/v1/openai"
    deepinfra_model: str = "meta-llama/Meta-Llama-3.1-70B-Instruct"
    scoring_concurrency: int = 20
    threads_posts_per_user: int = 100

    model_config = {"env_file": ".env"}


settings = Settings()
