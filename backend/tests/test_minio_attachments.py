"""Exercise private MinIO behavior through the SDK boundary without live credentials."""
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app import attachment_storage, attachments
from app.migrations import upgrade
from app.models import DeliveryAttachment
from scripts.migrate_attachments_to_minio import migrate
from test_attachments import AttachmentFixture, office_file


class S3Failure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('private access and secret must never be included in an API error')


class ObjectResponse:
    def __init__(self, data):
        self.data, self.closed, self.released = data, False, False

    def stream(self, amount):
        for offset in range(0, len(self.data), amount):
            yield self.data[offset:offset + amount]

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class MemoryMinio:
    def __init__(self):
        self.objects, self.calls, self.responses = {}, [], []
        self.failure, self.exists = None, True

    def bucket_exists(self, bucket):
        self.calls.append(('head', bucket))
        if self.failure:
            raise self.failure
        return self.exists

    def fput_object(self, bucket, key, source, **options):
        self.calls.append(('put', bucket, key))
        self.objects[bucket, key] = (Path(source).read_bytes(), options)
        if self.failure:
            raise self.failure

    def get_object(self, bucket, key):
        self.calls.append(('get', bucket, key))
        if self.failure:
            raise self.failure
        if (bucket, key) not in self.objects:
            raise S3Failure('NoSuchKey')
        response = ObjectResponse(self.objects[bucket, key][0])
        self.responses.append(response)
        return response

    def remove_object(self, bucket, key):
        self.calls.append(('delete', bucket, key))
        self.objects.pop((bucket, key), None)


class MinioAttachmentTests(AttachmentFixture):
    def setUp(self):
        super().setUp()
        self.minio = MemoryMinio()
        for item in [patch.dict(os.environ, {'ATTACHMENT_STORAGE': 'minio', 'MINIO_ENDPOINT': 'http://minio.invalid:9000',
                         'MINIO_ACCESS_KEY': 'isolated-access', 'MINIO_SECRET_KEY': 'isolated-secret',
                         'MINIO_BUCKET': 'private-test', 'MINIO_REGION': 'us-east-1'}),
                     patch('app.attachment_storage.minio_client', return_value=self.minio)]:
            item.start(); self.patches.append(item)

    def test_uploads_original_only_to_private_minio_and_proxies_download_and_text_preview(self):
        content = '交付清单：私有文档'.encode()
        response = self.upload(content=content)
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()
        with self.sessions() as db:
            stored = db.get(DeliveryAttachment, row['id'])
            key = stored.storage_key
            self.assertEqual((stored.storage_backend, stored.storage_bucket), ('minio', 'private-test'))
        name = 'delivery-attachments/originals/' + key
        value, options = self.minio.objects['private-test', name]
        self.assertEqual(value, content)
        self.assertEqual(options['metadata']['sha256'], hashlib.sha256(content).hexdigest())
        self.assertEqual(options['content_type'], 'text/plain')
        self.assertEqual(options['num_parallel_uploads'], 1)
        self.assertFalse((self.root / 'instance/delivery-attachments' / key).exists())
        for who in ['owner', 'colleague', 'reviewer', 'admin', 'ops']:
            download = self.document(row['id'], 'download', who)
            self.assertEqual(download.status_code, 200, download.text)
            self.assertEqual(download.content, content)
            self.assertNotIn('location', download.headers)
            self.assertEqual(self.document(row['id'], 'preview', who).text, content.decode())
        self.assertTrue(all(response.closed and response.released for response in self.minio.responses))
        listing = self.client.get(f'/api/deliveries/{self.item_id}/attachments', headers=self.headers['owner']).text
        for secret in ['storage_key', 'storage_bucket', 'private-test', 'isolated-access', 'isolated-secret', 'minio.invalid']:
            self.assertNotIn(secret, listing)

    def test_denied_operations_never_contact_minio(self):
        row = self.upload().json()
        self.minio.calls.clear()
        for who in ['other', 'admin', 'reviewer', 'colleague']:
            self.assertIn(self.upload(who=who).status_code, [403, 404])
        self.assertEqual(self.document(row['id'], 'download', 'other').status_code, 404)
        self.assertEqual(self.document(row['id'], 'preview', 'other').status_code, 404)
        self.assertEqual(self.document(row['id'], 'download', 'admin', self.deliveries['other']).status_code, 404)
        self.assertEqual(self.minio.calls, [])

    def test_office_preview_is_cached_in_private_minio_and_original_is_unchanged(self):
        for extension in ['.docx', '.xlsx']:
            content = office_file(extension)
            row = self.upload('交付' + extension, content).json()
            def converted(source, destination):
                self.assertEqual(source.read_bytes(), content)
                destination.write_bytes(b'%PDF-1.7\nprivate preview\n%%EOF')
            with patch('app.attachments.convert_to_pdf', side_effect=converted) as convert:
                for _ in range(2):
                    preview = self.document(row['id'], 'preview')
                    self.assertEqual(preview.status_code, 200, preview.text)
                    self.assertTrue(preview.content.startswith(b'%PDF-'))
                    self.assertIn('inline;', preview.headers['content-disposition'])
                convert.assert_called_once()
            self.assertEqual(self.document(row['id'], 'download').content, content)
        self.assertEqual(sum('/previews/' in key for _, key in self.minio.objects), 2)

    def test_db_or_put_failure_cleans_remote_object_and_never_creates_a_local_fallback(self):
        with patch('app.attachments.audit.record', side_effect=HTTPException(503, '日志事务失败')):
            self.assertEqual(self.upload().status_code, 503)
        self.assertFalse(self.minio.objects)
        self.minio.failure = S3Failure('AccessDenied')
        result = self.upload()
        self.assertEqual(result.status_code, 503, result.text)
        self.assertIn('MinIO 访问被拒绝', result.json()['detail'])
        self.assertNotIn('secret', result.text)
        self.assertFalse(self.minio.objects)
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(DeliveryAttachment)))
        self.assertFalse((self.root / 'instance/delivery-attachments').exists())

    def test_missing_objects_credentials_bucket_and_network_errors_have_specific_safe_messages(self):
        row = self.upload().json()
        self.minio.objects.clear()
        self.assertEqual(self.document(row['id'], 'download').status_code, 404)
        for error, fragment in [(S3Failure('AccessDenied'), '访问被拒绝'),
                                (S3Failure('SignatureDoesNotMatch'), 'AK/SK'),
                                (S3Failure('NoSuchBucket'), '桶不存在'),
                                (OSError('untrusted remote secret'), '暂不可用')]:
            self.minio.failure = error
            result = self.document(row['id'], 'download')
            self.assertEqual(result.status_code, 503, result.text)
            self.assertIn(fragment, result.json()['detail'])
            self.assertNotIn('secret', result.text)
        self.minio.failure = None
        with patch.dict(os.environ, {'MINIO_SECRET_KEY': ''}):
            result = self.upload()
            self.assertEqual(result.status_code, 503, result.text)
            self.assertIn('配置不完整', result.text)

    def test_tampered_originals_and_oversized_preview_caches_are_rejected(self):
        row = self.upload().json()
        key = next(iter(self.minio.objects))
        self.minio.objects[key] = (b'altered', {})
        result = self.document(row['id'], 'download')
        self.assertEqual(result.status_code, 502, result.text)
        office = self.upload('test.docx', office_file('.docx')).json()
        with self.sessions() as db:
            stored = db.get(DeliveryAttachment, office['id'])
            cached = ('private-test', attachment_storage.MinioDocuments.key(stored.storage_key, True))
        self.minio.objects[cached] = (b'not pdf', {})
        self.assertEqual(self.document(office['id'], 'preview').status_code, 502)
        self.minio.objects[cached] = (b'%PDF-' + b'x' * 100, {})
        with patch('app.attachments.MAX_ARCHIVE_SIZE', 20):
            self.assertEqual(self.document(office['id'], 'preview').status_code, 502)

    def test_original_pdf_is_served_inline_and_temporary_download_files_are_removed(self):
        row = self.upload('方案.pdf', b'%PDF-1.7\nexample\n%%EOF').json()
        temporary, factory = [], tempfile.TemporaryDirectory
        def track(**kwargs):
            result = factory(**kwargs); temporary.append(Path(result.name)); return result
        with patch('app.attachments.tempfile.TemporaryDirectory', side_effect=track):
            result = self.document(row['id'], 'preview')
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.headers['content-type'], 'application/pdf')
            self.assertIn('inline;', result.headers['content-disposition'])
            self.assertEqual(self.document(row['id'], 'download').status_code, 200)
        self.assertEqual(len(temporary), 2)
        self.assertTrue(all(not directory.exists() for directory in temporary))

    def test_each_attachment_keeps_its_original_bucket_when_new_upload_bucket_changes(self):
        row = self.upload().json()
        with patch.dict(os.environ, {'MINIO_BUCKET': 'next-private-bucket'}):
            self.assertEqual(self.document(row['id'], 'download').status_code, 200)
            new = self.upload().json()
        with self.sessions() as db:
            self.assertEqual(db.get(DeliveryAttachment, row['id']).storage_bucket, 'private-test')
            self.assertEqual(db.get(DeliveryAttachment, new['id']).storage_bucket, 'next-private-bucket')

    def test_legacy_local_documents_remain_readable_and_migrate_idempotently_after_verification(self):
        with patch.dict(os.environ, {'ATTACHMENT_STORAGE': 'local'}):
            row = self.upload().json()
        with self.sessions() as db:
            stored = db.get(DeliveryAttachment, row['id'])
            local = attachments.storage_path(stored.storage_key)
            self.assertEqual(stored.storage_backend, 'local')
        self.assertEqual(self.document(row['id'], 'download').status_code, 200)
        self.assertFalse(self.minio.objects)
        self.assertEqual(migrate(self.sessions), 1)
        self.assertEqual(migrate(self.sessions), 0)
        self.assertTrue(local.is_file())
        with self.sessions() as db:
            stored = db.get(DeliveryAttachment, row['id'])
            self.assertEqual((stored.storage_backend, stored.storage_bucket), ('minio', 'private-test'))
        self.assertEqual(self.document(row['id'], 'download').status_code, 200)

    def test_failed_legacy_migration_preserves_local_metadata_and_bytes(self):
        with patch.dict(os.environ, {'ATTACHMENT_STORAGE': 'local'}):
            row = self.upload().json()
        with patch.object(self.minio, 'get_object', side_effect=S3Failure('AccessDenied')):
            with self.assertRaises(HTTPException):
                migrate(self.sessions)
        with self.sessions() as db:
            stored = db.get(DeliveryAttachment, row['id'])
            self.assertEqual(stored.storage_backend, 'local')
            self.assertTrue(attachments.storage_path(stored.storage_key).is_file())
        self.assertEqual(self.document(row['id'], 'download').status_code, 200)


class MinioSchemaAndConfigTests(unittest.TestCase):
    def test_original_attachment_schema_gains_storage_location_without_changing_keys_or_defaults(self):
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE delivery_attachments (id INTEGER PRIMARY KEY, storage_key VARCHAR(64), filename VARCHAR(255))'))
            conn.execute(text("INSERT INTO delivery_attachments VALUES (1, 'original-key.txt', '交付说明.txt')"))
        upgrade(engine); upgrade(engine)
        with engine.connect() as conn:
            row = conn.execute(text('SELECT * FROM delivery_attachments')).mappings().one()
            self.assertEqual((row['storage_key'], row['filename'], row['storage_backend'], row['storage_bucket']),
                             ('original-key.txt', '交付说明.txt', 'local', ''))
        self.assertIn('storage_bucket', {column['name'] for column in inspect(engine).get_columns('delivery_attachments')})
        engine.dispose()

    def test_settings_validate_urls_keep_secrets_out_of_repr_and_never_change_bucket_policy(self):
        env = {'MINIO_ENDPOINT': 'https://minio.invalid:9443/', 'MINIO_ACCESS_KEY': 'hidden-ak',
               'MINIO_SECRET_KEY': 'hidden-sk', 'MINIO_BUCKET': 'private-test'}
        with patch.dict(os.environ, env):
            config = attachment_storage.minio_settings()
            self.assertTrue(config.secure)
            self.assertEqual(config.endpoint, 'minio.invalid:9443')
            self.assertNotIn('hidden-ak', repr(config)); self.assertNotIn('hidden-sk', repr(config))
            for endpoint in ['ftp://host', 'http://user:password@host', 'http://host/path', 'http://host?x=1']:
                with patch.dict(os.environ, {'MINIO_ENDPOINT': endpoint}):
                    with self.assertRaises(HTTPException):
                        attachment_storage.minio_settings()
            with patch('app.attachment_storage.minio_client', return_value=MemoryMinio()) as factory:
                store = attachment_storage.MinioDocuments(); store.check_bucket()
                self.assertEqual(factory.return_value.calls, [('head', 'private-test')])
                factory.return_value.exists = False
                with self.assertRaises(HTTPException):
                    store.check_bucket()


if __name__ == '__main__':
    unittest.main()
