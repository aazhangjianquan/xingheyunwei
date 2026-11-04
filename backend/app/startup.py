"""Serialize schema upgrades and seeding across independently starting replicas."""
from contextlib import contextmanager
import fcntl
from sqlalchemy import text
from .config import INSTANCE_DIR


@contextmanager
def initialization_lock(engine):
    if engine.dialect.name == 'mysql':
        # Connection-scoped and independent of DDL transaction/implicit commits.
        with engine.connect() as connection:
            acquired = connection.scalar(text("SELECT GET_LOCK('project_workspace_initialize', 180)"))
            if acquired != 1:
                raise RuntimeError('等待数据库初始化锁超时，请检查其他后端副本启动状态')
            try:
                yield
            finally:
                connection.execute(text("SELECT RELEASE_LOCK('project_workspace_initialize')"))
    else:
        # SQLite is for single-host development; production replicas use MySQL.
        with (INSTANCE_DIR / 'startup.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
