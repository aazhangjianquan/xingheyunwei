"""Loopback-only knowledge access fixture; no production data or notification delivery."""
import os
from pathlib import Path
import tempfile


def run():
    with tempfile.TemporaryDirectory(prefix='wiki-access-ui-') as folder:
        root = Path(folder)
        os.environ.update(INSTANCE_DIR=str(root/'instance'), DATABASE_URL='sqlite:///'+str(root/'fixture.sqlite'),
            DATABASE_CONFIG_MODE='environment', REQUIRE_SHARED_KEYS='false', SEED_DEMO_DATA='false', REDIS_URL='')
        from contextlib import asynccontextmanager
        from app.main import app
        from app.database import Base, engine, SessionLocal
        from app.models import Department, User, UserRole
        from app.security import hash_password
        import uvicorn
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            parent = Department(name='产品研发中心');db.add(parent);db.flush()
            child = Department(name='产品研发一组',parent_id=parent.id)
            other = Department(name='商务中心');db.add_all([child,other]);db.flush()
            for username, name, dep in [('wiki-owner','张知远',parent.id),('wiki-reader','林知行',child.id),('wiki-outsider','周晓宁',other.id)]:
                db.add(User(username=username,display_name=name,department='产品研发' if dep!=other.id else '商务中心',
                    department_id=dep,role='applicant',role_grants=[UserRole(role='applicant')],password_hash=hash_password('WikiAccessFixture123!')))
            db.commit()
        @asynccontextmanager
        async def lifespan(_):
            yield
        app.router.lifespan_context=lifespan
        try: uvicorn.run(app,host='127.0.0.1',port=18079)
        finally: engine.dispose()


if __name__ == '__main__': run()
