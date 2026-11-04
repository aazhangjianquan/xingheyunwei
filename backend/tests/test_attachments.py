"""Private document uploads, viewing permissions, durable files and previews."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import attachments
from app.database import Base, get_db
from app.main import app
from app.models import Approval, AuditLog, ChangeRequest, DeliveryAttachment, DeliveryProject, Project, ProjectMember, User, UserRole
from app.rate_limit import RateDecision
from app.security import make_token


def office_file(kind, macro=False):
    data = io.BytesIO()
    namespace = 'http://schemas.openxmlformats.org/'
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
        if kind == '.docx':
            archive.writestr('[Content_Types].xml', f'<Types xmlns="{namespace}package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            archive.writestr('_rels/.rels', f'<Relationships xmlns="{namespace}package/2006/relationships"><Relationship Id="rId1" Type="{namespace}officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            archive.writestr('word/document.xml', f'<w:document xmlns:w="{namespace}wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Delivery plan: document preview validation.</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
        else:
            archive.writestr('[Content_Types].xml', f'<Types xmlns="{namespace}package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            archive.writestr('_rels/.rels', f'<Relationships xmlns="{namespace}package/2006/relationships"><Relationship Id="rId1" Type="{namespace}officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            archive.writestr('xl/workbook.xml', f'<workbook xmlns="{namespace}spreadsheetml/2006/main" xmlns:r="{namespace}officeDocument/2006/relationships"><sheets><sheet name="Delivery" sheetId="1" r:id="rId1"/></sheets></workbook>')
            archive.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="{namespace}package/2006/relationships"><Relationship Id="rId1" Type="{namespace}officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            archive.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{namespace}spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Delivery checklist</t></is></c><c r="B1"><v>42</v></c></row></sheetData></worksheet>')
        if macro: archive.writestr('word/vbaProject.bin', b'macro payload')
    return data.getvalue()


class AttachmentFixture(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='yunwei-attachments-')
        self.root = Path(self.folder.name)
        self.engine = create_engine('sqlite:///' + str(self.root / 'db.sqlite'))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [patch.dict(os.environ, {'ATTACHMENT_STORAGE': 'local'}), patch('app.main.SessionLocal', self.sessions),
            patch('app.main.check_request_rate', return_value=RateDecision()), patch('app.attachments.INSTANCE_DIR', self.root / 'instance')]
        for item in self.patches: item.start()
        def dependency():
            with self.sessions() as db: yield db
        app.dependency_overrides[get_db] = dependency
        self.client = TestClient(app)
        self.ids = {}
        with self.sessions() as db:
            for name, role in [('owner','applicant'),('colleague','applicant'),('reviewer','department_head'),('other','applicant'),('admin','admin'),('ops','operator')]:
                user = User(username=name, display_name=name, role=role, department='独立部门-'+name,
                    password_hash='unused', role_grants=[UserRole(role=role)])
                db.add(user); db.flush(); self.ids[name] = user.id
            project = Project(name='交付关联项目', department='研发', created_by=self.ids['admin'])
            db.add(project); db.flush()
            for name in ['owner','colleague']:
                db.add(ProjectMember(project_id=project.id, user_id=self.ids[name], role='applicant'))
            self.changes = {}
            for name in ['owner','admin','other']:
                item = ChangeRequest(project_id=project.id if name!='other' else None, code='CHG-'+name, name='变更-'+name, change_type='配置变更', environment='生产', window_start='2026-10-28T22:00', window_end='2026-10-28T23:00', problem_description='问题描述', owner_id=self.ids[name])
                db.add(item); db.flush(); self.changes[name] = item.id
            db.add(Approval(object_type='change', object_id=self.changes['owner'], stage='review', assignee_id=self.ids['reviewer']))
            self.deliveries = {}
            for name in ['owner','admin','other']:
                item = DeliveryProject(project_id=project.id if name!='other' else None, code='DEL-'+name, name='项目-'+name,
                    owner_id=self.ids[name], customer='客户', cloud_type='专有云', deployment_mode='云平台', environment='POC', compute_type='虚拟机')
                db.add(item); db.flush(); self.deliveries[name] = item.id
            db.add(Approval(object_type='delivery', object_id=self.deliveries['owner'], stage='review', assignee_id=self.ids['reviewer']))
            db.commit()
        self.headers = {name: {'Authorization':'Bearer '+make_token(uid)} for name, uid in self.ids.items()}
        self.item_id = self.deliveries['owner']

    def tearDown(self):
        self.client.close(); app.dependency_overrides.pop(get_db, None)
        for item in reversed(self.patches): item.stop()
        self.engine.dispose(); self.folder.cleanup()

    def upload(self, name='交付说明.txt', content=None, who='owner', item_id=None):
        return self.client.post(f'/api/deliveries/{item_id or self.item_id}/attachments', params={'filename':name},
            content='交付说明：测试内容'.encode() if content is None else content, headers={**self.headers[who],'Content-Type':'application/octet-stream'})

    def document(self, attachment_id, action, who='owner', item_id=None):
        return self.client.get(f'/api/deliveries/{item_id or self.item_id}/attachments/{attachment_id}/{action}', headers=self.headers[who])

    def upload_change(self, name='变更说明.txt', content=None, who='owner', item_id=None):
        target = item_id or self.changes['owner']
        return self.client.post(f'/api/changes/{target}/attachments', params={'filename':name},
            content='变更依据：测试内容'.encode() if content is None else content, headers={**self.headers[who],'Content-Type':'application/octet-stream'})

    def change_document(self, attachment_id, action, who='owner', item_id=None):
        target = item_id or self.changes['owner']
        return self.client.get(f'/api/changes/{target}/attachments/{attachment_id}/{action}', headers=self.headers[who])


class ChangeAttachmentTests(AttachmentFixture):
    def test_change_documents_images_private_access_and_images_are_previewed_inline(self):
        text = self.upload_change().json()
        self.assertEqual(text['preview_type'], 'text')
        preview = self.change_document(text['id'], 'preview')
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertIn('变更依据', preview.text)
        image_data = bytes.fromhex('89504e470d0a1a0a') + b'0' * 16
        image = self.upload_change('截图.png', image_data)
        self.assertEqual(image.status_code, 201, image.text)
        row = image.json()
        self.assertEqual(row['preview_type'], 'image')
        result = self.change_document(row['id'], 'preview')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.headers['content-type'], 'image/png')
        self.assertEqual(result.content, image_data)
        self.assertEqual(self.change_document(row['id'], 'download').content, result.content)
        self.assertEqual(self.upload_change('fake.png', b'not an image').status_code, 400)
        outsider = self.client.get(f"/api/changes/{self.changes['owner']}/attachments/{row['id']}", headers=self.headers['other'])
        self.assertEqual(outsider.status_code, 404)
        self.assertEqual(self.change_document(row['id'], 'preview', 'other').status_code, 404)
        self.assertEqual(self.upload_change(who='reviewer').status_code, 403)
        self.assertEqual(self.upload_change(who='admin').status_code, 403)


class AttachmentTests(AttachmentFixture):
    def test_creator_upload_and_existing_viewers_can_list_download_and_preview(self):
        text = '交付说明：<script>alert("never execute")</script>'
        response = self.upload(content=text.encode())
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()
        self.assertNotIn('storage_key', row); self.assertNotIn('sha256', row)
        self.assertEqual((row['filename'],row['size'],row['uploaded_by']),('交付说明.txt',len(text.encode()),self.ids['owner']))
        for who in ['owner','colleague','reviewer','admin','ops']:
            with self.subTest(who=who):
                listing = self.client.get(f'/api/deliveries/{self.item_id}/attachments', headers=self.headers[who])
                self.assertEqual(listing.status_code,200,listing.text)
                self.assertEqual(listing.json()['can_upload'],who=='owner')
                self.assertEqual(listing.json()['items'][0]['id'],row['id'])
                download = self.document(row['id'],'download',who)
                self.assertEqual(download.status_code,200,download.text)
                self.assertEqual(download.content,text.encode())
                self.assertIn('attachment;',download.headers['content-disposition'])
                preview = self.document(row['id'],'preview',who)
                self.assertEqual(preview.status_code,200,preview.text)
                self.assertEqual(preview.text,text)
                self.assertIn('text/plain',preview.headers['content-type'])
                self.assertEqual(preview.headers['x-content-type-options'],'nosniff')
                self.assertEqual(preview.headers['cache-control'],'no-store')
        with self.sessions() as db:
            saved = db.get(DeliveryAttachment,row['id'])
            self.assertEqual(attachments.storage_path(saved.storage_key).read_bytes(),text.encode())
            self.assertEqual(saved.sha256,hashlib.sha256(text.encode()).hexdigest())
            logs = db.scalars(select(AuditLog).where(AuditLog.record_kind=='business')).all()
            self.assertEqual(len(logs),11)
            self.assertTrue(all(log.target_id==str(self.item_id) and log.target_code=='DEL-owner' for log in logs))
            self.assertNotIn(text,'\n'.join(log.changes for log in logs))

    def test_non_creators_cannot_upload_even_administrators_but_admin_can_upload_to_own(self):
        for who in ['colleague','reviewer','admin','ops','other']:
            response = self.upload(who=who)
            self.assertEqual(response.status_code,404 if who=='other' else 403,response.text)
        result = self.upload(who='admin',item_id=self.deliveries['admin'])
        self.assertEqual(result.status_code,201,result.text)
        self.assertEqual(result.json()['uploaded_by'],self.ids['admin'])

    def test_outsiders_cross_delivery_ids_anonymous_and_disabled_accounts_are_rejected(self):
        row = self.upload().json()
        for action in ['download','preview']:
            self.assertEqual(self.document(row['id'],action,'other').status_code,404)
            self.assertEqual(self.document(row['id'],action,'admin',self.deliveries['other']).status_code,404)
            self.assertEqual(self.client.get(f'/api/deliveries/{self.item_id}/attachments/{row["id"]}/{action}').status_code,401)
        self.assertEqual(self.client.get(f'/api/deliveries/{self.item_id}/attachments',headers=self.headers['other']).status_code,404)
        self.assertEqual(self.client.post(f'/api/deliveries/{self.item_id}/attachments?filename=a.txt',content=b'text').status_code,401)
        with self.sessions() as db:
            db.get(User,self.ids['owner']).active=False; db.commit()
        self.assertEqual(self.document(row['id'],'download').status_code,401)
        self.assertEqual(self.upload().status_code,401)

    def test_file_names_do_not_control_storage_paths_and_missing_files_report_clearly(self):
        response=self.upload(name='../../目录\\方案.TXT')
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()['filename'],'方案.TXT')
        with self.sessions() as db:
            row=db.get(DeliveryAttachment,response.json()['id'])
            path=attachments.storage_path(row.storage_key)
            self.assertEqual(path.parent,attachments.storage_root().resolve())
            self.assertEqual(path.stat().st_mode & 0o077,0)
            path.unlink()
        result=self.document(response.json()['id'],'download')
        self.assertEqual(result.status_code,404,result.text)
        self.assertIn('附件文件暂不可用',result.json()['detail'])
        with self.assertRaises(HTTPException): attachments.storage_path('../../keys.json')

    def test_streaming_size_limits_empty_files_invalid_types_archives_and_text_are_atomic(self):
        cases=[('a.txt',b''),('a.exe',b'MZ'),('a.pdf',b'<html>fake pdf</html>'),('a.docx',b'not a zip'),
               ('a.docx',office_file('.docx',macro=True)),('a.txt',b'\x00\x01\x02'),('a.doc',b'fake word'),('a.html',b'html')]
        for filename,content in cases:
            with self.subTest(filename=filename):
                self.assertEqual(self.upload(filename,content).status_code,400)
        with patch('app.attachments.MAX_FILE_SIZE',8):
            self.assertEqual(self.upload(content=b'x'*9).status_code,413)
            streamed=self.client.post(f'/api/deliveries/{self.item_id}/attachments?filename=large.txt',
                content=iter([b'1234',b'56789']),headers=self.headers['owner'])
            self.assertEqual(streamed.status_code,413,streamed.text)
        with patch('app.attachments.MAX_ARCHIVE_SIZE',100):
            self.assertEqual(self.upload('large.docx',office_file('.docx')).status_code,400)
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(DeliveryAttachment)))
            self.assertIsNone(db.scalar(select(AuditLog).where(AuditLog.record_kind=='business')))
        self.assertEqual(list(attachments.storage_root().iterdir()),[])

    def test_text_encodings_pdf_inline_preview_and_documents_survive_new_sessions(self):
        for encoding,extension in [('utf-8','.txt'),('utf-16','.txt'),('gb18030','.csv'),('utf-8','.md')]:
            text='交付清单\n机器,数量\n数据库,2'
            row=self.upload('清单'+extension,text.encode(encoding)).json()
            self.assertEqual(self.document(row['id'],'preview').text,text)
            self.assertEqual(self.document(row['id'],'download').content,text.encode(encoding))
        pdf=b'%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n%%EOF'
        row=self.upload('方案.pdf',pdf).json()
        result=self.document(row['id'],'preview')
        self.assertEqual(result.content,pdf)
        self.assertIn('inline;',result.headers['content-disposition'])
        self.assertEqual(result.headers['content-type'],'application/pdf')
        # New metadata/connection access does not depend on an upload's request state.
        with self.sessions() as db:
            stored=db.get(DeliveryAttachment,row['id'])
            self.assertEqual(attachments.storage_path(stored.storage_key).read_bytes(),pdf)

    def test_word_excel_preview_cache_failures_and_audit_transactions(self):
        row=self.upload('交付方案.docx',office_file('.docx')).json()
        def converted(source,destination): destination.write_bytes(b'%PDF-1.7\npreview\n%%EOF')
        with patch('app.attachments.convert_to_pdf',side_effect=converted) as convert:
            for _ in range(2):
                response=self.document(row['id'],'preview')
                self.assertEqual(response.status_code,200,response.text)
                self.assertTrue(response.content.startswith(b'%PDF-'))
            convert.assert_called_once()
        workbook=self.upload('交付清单.xlsx',office_file('.xlsx')).json()
        with patch('app.attachments.convert_to_pdf',side_effect=HTTPException(503,'预览服务暂不可用')):
            self.assertEqual(self.document(workbook['id'],'preview').status_code,503)
        self.assertEqual(self.document(workbook['id'],'download').status_code,200)
        with self.sessions() as db:
            count=len(db.scalars(select(DeliveryAttachment)).all())
            files=set(attachments.storage_root().iterdir())
        with patch('app.attachments.audit.record',side_effect=HTTPException(503,'日志事务失败')):
            self.assertEqual(self.upload().status_code,503)
        with self.sessions() as db:
            self.assertEqual(len(db.scalars(select(DeliveryAttachment)).all()),count)
        self.assertEqual(set(attachments.storage_root().iterdir()),files)

    def test_old_tables_gain_attachments_without_changing_existing_delivery(self):
        DeliveryAttachment.__table__.drop(self.engine)
        Base.metadata.create_all(self.engine); Base.metadata.create_all(self.engine)
        with self.sessions() as db:
            self.assertEqual(db.get(DeliveryProject,self.item_id).name,'项目-owner')
            self.assertEqual(db.scalars(select(DeliveryAttachment)).all(),[])

    @unittest.skipUnless(os.getenv('ATTACHMENT_REAL_OFFICE_CHECK')=='1','Enable with the supplied LibreOffice runtime')
    def test_real_word_excel_rtf_and_legacy_office_files_convert_and_download(self):
        for extension,content in [('.docx',office_file('.docx')),('.xlsx',office_file('.xlsx')),('.rtf',b'{\\rtf1\\ansi Delivery plan for preview.}')]:
            with self.subTest(extension=extension):
                response=self.upload('real'+extension,content)
                self.assertEqual(response.status_code,201,response.text)
                row=response.json()
                preview=self.document(row['id'],'preview')
                self.assertEqual(preview.status_code,200,preview.text[:300])
                self.assertTrue(preview.content.startswith(b'%PDF-'))
                self.assertGreater(len(preview.content),1000)
                self.assertEqual(self.document(row['id'],'download').content,content)
        # Generate real binary DOC/XLS from the modern fixtures, then preview them.
        binary=os.getenv('SOFFICE_BIN') or shutil.which('soffice') or shutil.which('libreoffice')
        for modern,legacy in [('.docx','doc'),('.xlsx','xls')]:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory); source=root/('legacy'+modern);source.write_bytes(office_file(modern))
                result=subprocess.run([binary,'-env:UserInstallation='+(root/'profile').as_uri(),'--headless','--convert-to',legacy,'--outdir',str(root),str(source)],capture_output=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stderr)
                data=(root/('legacy.'+legacy)).read_bytes()
                row=self.upload('旧版.'+legacy,data).json()
                preview=self.document(row['id'],'preview')
                self.assertEqual(preview.status_code,200,preview.text[:300])
                self.assertTrue(preview.content.startswith(b'%PDF-'))


class ConversionFailures(unittest.TestCase):
    def test_missing_runtime_and_timeout_are_explicit_without_publishing_partial_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'input.docx';source.write_bytes(office_file('.docx'));target=root/'result.pdf'
            with patch.dict(os.environ,{'SOFFICE_BIN':''}), patch('app.attachments.shutil.which',return_value=None):
                with self.assertRaises(HTTPException) as raised: attachments.convert_to_pdf(source,target)
                self.assertEqual(raised.exception.status_code,503)
            with patch.dict(os.environ,{'SOFFICE_BIN':'/test/soffice'}), patch('app.attachments.subprocess.run',side_effect=subprocess.TimeoutExpired('soffice',60)):
                with self.assertRaises(HTTPException) as raised: attachments.convert_to_pdf(source,target)
                self.assertEqual(raised.exception.status_code,504)
            self.assertFalse(target.exists())

    def test_conversion_uses_unique_private_profile_disabled_macros_no_shell_and_atomic_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'input.docx';source.write_bytes(office_file('.docx'));target=root/'result.pdf'
            def convert(args,**options):
                self.assertNotIn('shell',options)
                self.assertEqual(options['timeout'],60)
                profile=Path(args[1].split('file://',1)[1])
                self.assertIn('MacroSecurityLevel',(profile/'user/registrymodifications.xcu').read_text())
                output=Path(args[args.index('--outdir')+1])/'document.pdf'
                output.write_bytes(b'%PDF-1.7\nconverted\n%%EOF')
                return SimpleNamespace(returncode=0)
            with patch.dict(os.environ,{'SOFFICE_BIN':'/test/soffice'}), patch('app.attachments.subprocess.run',side_effect=convert):
                attachments.convert_to_pdf(source,target)
            self.assertTrue(target.read_bytes().startswith(b'%PDF-'))
            self.assertEqual(target.stat().st_mode & 0o077,0)


if __name__=='__main__': unittest.main()
