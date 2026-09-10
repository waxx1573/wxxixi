"""Isolated adapter contract checks; no credentials, real chats or model calls."""
import os,sys,asyncio,tempfile,json,types
os.environ['MEM0_TELEMETRY']='false'
sys.path.insert(0,'/AstrBot/data/plugins')
from astrbot_plugin_self_learning.services.integration.mem0_memory_manager import Mem0MemoryManager
class Emb:
 def get_dim(self):return 8
 async def get_embedding(self,text):return [1.,0.,0.,0.,0.,0.,0.,0.]
class LLM:
 def __init__(self):self.calls=0
 async def filter_chat_completion(self,**kw):
  self.calls+=1
  if self.calls%2:return json.dumps({'facts':['Synthetic preference: blue']})
  return json.dumps({'memory':[{'id':'0','text':'Synthetic preference: blue','event':'ADD'}]})
async def main():
 with tempfile.TemporaryDirectory(prefix='pipi-memory-test-') as td:
  llm=LLM();m=Mem0MemoryManager(types.SimpleNamespace(data_dir=td),llm,Emb())
  assert await m.start()
  await m.add_memory_from_message(types.SimpleNamespace(message='Synthetic preference: blue',sender_id='tester',sender_name='Test'),'synthetic-a')
  assert llm.calls>=2
  assert await m.get_related_memories('preference','synthetic-a')
  assert not await m.get_related_memories('preference','synthetic-b')
  raw=m._memory
  raw.vector_store.client.close()
  raw.db.connection.close()
  await m.stop()
  m=Mem0MemoryManager(types.SimpleNamespace(data_dir=td),LLM(),Emb())
  assert await m.start()
  assert await m.get_related_memories('preference','synthetic-a')
  assert not await m.get_related_memories('preference','synthetic-b')
  entries=m._memory.get_all(agent_id='synthetic-a')['results']
  for entry in entries:m._memory.delete(entry['id'])
  assert not await m.get_related_memories('preference','synthetic-a')
  m._memory.vector_store.client.close();m._memory.db.connection.close();await m.stop()
  print('PASS extraction/write/recall/group-isolation/reopen/delete; synthetic temporary data only')
asyncio.run(main())
