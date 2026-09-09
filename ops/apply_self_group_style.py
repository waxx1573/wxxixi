"""Enable isolated expression injection while social injection is off; no DB writes."""
import ast, asyncio, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
root=Path(sys.argv[1]).resolve()
p=root/'services/hooks/llm_hook_handler.py'
s=p.read_text(encoding='utf-8-sig')
old='''        if not social_enabled:
            logger.debug("[LLM Hook] 社交关系上下文注入已关闭，跳过社交上下文")
            return None
'''
new='''        if not social_enabled:
            # Style is independent of social features. Never borrow another group's patterns.
            style_enabled = bool(getattr(self._config, "enable_style_learning", False))
            expressions_enabled = bool(getattr(self._config, "enable_expression_patterns", False))
            targets = getattr(self._config, "target_qq_list", []) or []
            groups = {str(t)[6:] for t in targets if str(t).startswith("group_")}
            if (not style_enabled or not expressions_enabled or not self._social_context_injector
                    or str(group_id) not in groups):
                return None
            try:
                result = await self._social_context_injector._format_expression_patterns_context(
                    group_id, persona_id=persona_id, user_id=None,
                    enable_protection=True, enable_global_fallback=False,
                )
                if result:
                    logger.info("[LLM Hook] isolated group style injected: group=%s persona=%s", group_id, persona_id)
                return result
            except Exception as exc:
                logger.warning("[LLM Hook] group style unavailable: %s", type(exc).__name__)
                return None
'''
if new in s: candidate=s
else:
    assert s.count(old)==1, 'Unexpected hook layout'
    candidate=s.replace(old,new)
compile(candidate,str(p),'exec')
cls=next(n for n in ast.parse(candidate).body if isinstance(n,ast.ClassDef) and n.name=='LLMHookHandler')
fn=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_fetch_social');fn.decorator_list=[]
from typing import Optional
ns={'Optional':Optional,'logger':Mock()}
exec(compile(ast.Module(body=[fn],type_ignores=[]),'hook','exec'),ns)
async def test():
    config=SimpleNamespace(enable_social_context_injection=False, enable_style_learning=True,enable_expression_patterns=True,target_qq_list=['group_one','group_two'])
    formatter=AsyncMock(return_value='group style')
    obj=SimpleNamespace(_config=config,_social_context_injector=SimpleNamespace(_format_expression_patterns_context=formatter))
    call=ns['_fetch_social']
    assert await call(obj,'one','alice','pipi')=='group style'
    formatter.assert_awaited_once_with('one',persona_id='pipi',user_id=None,enable_protection=True,enable_global_fallback=False)
    formatter.reset_mock(); assert await call(obj,'outside','alice','pipi') is None;formatter.assert_not_awaited()
    config.enable_style_learning=False;assert await call(obj,'one','alice','pipi') is None;formatter.assert_not_awaited()
    config.enable_style_learning=True;config.enable_expression_patterns=False;assert await call(obj,'one','alice','pipi') is None
    config.enable_expression_patterns=True;formatter.return_value=None;assert await call(obj,'two','bob','pipi') is None
    formatter.side_effect=RuntimeError('unavailable');assert await call(obj,'one','alice','pipi') is None
    print('PASS 6: isolated style, no global fallback/personal scope, outside blocked, switches, empty, failure')
asyncio.run(test())
if '--check' not in sys.argv:
    p.write_text(candidate,encoding='utf-8')
    print('Applied isolated group style hook; no social features or review bypass')
