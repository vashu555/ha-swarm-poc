def redis_state() -> dict:
    out = {"master": None, "reported_master": None, "nodes": {}, "sentinels": {}, "sentinel_details": {}}
    def node(h):
        try:
            client = redis.Redis(host=h, socket_timeout=0.4, socket_connect_timeout=0.4)
            info = client.info("replication")
            required = int(client.config_get("min-replicas-to-write").get("min-replicas-to-write", 0))
            good = int(info.get("min_slaves_good_slaves", info.get("connected_slaves", 0)))
            return {"up": True, "role": info.get("role"), "replicas": info.get("connected_slaves", 0),
                    "link": info.get("master_link_status"), "required_replicas": required,
                    "good_replicas": good, "write_ready": info.get("role") == "master" and good >= required}
        except Exception as exc:
            return {"up": False, "error": type(exc).__name__}
    def sentinel(h):
        try:
            client = redis.Redis(host=h, port=26379, socket_timeout=0.4, socket_connect_timeout=0.4)
            info = client.info("sentinel")
            try:
                client.execute_command("SENTINEL", "CKQUORUM", "lab")
                quorum = True
            except redis.exceptions.RedisError:
                quorum = False
            return {"up": True, "tilt": bool(info.get("sentinel_tilt", 0)), "quorum_ok": quorum}
        except Exception as exc:
            return {"up": False, "error": type(exc).__name__}
    # Health checks for missing members run concurrently so a failure cannot stall the whole map.
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        tasks = {h: pool.submit(node if h.startswith("redis-") else sentinel, h)
                 for h in ("redis-1", "redis-2", "redis-3", "sentinel-1", "sentinel-2", "sentinel-3")}
        for h, future in tasks.items():
            value = future.result()
            if h.startswith("redis-"):
                out["nodes"][h] = value
            else:
                out["sentinel_details"][h] = value
                out["sentinels"][h] = value["up"]
    try:
        out["reported_master"] = _SENT.discover_master("lab")[0]
    except Exception:
        pass
    candidate = out["reported_master"]
    current = out["nodes"].get(candidate, {})
    # A cached Sentinel address is not proof that a primary exists or accepts writes.
    if current.get("up") and current.get("role") == "master":
        out["master"] = candidate
    out["write_ready"] = bool(out["master"] and current.get("write_ready"))
    out["healthy_sentinels"] = sum(v.get("up", False) and not v.get("tilt", False) and v.get("quorum_ok", False)
                                   for v in out["sentinel_details"].values())
    out["failover_ready"] = out["healthy_sentinels"] >= 2
    out["api_policy"] = "signed_tokens_best_effort_redis"
    return out
