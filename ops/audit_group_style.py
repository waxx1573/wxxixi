"""Read-only audit of the deployed expression formatter against SQLite stock.
Only scope/count summaries are printed; no sample bodies or credentials.
"""
import ast, asyncio, json, sqlite3, sys, time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock
root=Path(sys.argv[1]).resolve()
data=Path(sys.argv[2]).resolve()
config=json.loads((data/'config/astrbot_plugin_self_learning_config.json').read_text(encoding='utf-8-sig'))
groups=[t[6:] for t in config['Target_Settings']['target_qq_list'] if t.startswith('group_')]
assert len(groups)==4
assert not config['MaiBot_Enhancement']['enable_expression_user_scope']
assert not config['Social_Context_Settings']['enable_social_context_injection']
db=sqlite3.connect((data/'plugin_data/astrbot_plugin_self_learning/messages.db').as_uri()+'?mode=ro',uri=True)
db.row_factory=sqlite3.Row
tree=ast.parse((root/'services/social/social_context_injector.py').read_text(encoding='utf-8-sig'))
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='SocialContextInjector')
fn=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_format_expression_patterns_context');fn.decorator_list=[]
ns={'Optional':Optional,'normalize_persona_scope':lambda x:str(x),'logger':Mock()}
exec(compile(ast.Module(body=[fn],type_ignores=[]),'deployed_formatter','exec'),ns)
class ReadOnlyDB:
    async def get_recent_week_expression_patterns(self, group_id, limit, hours, persona_id, user_id):
        assert group_id in groups, 'unexpected global/cross-group fallback'
        assert user_id is None, 'personal style must not be selected'
        rows=db.execute('SELECT * FROM expression_patterns WHERE group_id=? AND persona_id=? AND user_id IS NULL AND last_active_time>=? ORDER BY weight DESC LIMIT ?', (group_id,persona_id,time.time()-hours*3600,limit)).fetchall()
        self.calls.append((group_id,persona_id,len(rows)))
        return [dict(row) for row in rows]
async def run():
    adapter=ReadOnlyDB();adapter.calls=[]
    obj=SimpleNamespace(config=SimpleNamespace(expression_patterns_hours=24,enable_expression_user_scope=False), database_manager=adapter,_enable_protection=False,_get_from_cache=lambda k:None,_set_to_cache=lambda k,v:None)
    personas=[r[0] for r in db.execute('SELECT DISTINCT persona_id FROM expression_patterns')]
    count=0
    for group in groups:
        for persona in personas+['audit-nonexistent-persona']:
            start=len(adapter.calls)
            result=await ns['_format_expression_patterns_context'](obj,group,persona_id=persona,user_id=None,enable_global_fallback=False)
            assert len(adapter.calls)==start+1
            g,p,n=adapter.calls[-1]
            assert bool(result)==bool(n)
            if result:
                assert '全局所有群组' not in result
                assert ('群组 '+g+' / 人格 '+p) in result
            print(json.dumps({'group':g,'persona':p,'selected':n,'formatted':bool(result)},ensure_ascii=False))
            count+=1
    print('PASS',count,'deployed formatter + read-only inventory scope checks')
asyncio.run(run())
db.close()
