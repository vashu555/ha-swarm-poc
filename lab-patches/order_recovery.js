function databaseRecoveryMessage(st){
  const running=['pg-1','pg-2','pg-3'].filter(n=>st.containers?.[n]?.state==='running').length;
  return `<b>DATABASE FAILOVER: no writable PostgreSQL primary verified.</b> ${running}/3 database containers running. Automatic crash recovery can wait for the configured 20 s leader lease, followed by promotion and reconnection. Keep unacknowledged requests pending and retry with the same client_ref; they are not queued directly in Kafka. Committed orders and outbox records remain in PostgreSQL/WAL; the relay publishes to Kafka afterward. A timeout can hide a committed order, so do not submit a new reference. Restore is needed if no eligible replica remains.`;
}
