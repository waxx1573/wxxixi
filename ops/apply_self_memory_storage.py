"""Persist Mem0 history within plugin data and disable telemetry; source-layout guarded."""
import sys
from pathlib import Path
p=Path(sys.argv[1])/'services/integration/mem0_memory_manager.py'
s=p.read_text(encoding='utf-8-sig')
if 'os.environ["MEM0_TELEMETRY"] = "false"' not in s:
 assert 'import os\n' in s
 s=s.replace('import os\n','import os\nos.environ["MEM0_TELEMETRY"] = "false"\n',1)
old='config: Dict[str, Any] = {"version": "v1.1"}'
new='config: Dict[str, Any] = {"version": "v1.1", "history_db_path": os.path.join(self._config.data_dir, "mem0_history.db")}'
if new not in s:
 assert s.count(old)==1
 s=s.replace(old,new)
compile(s,str(p),'exec');p.write_text(s,encoding='utf-8')
print('Applied telemetry/history location guard')
