"""Trusted, one-operation desktop approval snapshots. No window content is read here."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def prepare_desktop_scope(step, workspace_root):
    from harness_tools.desktop_native import Native, window_id
    from harness_tools.file_perception import FilePolicy, _path
    tool=str(step.tool)
    if not tool.startswith('desktop_observe:'):raise ValueError('desktop_instruction_required')
    operation=tool.split(':',1)[1]
    fields={'list_windows':{'max_windows','timeout','title_contains','process_id','window_class'},'inspect_window':{'window_id','max_controls','max_chars','timeout'},'screenshot':{'window_id','output','timeout'}}
    inputs=deepcopy(step.inputs or {})
    if operation not in fields or type(inputs) is not dict or set(inputs)-fields[operation]:raise ValueError('invalid_desktop_inputs')
    for key,upper in [('max_windows',100),('max_controls',200),('max_chars',12000),('timeout',30)]:
        if key in inputs and (type(inputs[key]) is not int or not 1<=inputs[key]<=upper):raise ValueError('invalid_desktop_limits')
    for key in ('title_contains','window_class'):
        if key in inputs and (type(inputs[key]) is not str or not 1<=len(inputs[key])<=120 or any(ord(c)<32 for c in inputs[key])):raise ValueError('invalid_window_filter')
    if 'process_id' in inputs and (type(inputs['process_id']) is not int or not 1<=inputs['process_id']<=4294967295):raise ValueError('invalid_window_filter')
    scope={'operation':operation,'workspace_root':str(Path(workspace_root).resolve()),'inputs':inputs,'identity':None,'output':None}
    if operation!='list_windows':scope['identity']=Native().identity(window_id(inputs.get('window_id')))
    if operation=='screenshot':
        scope['output']=str(_path(FilePolicy(workspace_root,lambda p:False,lambda p:True),inputs.get('output'),True))
    scope['confirmation']=hashlib.sha256(json.dumps(scope,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    scope['summary']={
        'list_windows':'仅本次：读取所有可见顶层窗口的标题、进程编号和位置；不授权控件或截图。',
        'inspect_window':'仅本次：读取指定窗口的可见控件；跳过密码及编辑/文档控件内容，不点击、不输入。',
        'screenshot':'仅本次：截取指定窗口并保存新 PNG。截图不自动脱敏，可能包含私人文字。',
    }[operation]
    return scope


def issue_approved_desktop_grant(manager,plan_id,step,scope,*,approved,workspace_root=None):
    from authorization import GrantLimits
    if approved is not True or not scope:raise ValueError('desktop_approval_required')
    root=workspace_root or manager.workspace_root
    fresh=prepare_desktop_scope(step,root)
    if fresh!=scope:raise ValueError('desktop_scope_changed_reapproval_required')
    grant=manager.issue_for_step(plan_id,step,workspace_root=root,subject='desktop-approved-runtime',
        limits=GrantLimits(max_tool_calls=1,max_model_calls=0,max_elapsed_seconds=45))
    try:
        resources=[]
        if scope['operation']=='list_windows':resources.append('desktop:enumerate')
        else:
            identity=scope['identity'];suffix=identity['window_id']+':'+str(identity['pid'])+':'+identity['process_created']
            kind='inspect' if scope['operation']=='inspect_window' else 'capture'
            resources.append('desktop:'+kind+':'+suffix)
        if scope['output']:resources.append(scope['output'])
        grant.allowed_resources=tuple(resources)
        return grant
    except Exception:
        manager.revoke(grant.grant_id)
        raise


DESKTOP_ACCESS_CONFIG = Path(__file__).resolve().parents[2] / 'config' / 'desktop_access.json'


def desktop_auto_allowed():
    """Operator-owned persistent consent; re-read each step. Missing/bad config fails closed."""
    try:
        path=DESKTOP_ACCESS_CONFIG
        if path.is_symlink() or path.parent.is_symlink():return False
        info=path.stat()
        if getattr(info,'st_file_attributes',0) & 0x400 or info.st_size>4096:return False
        config=json.loads(path.read_text(encoding='utf-8'))
        return (type(config) is dict and config.get('version')==1
                and config.get('mode')=='auto-readonly'
                and config.get('consent')=='persistent_read_only_desktop')
    except (OSError,ValueError,TypeError):return False
