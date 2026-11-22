"""应用配置中心。

设计要点：
1. 所有配置只从仓库根目录的 `.env` 读取，避免 cwd 不同导致读不到；
2. `.env` 里写的是相对 `backend/` 的路径（如 `../data/app.db`），
   在这里统一解析成绝对路径，容器内可用 `/app/data/app.db` 覆盖；
3. 用 `lru_cache` 保证全进程只构造一次 Settings。
"""

from __future__ import annotations

from functools import lru_cache
import logging
from pathlib import Path
import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# app/config.py -> app/ -> backend/ -> 仓库根
BACKEND_DIR: Path = Path(__file__).resolve().parent.parent
REPO_ROOT: Path = BACKEND_DIR.parent
ENV_FILE: Path = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """环境变量配置。字段名与 .env 中的变量名大小写无关地一一对应。"""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 应用 ----
    app_name: str = "cloud-support-graph"
    app_env: str = "development"

    # ---- 数据库 ----
    app_db_path: str = "../data/app.db"
    checkpoint_db_path: str = "../data/checkpoints.db"

    # ---- JWT ----
    jwt_secret: str = "change-this-to-a-long-random-secret-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720
    # 默认关闭免密演示登录；本地开发由 .env 显式开启，公网生产强制禁止
    enable_demo_login: bool = False
    # 生产环境初始化种子账号的强口令（APP_ENV=production 时禁止沿用仓库公开的 Passw0rd!）
    seed_default_password: str = ""

    # ---- 大模型 ----
    llm_base_url: str = ""
    llm_model_name: str = "deepseek-v4.1-flash"
    llm_api_key: str = ""
    llm_timeout_seconds: int = 120
    # OpenCode Go 网关要求带 x-opencode-session 头；留空则按进程自动生成稳定 ID
    llm_session_id: str = ""

    # ---- 检索 ----
    rag_top_k: int = 4
    rag_min_score: float = 0.35
    max_tool_steps: int = 4

    # ---- 可观测（可选） ----
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # ---- 派生路径 ----
    @property
    def app_db(self) -> Path:
        """业务库绝对路径（data/app.db）。"""
        return self._resolve(self.app_db_path)

    @property
    def checkpoint_db(self) -> Path:
        """LangGraph 快照库绝对路径（data/checkpoints.db）。"""
        return self._resolve(self.checkpoint_db_path)

    @property
    def data_dir(self) -> Path:
        return self.app_db.parent

    @staticmethod
    def _resolve(raw: str) -> Path:
        p = Path(raw)
        return p.resolve() if p.is_absolute() else (BACKEND_DIR / p).resolve()

    def validate_runtime_security(self) -> None:
        """应用启动期安全校验。

        策略分级：
        - 生产环境（APP_ENV=production 或非 dev/test）：
          1. 占位/过短 JWT_SECRET 直接 fail-fast 拒绝启动；
          2. 严禁开启 ENABLE_DEMO_LOGIN=true。
        - 本地开发/测试环境（development/dev/local/test）：
          若 JWT_SECRET 仍为模板占位值，打印告警并自动生成进程内临时随机密钥，
          保证 `copy .env.example .env` 首次启动不崩溃，同时避免沿用固定占位串签发令牌。
        """
        env = (self.app_env or "development").strip().lower()
        is_dev = env in ("development", "dev", "local", "test")

        secret = (self.jwt_secret or "").strip()
        lowered = secret.lower()
        is_placeholder = (
            not secret
            or len(secret) < 32
            or "change-this" in lowered
            or "replace-me" in lowered
            or "your-secret" in lowered
            or secret == "secret"
        )
        if is_placeholder:
            if is_dev:
                self.jwt_secret = secrets.token_hex(32)
                logger.warning(
                    "检测到当前为开发环境 (%s) 且 JWT_SECRET 为占位/过短值，已自动生成进程内临时随机密钥；"
                    "生产环境启动将强制拒绝，请在 .env 中配置高熵随机 JWT_SECRET。",
                    env,
                )
            else:
                raise RuntimeError(
                    "安全校验失败：生产环境 JWT_SECRET 为空、长度不足 32 字节或仍为模板占位值。"
                    "请在 .env 中配置高熵随机密钥（例如运行 openssl rand -hex 32 生成）。"
                )
        if not is_dev and self.enable_demo_login:
            raise RuntimeError(
                "安全校验失败：非开发环境（如 APP_ENV=production）严禁开启 ENABLE_DEMO_LOGIN=true，"
                "请关闭免密演示登录后再启动服务。"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例。"""
    return Settings()
