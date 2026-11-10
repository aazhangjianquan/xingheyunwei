"""Idempotent v1.0.8 data upgrade; the caller owns the startup transaction."""
import json
from sqlalchemy import select, delete, or_, and_
from .models import (SystemSetting, Project, ProjectMember, User, UserRole, DepartmentPosition,
                     WorkflowNode, WorkflowInstance, WorkflowTemplate, WorkflowVersion)
from . import project_positions as pp
from .roles import SYSTEM_ROLES, direct_role_ids, canonical_role


def definition_for_projects(definition):
    """Translate active drafts/publications without changing archived versions."""
    result = json.loads(json.dumps(definition))
    def walk(nodes):
        updated = []
        for node in nodes:
            for arm in node.get('branches', []):
                arm['steps'] = walk(arm.get('steps', []))
            source = node.get('assignee', {})
            role = canonical_role(source.get('role'))
            legacy_manager = role in {'manager','product_developer'} or node.get('id') in {'leader','manager'} and node.get('name') in {'领导','管理者','领导审批','管理者审批'}
            if legacy_manager:
                continue
            if source.get('source') in {'project_role','department_role'}:
                mapped = pp.migrated_positions(role)
                if mapped:
                    node['assignee'] = {'source':'project_role','role':mapped[0]}
            if node.get('type') == 'execution':
                node['assignee'] = {'source':'project_role','role':'operator'}
            elif node.get('id') == 'ops_head' and node.get('name') in {'运维负责人','运维负责人审批'}:
                node['assignee'] = {'source':'project_role','role':'ops_head'}
            updated.append(node)
        return updated
    result['steps'] = walk(result.get('steps', []))
    return result


def upgrade_project_positions(db, objects):
    """Keep explicit appointments and immutable history before retiring grants."""
    db.flush()
    marker = 'project_positions_v108'
    if db.get(SystemSetting, marker):
        return
    counts = {'archived':0,'members':0,'policies':0,'publications':0}
    def archive(key, value):
        db.add(SystemSetting(key=marker+'.'+key, value=json.dumps(value,ensure_ascii=False),is_secret=False))
        counts['archived'] += 1
    old = list(db.scalars(select(ProjectMember)))
    members = set()
    for m in old:
        archive(f'member.{m.project_id}.{m.user_id}.{m.role}', {'project_id':m.project_id,'user_id':m.user_id,'role':m.role})
        members.update((m.project_id,m.user_id,role) for role in pp.migrated_positions(m.role))
    # 旧全局流程已明确指定的唯一运维处理人可等价承接到项目；多人或不明确时不猜测。
    defaults={'ops_head':set(),'operator':set()}
    for template in db.scalars(select(WorkflowTemplate)):
        version=db.scalar(select(WorkflowVersion).where(WorkflowVersion.kind==template.kind).order_by(WorkflowVersion.version.desc()).limit(1))
        if not version:continue
        for node in json.loads(version.definition).get('steps',[]):
            source=node.get('assignee',{})
            role='operator' if node.get('type')=='execution' else 'ops_head' if node.get('id')=='ops_head' else None
            if role and source.get('source')=='fixed':defaults[role].update(source.get('user_ids',[]))
    for project in db.scalars(select(Project)):
        for role,candidates in defaults.items():
            if len(candidates)==1 and not any(pid==project.id and r==role for pid,uid,r in members):
                uid=next(iter(candidates));person=db.get(User,uid)
                if person and person.active:members.add((project.id,uid,role))
    # 原在途节点与已完成资源的执行联系人保留资格；原快照和配置保持原字节内容。
    for node, instance in db.execute(select(WorkflowNode,WorkflowInstance).join(WorkflowInstance).where(
            or_(and_(WorkflowInstance.status.in_(['pending','in_progress']),WorkflowNode.status.in_(['pending','waiting'])),
                and_(WorkflowInstance.object_type=='resource',WorkflowInstance.status=='completed',
                     WorkflowNode.node_type=='execution',WorkflowNode.status=='completed')))):
        if node.assignment_policy:
            continue
        config=json.loads(node.config)
        source=config.get('assignee',{})
        explicit_manager = source.get('source') == 'department_role' and source.get('role') == 'department_head' and any(
            person.get('organization_manager') for person in config.get('assignees', []))
        if source.get('source') == 'organization_manager' or explicit_manager:
            continue
        model=objects.get(instance.object_type)
        item=db.get(model,instance.object_id) if model else None
        if not item:
            continue
        owner_id=item.applicant_id if instance.object_type in {'resource','license'} else item.owner_id
        project_id=getattr(item,'project_id',None)
        role=canonical_role(source.get('role'))
        mapped=pp.migrated_positions(role)
        policy={'source':'fixed','owner_id':owner_id}
        if instance.status in {'pending','in_progress'} and project_id and source.get('source')=='project_role' and mapped:
            policy.update(source='project_role',project_id=project_id,role=mapped[0])
            # 只迁移现存项目任命，不能依据旧快照恢复管理员已撤销的成员关系。
        node.assignment_policy=json.dumps(policy,ensure_ascii=False)
        counts['policies']+=1
    db.execute(delete(ProjectMember))
    for project_id,user_id,role in sorted(members):
        db.add(ProjectMember(project_id=project_id,user_id=user_id,role=role))
    counts['members']=len(members)
    for user in db.scalars(select(User)):
        original=direct_role_ids(user)
        remaining=[r for r in original if r in SYSTEM_ROLES] or ['applicant']
        if set(original)!=set(remaining):
            archive('user.'+str(user.id),{'roles':original,'primary_role':user.role})
            user.role_grants=[UserRole(role=r) for r in remaining]
            user.role=remaining[0]
            user.organization_revision+=1
    for job in db.scalars(select(DepartmentPosition)):
        if job.role_grants:
            archive('job.'+str(job.id),{'roles':[g.role for g in job.role_grants]})
            job.role_grants=[]
            job.revision+=1
    db.flush()
    from . import flow_engine as flow
    for template in db.scalars(select(WorkflowTemplate)):
        draft=definition_for_projects(flow.department_first_definition(json.loads(template.draft)))
        if json.loads(template.draft)!=draft:
            template.draft=flow.dump(draft);template.revision+=1
        version=db.scalar(select(WorkflowVersion).where(WorkflowVersion.kind==template.kind).order_by(WorkflowVersion.version.desc()).limit(1))
        if version:
            latest=definition_for_projects(flow.department_first_definition(json.loads(version.definition)))
            if json.loads(version.definition)!=latest:
                db.add(WorkflowVersion(kind=template.kind,version=version.version+1,definition=flow.dump(latest)))
                counts['publications']+=1
    db.add(SystemSetting(key=marker,value=json.dumps(counts),is_secret=False))
    db.flush();db.expire_all()
