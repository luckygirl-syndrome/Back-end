import os
import atexit
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from posthog import Posthog


def init_sentry(dsn: str, env: str):
    if not dsn:
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=env,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
            RedisIntegration(),
        ],
        # 전체 요청의 20%만 성능 추적 (배포 후 트래픽 보고 조정)
        traces_sample_rate=0.2,
        send_default_pii=True,
    )


def init_posthog() -> Posthog | None:
    token = os.getenv("POSTHOG_PROJECT_TOKEN")
    if not token:
        return None
    client = Posthog(
        token,
        host=os.getenv("POSTHOG_HOST", "https://us.i.posthog.com"),
        enable_exception_autocapture=True,
    )
    atexit.register(client.shutdown)
    return client


posthog_client: Posthog | None = init_posthog()
