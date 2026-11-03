const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'console','ui.html'),'utf8')
  .match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace('tick(); setInterval(() => tick(), 2500);','');

function render(health, stale=false) {
  const context = vm.createContext({
    document:{querySelector(){return null;},addEventListener(){}},
    window:{addEventListener(){}}, location:{hash:''}, setInterval(){},
    sample:{fetched_at:Date.now()/1000,stale,probe_fresh:true,zones:[{
      region:'mumbai',pc_state:'online',components:[{name:'rabbit-1',state:'running',health,
        probe_note:'Notifications queue not created yet <test>',in:0,out:0}]}]},
  });
  vm.runInContext(source, context);
  return vm.runInContext("S={regions:['mumbai'],job:{running:false}}; topologyMap(sample)",context);
}
const healthy = render('healthy');
assert.match(healthy,/Broker OK/);
assert.match(healthy,/Notifications queue not created yet &lt;test&gt;/);
assert.doesNotMatch(healthy,/Probe failed/);
const unknown = render('unknown');
assert.match(unknown,/Probe unknown/);
assert.doesNotMatch(unknown,/Probe failed/);
const stale = render('healthy',true);
assert.doesNotMatch(stale,/Notifications queue not created yet/);
assert.doesNotMatch(stale,/Broker OK/);
console.log('PASS: broker/queue distinction, missing probe data, and stale-note suppression.');
