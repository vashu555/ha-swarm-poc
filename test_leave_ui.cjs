// Read-only UI regression tests. Run: node test_leave_ui.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, 'console', 'ui.html'), 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace('tick(); setInterval(() => tick(), 2500);', '');

async function render({running = true, name = 'Leave the swarm', error = 'manager control socket unavailable', state = 'active'} = {}) {
  const elements = Object.fromEntries(['#host', '#rolebadge', '#tabs', '#app', '#flowmap'].map(id =>
    [id, {html: '', firstChild: null, replaceChildren() { this.firstChild = null; }}]));
  const reads = [];
  const status = {
    hostname: 'test-pc', regions: ['mumbai', 'hyderabad', 'delhi'],
    swarm: {state, region: state === 'inactive' ? '' : 'hyderabad', is_main: false, membership_error: error},
    netbird: {connected: true, ip: '192.0.2.2', peers: []},
    job: {name, running, ok: !error, log: ['Demoting this manager before leaving…'], reject: null},
  };
  const context = vm.createContext({
    document: {querySelector: id => elements[id] || null, addEventListener() {}, activeElement: null},
    window: {addEventListener() {}}, location: {hash: '#/zone'},
    setInterval() {}, setTimeout, clearTimeout, AbortController,
    fetch: async url => { reads.push(url); assert.equal(url, '/api/state'); return {ok: true, json: async () => status}; },
  });
  vm.runInContext(source, context);
  vm.runInContext("renderContent = (element, content) => { element.html = content; }; viewChoose = () => '<p>Choose a role</p>' + jobBox();", context);
  await vm.runInContext('refreshView()', context);
  return {app: elements['#app'].html, title: context.document.title, reads};
}

(async () => {
  for (const error of ['manager control socket unavailable', null]) {
    const result = await render({error});
    assert.match(result.app, /Leaving the swarm/);
    assert.doesNotMatch(result.app, /Manager connection unavailable/);
    assert.match(result.app, /Demoting this manager/);
    assert.equal(result.title, 'Leaving the swarm · HA Swarm');
    assert.deepEqual(result.reads, ['/api/state']);
  }
  const failed = await render({running: false});
  assert.match(failed.app, /Manager connection unavailable/);
  assert.match(failed.app, /Leave the swarm: failed/);
  assert.doesNotMatch(failed.app, /Waiting for Docker to confirm completion/);
  const unrelated = await render({name: 'Become hyderabad'});
  assert.match(unrelated.app, /Manager connection unavailable/);
  const complete = await render({running: false, error: null, state: 'inactive'});
  assert.match(complete.app, /Choose a role/);
  assert.match(complete.app, /Leave the swarm: done/);
  assert.doesNotMatch(complete.app, /Waiting for Docker to confirm completion/);
  console.log('PASS: 5 leave UI scenarios; no live Docker or network operations.');
})().catch(error => { console.error(error); process.exitCode = 1; });
