"""Apply bounded follow-up handling to the inspected Spectre 2.1.13 layout.
Run with the target plugin directory. Does not restart services.
"""
import ast
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

root = Path(sys.argv[1]).resolve()
helper = Path(__file__).with_name("spectre_followup.py").read_text(encoding="utf-8")
changes = {}

def replace(path, old, new):
    original = path.read_text(encoding="utf-8-sig")
    if new in original:
        changes[path] = original
        return
    if original.count(old) != 1:
        raise RuntimeError(f"Unsupported source layout: {path.name}")
    changes[path] = original.replace(old, new)

replace(root / "utils/reply_decision.py",
        '        # Smart 风格轻量语义优先：明确问题/请求不再完全依赖随机概率。',
        '        if not event.is_private_chat() and followup_window.eligible(event):\n'
        '            logger.info("Spectre followup eligible: group=%s sender=%s", event.get_group_id(), event.get_sender_id())\n'
        '            return True\n\n'
        '        # Smart 风格轻量语义优先：明确问题/请求不再完全依赖随机概率。')
p = root / "utils/reply_decision.py"
if 'from .spectre_followup import followup_window' not in changes[p]:
    changes[p] = 'from .spectre_followup import followup_window\n' + changes[p]
replace(root / "main.py",
        '                await HistoryStorage.save_bot_message_from_chain(event._result.chain, event)',
        '                if message_text.strip() and event._has_send_oper:\n'
        '                    followup_window.note_reply(event)\n'
        '                await HistoryStorage.save_bot_message_from_chain(event._result.chain, event)')
p = root / "main.py"
if 'from .utils.spectre_followup import followup_window' not in changes[p]:
    changes[p] = 'from .utils.spectre_followup import followup_window\n' + changes[p]
replace(root / "utils/llm_utils.py",
        '        # 将环境描述追加到 system_prompt',
        '        env_description += "\\n接话规则：先核对最近记录里的发言人和最后一轮机器人回复。若当前成员正在回答你刚才的问题、补充地点/条件或纠正你的理解，直接承接并简短确认，不要求再次点名。只用本群事实；不得把别人的话当成当前成员的经历。成员彼此聊天且与你无关时保持安静。对于记住类请求，先承接当前对话，未实际写入记忆不得声称永久记住。天气预报、价格、库存等实时事实必须依据可用工具的本次查询结果，说明地点和日期；没有查询依据时明确尚未查到，不编造精确预报或声称已查询。历史Bot回复不是真实数据源，收到纠正先承认并核对，不重复断言、不反问责怪用户。"\n\n'
        '        # 将环境描述追加到 system_prompt')
changes[root / "utils/spectre_followup.py"] = helper
for path, text in changes.items():
    compile(text, str(path), "exec")

# Exercise the actual patched decision class without AstrBot/model/network side effects.
ns = {}
exec(compile(helper, "spectre_followup.py", "exec"), ns)
clock = [100.0]
window = ns['FollowupWindow'](clock=lambda: clock[0])
class Event:
    unified_msg_origin = 'test:group:one'
    group = 'one'
    sender = 'alice'
    text = '记住以后默认成都'
    def get_group_id(self): return self.group
    def get_sender_id(self): return self.sender
    def get_platform_name(self): return 'test'
    def is_private_chat(self): return False
    def get_message_outline(self): return self.text
class Logger:
    def __getattr__(self, key): return lambda *a, **k: None
busy = [False]
env = dict(followup_window=window, AstrMessageEvent=object, AstrBotConfig=dict,
           Context=object, time=__import__('time'), random=SimpleNamespace(random=lambda: 0.99),
           logger=Logger(), LLMUtils=SimpleNamespace(is_llm_in_progress=lambda *a: busy[0]))
tree = ast.parse(changes[root / 'utils/reply_decision.py'])
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ReplyDecision')
exec(compile(ast.Module(body=[cls], type_ignores=[]), 'decision.py', 'exec'), env)
decide = env['ReplyDecision'].should_reply
cfg = {'enabled_groups':['one','two'], 'model_frequency':{'keywords':['皮皮'], 'blacklist_keywords':['/'], 'probability':{'probability':0}}}
e = Event()
assert not decide(e,cfg), 'unsolicited plain text must not bypass probability'
window.note_reply(e)
assert decide(e,cfg), 'reply to previous question must bypass probability'
e.text='成都'; assert decide(e,cfg)
e.sender='bob'; assert not decide(e,cfg)
e.sender='alice';e.group='two';assert not decide(e,cfg)
e.group='one';e.unified_msg_origin='other:group:one';assert not decide(e,cfg)
e.unified_msg_origin='test:group:one';e.text='/reset';assert not decide(e,cfg)
e.text='成都';cfg['blocked_groups']=['one'];assert not decide(e,cfg)
cfg['blocked_groups']=[];cfg['_temp_mute']={'until':__import__('time').time()+100};assert not decide(e,cfg)
cfg.pop('_temp_mute');busy[0]=True;assert not decide(e,cfg)
busy[0]=False;clock[0]=401;assert not decide(e,cfg)
e.text='皮皮';assert decide(e,cfg)
assert not ns['FollowupWindow']().eligible(e), 'restart must not revive old windows'
assert ast.parse(changes[root/'main.py'])
print('PASS: followup acceptance, short answer, member/group/origin isolation, command, blocked group, mute, busy, expiration, keyword, restart')
if '--check' not in sys.argv:
    for path,text in changes.items():
        path.write_text(text,encoding='utf-8')
    print('APPLIED: 3 Spectre files + helper; core/config/data unchanged')
