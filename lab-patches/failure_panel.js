// Measurements are observations, not promises of an election deadline.
function failurePanel(rows) {
  const now = Date.now()/1000;
  return `<section class="card"><h2>Abrupt failure and recovery timing</h2>
    <p>Crash kills the real process without a shutdown grace period. Injected failures stay down until Restore.
    PostgreSQL crash recovery waits for its leader lease (20 s configured; supported minimum), then promotion and reconnection. This is separate from successful order acknowledgement. New requests retry at the client; Kafka receives committed outbox messages afterward.</p>
    <table><thead><tr><th>Failed component</th><th>Crash request → confirmed / held</th><th>Recovery observed after crash</th><th>Replacement / status</th></tr></thead>
    <tbody>${rows.map(r=>`<tr><td><b>${esc(r.target)}</b><br>${fmtT(r.requested_at)}</td>
      <td>${r.signal_ms==null?'Not confirmed':Number(r.signal_ms).toFixed(0)+' ms'}</td>
      <td>${r.elapsed_s!=null?Number(r.elapsed_s).toFixed(1)+' s':r.status==='waiting'?(Math.max(0,now-r.killed_at)).toFixed(1)+' s · waiting':esc(r.status)}</td>
      <td>${esc(r.replacement||r.note||r.status)}</td></tr>`).join('')||'<tr><td colspan="4">No abrupt failure measured since the controller started.</td></tr>'}</tbody></table></section>`;
}
const previousRenderEvents = renderEvents;
const previousRenderActions = renderActions;
renderActions = function() {
  previousRenderActions();
  if (S?.layout !== 'swarm') return;
  document.querySelectorAll('[data-a="pause"],[data-a="cut"],[data-a="unpause"]').forEach(b=>b.remove());
  const restore = document.querySelector('[data-a="start"]');
  if (restore) restore.textContent='Restore';
  const unsupported = document.querySelector('[data-try="partition"]');
  if (unsupported) { unsupported.disabled=true; unsupported.title='Network-partition drill is separate from abrupt process failure'; }
};
renderEvents = function() {
  previousRenderEvents();
  let panel = document.getElementById('fault-timing');
  if (!panel) {
    panel=document.createElement('div'); panel.id='fault-timing';
    document.getElementById('tiles').before(panel);
  }
  panel.innerHTML=failurePanel(S.failure_drills||[]);
  if (S.layout==='swarm') {
    document.querySelectorAll('[data-a="pause"],[data-a="cut"],[data-site$=":cut"]').forEach(b=>b.remove());
  }
};
