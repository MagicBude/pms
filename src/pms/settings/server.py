"""内网和云端共享的严格服务器配置构造。"""

from pms.settings.environment import read_csv, require


def postgres_database() -> dict[str, str]:
    """读取 PostgreSQL 配置，不接受隐式 SQLite 回退。"""
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": require("PMS_DB_NAME"),
        "USER": require("PMS_DB_USER"),
        "PASSWORD": require("PMS_DB_PASSWORD"),
        "HOST": require("PMS_DB_HOST"),
        "PORT": require("PMS_DB_PORT"),
    }


def allowed_hosts() -> list[str]:
    """要求服务器显式声明可接受的 Host 头。"""
    return read_csv("PMS_ALLOWED_HOSTS", required=True)
