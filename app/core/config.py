import secrets
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)  # empty = unset

    app_name: str = "Stradit Campus Assessment Platform"
    environment: str = "dev"  # dev | prod
    database_url: str = "sqlite:///./assessment.db"
    # per worker process. total = workers x (size + overflow) must stay below PostgreSQL max_connections (default 100)
    db_pool_size: int = 15
    db_max_overflow: int = 10

    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = False  # True behind HTTPS

    admin_email: str = "admin@example.com"
    admin_password: str = ""

    cors_origins: str = "http://localhost:8000"
    rate_limit_enabled: bool = True
    rate_limit_storage: str = "memory://"  # redis://... in prod
    login_rate: str = "60/minute"  # per IP; labs share one NAT IP, keep generous
    submit_rate: str = "120/minute"
    bcrypt_rounds: int = 12  # password hashing cost; ~250 ms/login at 12. 10 is the OWASP floor (~4x faster) for huge login bursts
    max_failed_logins: int = 5
    lockout_minutes: int = 10

    # ---- email (invitations, password reset). console = print to the server log (dev); smtp = real delivery
    mail_backend: str = "console"          # console | smtp | memory (tests)
    mail_from: str = "Stradit Assessment <no-reply@localhost>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    public_url: str = "http://localhost:8000"   # base of the links in emails; must be the address candidates open
    reset_token_minutes: int = 30
    invite_token_days: int = 7
    reset_cooldown_seconds: int = 60       # at most one reset email per account per minute

    judge0_url: str = "http://localhost:2358"
    judge0_auth_token: str = ""
    judge0_timeout_s: int = 30

    answer_grace_seconds: int = 3  # tolerated network lag after server deadline
    violation_cluster_seconds: int = 2
    sweeper_interval_seconds: int = 20

    def model_post_init(self, __context) -> None:
        # hosts such as Koyeb/Heroku hand out postgres://; SQLAlchemy 2 only knows postgresql://
        if self.database_url.startswith("postgres://"):
            object.__setattr__(self, "database_url", "postgresql://" + self.database_url[len("postgres://"):])
        if self.environment == "prod" and self.database_url.startswith("sqlite"):
            raise ValueError("DATABASE_URL is not set (or is SQLite) in production; set it to a PostgreSQL URL")
        if not self.jwt_secret:
            if self.environment == "prod":
                raise ValueError("JWT_SECRET must be set in production")
            # dev only: ephemeral secret (tokens die on restart)
            object.__setattr__(self, "jwt_secret", secrets.token_urlsafe(48))


@lru_cache
def get_settings() -> Settings:
    return Settings()
