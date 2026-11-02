// Redis process state and Redis application health are distinct observations.
function redisHealth(n,c){
  if(!['redis','sentinel'].includes(c.kind)||c.state!=='running')return null;
  const rd=S.status.redis||{};
  if(c.kind==='sentinel'){
    const d=(rd.sentinel_details||{})[n];
    if(!d)return {state:'unhealthy',role:'UNVERIFIED',detail:'No current Sentinel health result'};
    if(!d.up)return {state:'unhealthy',role:'NOT RESPONDING',detail:`Container running · ${d.error||'probe failed'}`};
    if(d.tilt)return {state:'degraded',role:'TILT · NO FAILOVER',detail:'Protective mode; cannot promote a replica'};
    if(!d.quorum_ok)return {state:'degraded',role:'NO QUORUM',detail:'Responding; failover majority unavailable'};
    return {state:'',role:'WATCHING',detail:'Responding · failover quorum available'};
  }
  const r=(rd.nodes||{})[n];
  if(!r||!r.up)return {state:'unhealthy',role:'NOT RESPONDING',detail:`Container running · ${r?.error||'probe failed'}`};
  if(r.role==='master'){
    if(rd.master!==n)return {state:'degraded',role:'UNCONFIRMED PRIMARY',detail:'Redis reports primary; Sentinel has not confirmed it'};
    if(!r.write_ready)return {state:'degraded',role:'MASTER · WRITES BLOCKED',detail:`${r.good_replicas||0}/${r.required_replicas||0} required replicas available`};
    return {state:'',role:'MASTER',detail:`${r.replicas} replicas · writes ready`};
  }
  if(r.link!=='up')return {state:'degraded',role:'REPLICA · LINK DOWN',detail:'Responding; disconnected from its primary'};
  return {state:'',role:'REPLICA',detail:'Replication link up'};
}
function renderRedisHealth(){
  let box=$('redis-health');
  if(!box){box=document.createElement('div');box.id='redis-health';$('tiles').before(box)}
  const rd=S.status.redis||{}, votes=rd.healthy_sentinels??0;
  const ok=!!rd.write_ready, protectedFailover=!!rd.failover_ready;
  box.className='redis-health '+(ok?(protectedFailover?'healthy':'degraded'):'unhealthy');
  const status=ok?`Redis primary ${esc(rd.master)} · writes ready · ${votes}/3 Sentinel votes ready`:
    'Redis extras unavailable · no verified writable primary';
  const api=ok?'Signed session tokens keep working; Redis login tracking and rate limits are available.':
    'Requests can continue: APIs verify signed session tokens locally. Redis login tracking and per-account rate limits are bypassed on Redis errors.';
  box.innerHTML=`<b>${status}</b><div>${api}${ok&&!protectedFailover?' Automatic Redis failover is currently unavailable.':''}</div>`;
}
