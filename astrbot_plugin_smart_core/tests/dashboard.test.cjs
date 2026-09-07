const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const html = fs.readFileSync(path.join(__dirname, '../pages/dashboard/index.html'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../pages/dashboard/smart-core.css'), 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];
const tick = () => new Promise(resolve => setImmediate(resolve));

function page(options = {}) {
  const elements = new Map();
  for (const match of html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)) {
    elements.set(match[1], {
      type: /type="([^"]+)"/.exec(match[0])?.[1] || 'text',
      value: '', checked: false, disabled: /\bdisabled\b/.test(match[0]),
      textContent: '', innerHTML: '', classList: { toggle() {} },
      addEventListener() {},
    });
  }
  const calls = [];
  const data = { enabled: true, groups: [], cooldown_seconds: 3, reply_probability: 1,
    model_roles: { main: 'main-provider', fallback: 'backup-provider' } };
  const bridge = {
    async ready() { if (options.ready) await options.ready; calls.push('ready'); },
    async apiGet(endpoint) {
      calls.push(['GET', endpoint]);
      if (options.loadError) throw new Error('load failed');
      return options.invalidData ? {} : data;
    },
    async apiPost(endpoint, body) {
      calls.push(['POST', endpoint, body]);
      if (options.post) return options.post(body);
      return { ok: true, groups: body.groups };
    },
  };
  const context = vm.createContext({
    window: options.noBridge ? {} : { AstrBotPluginPage: bridge },
    document: { getElementById: id => elements.get(id), querySelectorAll: () => [] },
    fetch() { throw new Error('Direct fetch is forbidden in the plugin iframe'); },
  });
  vm.runInContext(script, context);
  return { calls, elements, save: () => vm.runInContext('save()', context) };
}

test('SDK loads before inline code; save begins disabled', () => {
  assert.ok(html.indexOf('src="/api/plugin/page/bridge-sdk.js"') < html.indexOf('<script>'));
  assert.match(html, /id="save"[^>]*disabled/);
  assert.match(html, /href="\.\/smart-core\.css"/);
  assert.match(css, /grid-template-columns:\s*156px minmax\(0, 1fr\)/);
});

test('navigation and dashboard summary use existing elements', async () => {
  assert.doesNotMatch(script, /getElementById\(['"]title['"]\)/);
  const p = page();
  await tick();
  assert.equal(p.elements.get('core_status').textContent, '运行中');
  assert.equal(p.elements.get('group_count').textContent, '0');
  assert.equal(p.elements.get('provider_count').textContent, '2');
  assert.equal(p.elements.get('groups_status').textContent, '0 个群');
});

test('waits for bridge context before loading or permitting saves', async () => {
  let ready;
  const p = page({ ready: new Promise(resolve => { ready = resolve; }) });
  await p.save();
  assert.equal(p.calls.length, 0);
  assert.equal(p.elements.get('save').disabled, true);
  ready();
  await tick();
  assert.deepEqual(p.calls, ['ready', ['GET', 'page/config'], ['GET', 'page/groups']]);
  assert.equal(p.elements.get('save').disabled, false);
});

test('loaded values are sent through the plugin bridge', async () => {
  const p = page();
  await tick();
  await p.save();
  const post = p.calls.find(call => call[0] === 'POST');
  assert.equal(post[1], 'page/config');
  assert.equal(post[2].enabled, true);
  assert.equal(post[2].cooldown_seconds, 3);
  assert.equal(post[2].model_roles.main, 'main-provider');
  assert.match(p.elements.get('msg').textContent, /已保存/);
});

for (const option of ['noBridge', 'loadError', 'invalidData']) {
  test(`${option} cannot save an empty form over server configuration`, async () => {
    const p = page({ [option]: true });
    await tick();
    await p.save();
    assert.equal(p.elements.get('save').disabled, true);
    assert.equal(p.calls.some(call => call[0] === 'POST'), false);
    assert.ok(p.elements.get('msg').textContent);
  });
}

test('pending save prevents duplicate submission and resets after failure', async () => {
  let reject;
  const p = page({ post: () => new Promise((resolve, fail) => { reject = fail; }) });
  await tick();
  const save = p.save();
  await p.save();
  assert.equal(p.calls.filter(call => call[0] === 'POST').length, 1);
  reject(new Error('backend unavailable'));
  await assert.rejects(save, /backend unavailable/);
  assert.equal(p.elements.get('save').disabled, false);
});

test('backend rejection does not display a success message', async () => {
  const p = page({ post: async () => ({ ok: false, error: 'group update failed' }) });
  await tick();
  await assert.rejects(p.save(), /group update failed/);
  assert.doesNotMatch(p.elements.get('msg').textContent, /已保存/);
  assert.equal(p.elements.get('save').disabled, false);
});
