"""Copy legacy local documents to MinIO, verifying bytes before switching each row."""
import hashlib
from pathlib import Path
import tempfile

from fastapi import HTTPException
from sqlalchemy import select

from app.attachment_storage import MinioDocuments, upload_backend
from app.attachments import MAX_FILE_SIZE, storage_path
from app.database import SessionLocal
from app.models import DeliveryAttachment


def migrate(sessions=SessionLocal):
    if upload_backend() != 'minio':
        raise HTTPException(503, '当前附件存储不是 MinIO，已停止历史附件迁移')
    store = MinioDocuments()
    store.check_bucket()
    with sessions() as db:
        ids = list(db.scalars(select(DeliveryAttachment.id).where(DeliveryAttachment.storage_backend == 'local')))
    copied = 0
    for attachment_id in ids:
        with sessions() as db:
            row = db.scalar(select(DeliveryAttachment).where(DeliveryAttachment.id == attachment_id).with_for_update())
            if not row or row.storage_backend != 'local':
                continue
            source = storage_path(row.storage_key)
            if not source.is_file() or source.stat().st_size != row.size or hashlib.sha256(source.read_bytes()).hexdigest() != row.sha256:
                raise HTTPException(409, f'历史附件 #{attachment_id} 缺失或校验失败，已停止迁移；原存储记录保持不变')
            store.put(row.storage_key, source, row.content_type, row.sha256)
            with tempfile.TemporaryDirectory(prefix='attachment-migration-') as directory:
                store.fetch(row.storage_key, Path(directory) / 'verify', MAX_FILE_SIZE, size=row.size, digest=row.sha256)
            row.storage_backend = 'minio'
            row.storage_bucket = store.bucket
            db.commit()
            copied += 1
            print(f'附件 #{attachment_id} 已复制、校验并切换至 MinIO；原本地文件保留。')
    return copied


if __name__ == '__main__':
    try:
        count = migrate()
    except HTTPException as exc:
        raise SystemExit(f'附件迁移未完成：{exc.detail}') from None
    print(f'历史附件迁移完成：本次 {count} 个；可重复执行。')
